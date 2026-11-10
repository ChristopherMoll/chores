"""Turning "now" into the period a task belongs to, and working out when it's due.

Every recurring task resets at the start of its period, in the household's
time zone: daily at midnight, weekly on the configured week-start day,
monthly on the 1st. "Once" tasks are done one time. "Other" tasks never
reset; each log is a dated event.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

FREQUENCIES = ("daily", "weekly", "monthly", "once", "other", "selected", "sequence")
RECURRING = ("daily", "weekly", "monthly", "selected")
FREQ_LABELS = {"daily": "Today", "weekly": "This week", "monthly": "This month",
               "once": "One-off", "other": "As needed", "selected": "Selected days", "sequence": "Sequence step"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Average occurrences per month, used to turn monthly hours into per-occurrence effort.
OCCURRENCES_PER_MONTH = {"daily": 30.44, "weekly": 4.35, "monthly": 1.0, "once": 1.0, "other": 1.0, "sequence": 1.0}
ONCE_KEY = "ONCE"
END_OF_DAY = time(23, 59)


def tz():
    return ZoneInfo(current_app.config["TIMEZONE"])


def now_local():
    return datetime.now(tz())


def today():
    if current_app.config.get("NOW_OVERRIDE"):
        return now().date()
    override = current_app.config.get("TODAY_OVERRIDE")   # tests only
    return date.fromisoformat(override) if override else now_local().date()


def now():
    """Local wall-clock time as a naive datetime. Noon on the overridden day in tests."""
    if current_app.config.get("NOW_OVERRIDE"):
        value = datetime.fromisoformat(current_app.config["NOW_OVERRIDE"])
        return value.astimezone(tz()).replace(tzinfo=None) if value.tzinfo else value
    if current_app.config.get("TODAY_OVERRIDE"):
        return datetime.combine(today(), time(12))
    return now_local().replace(tzinfo=None)


def week_start(d):
    offset = (d.weekday() - current_app.config["WEEK_START"]) % 7
    return d - timedelta(days=offset)


def month_start(d):
    return d.replace(day=1)


def next_month(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def period_key(freq, d=None):
    d = d or today()
    if freq == "daily":
        return f"D{d.isoformat()}"
    if freq == "weekly":
        return f"W{week_start(d).isoformat()}"
    if freq == "monthly":
        return f"M{d:%Y-%m}"
    if freq == "once":
        return ONCE_KEY
    if freq == "selected":
        return f"S{d.isoformat()}"
    return f"O{d.isoformat()}"


def period_start(freq, d):
    """First day of the task's current period, or None if it has no period."""
    return {"daily": d, "weekly": week_start(d), "monthly": month_start(d)}.get(freq)


def range_for(name, d=None):
    """Inclusive start, exclusive end for 'day' | 'week' | 'month'."""
    d = d or today()
    if name == "day":
        return d, d + timedelta(days=1)
    if name == "week":
        s = week_start(d)
        return s, s + timedelta(days=7)
    s = month_start(d)
    return s, next_month(s)


def days_left(freq, d=None):
    d = d or today()
    if freq == "weekly":
        return (week_start(d) + timedelta(days=7) - d).days
    if freq == "monthly":
        return (next_month(d) - d).days
    return 1


# ------------------------------------------------------------------- due ---

def due_date(task, d):
    """The date this task's current occurrence is due, or None if it has none.

    Weekly and monthly tasks with no due day set are due on the last day of
    their period. "Other" tasks, and one-offs without a date, have no due date.
    """
    freq = task["frequency"]
    if freq == "selected":
        days = {int(v) for v in task["selected_days"].split(",") if v}
        return next((d + timedelta(days=n) for n in range(7) if (d + timedelta(days=n)).weekday() in days), None)
    if freq == "daily":
        return d
    if freq == "weekly":
        start = week_start(d)
        if task["due_weekday"] is None:
            return start + timedelta(days=6)
        return start + timedelta(days=(task["due_weekday"] - current_app.config["WEEK_START"]) % 7)
    if freq == "monthly":
        last = (next_month(d) - timedelta(days=1)).day
        return d.replace(day=min(task["due_monthday"] or last, last))
    if freq == "once" and task["due_date"]:
        return date.fromisoformat(task["due_date"])
    return None


def due_at(task, d):
    """Due date as a datetime. Only daily tasks have a time; others run to the end of the day."""
    day = due_date(task, d)
    if day is None:
        return None
    if task["frequency"] in ("daily", "selected") and task["due_time"]:
        return datetime.combine(day, time.fromisoformat(task["due_time"]))
    return datetime.combine(day, END_OF_DAY)


def format_time(value):
    """'20:30' or a time -> '8:30 pm'. strftime's no-padding flags aren't portable."""
    t = time.fromisoformat(value) if isinstance(value, str) else value
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def has_explicit_due(task):
    freq = task["frequency"]
    return bool((freq == "selected" and task["selected_days"])
                or (freq == "daily" and task["due_time"])
                or (freq == "weekly" and task["due_weekday"] is not None)
                or (freq == "monthly" and task["due_monthday"])
                or (freq == "once" and task["due_date"]))


def due_info(task, now_, explicit_only=True):
    """{date, label, overdue} for showing on a task, or None if there's nothing to show."""
    if explicit_only and not has_explicit_due(task):
        return None
    d = now_.date()
    day = due_date(task, d)
    if day is None:
        return None
    if task["frequency"] == "daily":
        if not task["due_time"]:
            return {"date": day, "label": "Due today", "overdue": False}
        overdue = now_ > due_at(task, d)
        return {"date": day, "label": f"{'Was due' if overdue else 'By'} {format_time(task['due_time'])}",
                "overdue": overdue}
    days = (day - d).days
    pretty = f"{day:%b} {day.day}"
    if days < 0:
        label = f"Overdue · {pretty}"
    elif days == 0:
        label = "Due today"
    elif days == 1:
        label = "Due tomorrow"
    elif days < 7:
        label = f"Due {day:%a}"
    else:
        label = f"Due {pretty}"
    return {"date": day, "label": label, "overdue": days < 0}


def due_summary(task):
    """The configured schedule in a few words, for the Manage grid."""
    if not has_explicit_due(task):
        return ""
    freq = task["frequency"]
    if freq == "selected":
        days = ", ".join(WEEKDAYS[int(v)][:3] for v in task["selected_days"].split(",") if v)
        return days + (f" by {format_time(task['due_time'])}" if task["due_time"] else "")
    if freq == "daily":
        return format_time(task["due_time"])
    if freq == "weekly":
        return WEEKDAYS[task["due_weekday"]][:3]
    if freq == "monthly":
        return f"day {task['due_monthday']}"
    return task["due_date"]
