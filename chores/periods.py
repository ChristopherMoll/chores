"""Turning "now" into the period a task belongs to.

Every recurring task resets at the start of its period, in the household's
time zone: daily at midnight, weekly on the configured week-start day,
monthly on the 1st. "Other" tasks never reset; each log is a dated event.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

FREQUENCIES = ("daily", "weekly", "monthly", "other")
FREQ_LABELS = {"daily": "Today", "weekly": "This week", "monthly": "This month", "other": "As needed"}
# Average occurrences per month, used to turn monthly hours into per-occurrence effort.
OCCURRENCES_PER_MONTH = {"daily": 30.44, "weekly": 4.35, "monthly": 1.0, "other": 1.0}


def tz():
    return ZoneInfo(current_app.config["TIMEZONE"])


def now_local():
    return datetime.now(tz())


def today():
    override = current_app.config.get("TODAY_OVERRIDE")   # tests only
    return date.fromisoformat(override) if override else now_local().date()


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
    return f"O{d.isoformat()}"


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
