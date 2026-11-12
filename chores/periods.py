"""Turning "now" into the period a task belongs to, and working out when it's due.

Every recurring task resets at the start of its period, in the household's
time zone: daily at midnight, weekly on the configured week-start day,
monthly on the 1st. "Once" tasks are done one time. "Other" tasks never
reset; each log is a dated event.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import calendar
import hashlib

from flask import current_app

FREQUENCIES = ("daily", "weekly", "monthly", "once", "other", "selected", "sequence", "interval", "yearly")
RECURRING = ("daily", "weekly", "monthly", "selected", "interval", "yearly")
FREQ_LABELS = {"daily": "Today", "weekly": "This week", "monthly": "This month",
               "once": "One-off", "other": "As needed", "selected": "Selected days", "sequence": "Sequence step",
               "interval": "Every N weeks/months", "yearly": "Yearly"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = tuple(calendar.month_name[1:])
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


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def repeat_due(task, d):
    """One outstanding deadline; an interval advances from actual work, not missed cycles."""
    from .db import get_db
    prefix = "I" if task["frequency"] == "interval" else "Y"
    last = get_db().execute("SELECT done_on AS day,period_key FROM completions WHERE task_id=? AND period_key LIKE ?"
                            " UNION ALL SELECT skipped_on AS day,period_key FROM task_skips WHERE task_id=? AND period_key LIKE ?"
                            " ORDER BY day DESC,period_key DESC LIMIT 1", (task["id"], prefix + "%", task["id"], prefix + "%")).fetchone()
    first = date.fromisoformat(task["repeat_start"]) if task["repeat_start"] else d
    if task["frequency"] == "interval":
        if not last:
            return first
        base = date.fromisoformat(last["day"])
        return base + timedelta(weeks=task["repeat_every"]) if task["repeat_unit"] == "weeks" else add_months(base, task["repeat_every"])
    month, day = task["year_month"] or first.month, task["year_day"] or first.day
    year = max(first.year, date.fromisoformat(last["day"]).year + 1, int(last["period_key"][1:5]) + 1) if last else first.year
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def task_key(task, d=None):
    d = d or today()
    if task["frequency"] in ("interval", "yearly"):
        key = ("I" if task["frequency"] == "interval" else "Y") + repeat_due(task, d).isoformat()
        if task["frequency"] == "interval":
            # A second early completion on the same day must still start a new cycle.
            # The state token also returns to the right cycle when work is undone.
            from .db import get_db
            state = [f"c{r['id']}" for r in get_db().execute("SELECT id FROM completions WHERE task_id=? AND period_key LIKE 'I%' ORDER BY id", (task["id"],))]
            state += [r["period_key"] for r in get_db().execute("SELECT period_key FROM task_skips WHERE task_id=? AND period_key LIKE 'I%' ORDER BY period_key", (task["id"],))]
            key += ":" + hashlib.sha256("|".join(state).encode()).hexdigest()[:12]
        return key
    return period_key(task["frequency"], d)


def human_date(value):
    if not value:
        return "Not yet"
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    gap = (today() - day).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Yesterday"
    if gap == -1:
        return "Tomorrow"
    return day.strftime("%a %b ") + str(day.day) + (f", {day.year}" if day.year != today().year else "")


def duration(minutes):
    minutes = int(minutes)
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440} day{'s' if minutes != 1440 else ''}"
    if minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {rest} min" if rest else "")
    return f"{minutes} min"


def human_datetime(value):
    if not value:
        return "Not recorded yet"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        moment = moment.astimezone(tz()) if moment.tzinfo else moment
        return human_date(moment.date()) + " at " + format_time(moment.time())
    except ValueError:
        return "Unknown time"


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
    if freq in ("interval", "yearly"):
        return repeat_due(task, d)
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
    if task["frequency"] in ("daily", "selected", "interval", "yearly") and task["due_time"]:
        return datetime.combine(day, time.fromisoformat(task["due_time"]))
    return datetime.combine(day, END_OF_DAY)


def format_time(value):
    """'20:30' or a time -> '8:30 pm'. strftime's no-padding flags aren't portable."""
    t = time.fromisoformat(value) if isinstance(value, str) else value
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def has_explicit_due(task):
    freq = task["frequency"]
    return bool(freq in ("interval", "yearly") or (freq == "selected" and task["selected_days"])
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
    if freq == "interval":
        return f"Every {task['repeat_every']} {task['repeat_unit']} from last done"
    if freq == "yearly":
        first = date.fromisoformat(task["repeat_start"]) if task["repeat_start"] else today()
        return f"Yearly on {calendar.month_abbr[task['year_month'] or first.month]} {task['year_day'] or first.day}"
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
