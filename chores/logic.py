"""Who owns what, what's done, and how points are earned.

Kept free of Flask request objects so it can be tested directly.
"""
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import periods
from .db import get_db

SHARED, VOLUNTEER = "shared", "volunteer"
DEFAULT_POINTS = {"daily": 2, "weekly": 5, "monthly": 10, "once": 8, "other": 8}
VOLUNTEER_BONUS = 1.5


# ---------------------------------------------------------------- people ---

def members():
    return get_db().execute("SELECT * FROM users WHERE role = 'member' ORDER BY id").fetchall()


def user_by_name(username):
    return get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def owner_choices():
    """(value, label) pairs for the owner dropdown: each member, then Shared and Needs Volunteer."""
    return [(m["username"], m["display_name"]) for m in members()] + [
        (SHARED, "Shared"), (VOLUNTEER, "Needs Volunteer")]


def owner_label(value):
    return dict(owner_choices()).get(value, value)


# ---------------------------------------------------------------- points ---

def points_for(task, owner_at):
    """Points for one completion: about 1 point per 5 minutes of effort.

    Effort per occurrence = monthly hours / occurrences per month. Tasks
    without an hours estimate use a flat default per frequency. Picking up a
    "Needs Volunteer" task earns a 50% bonus.
    """
    hours = task["hours_month"]
    if hours:
        minutes = hours * 60 / periods.OCCURRENCES_PER_MONTH[task["frequency"]]
        base = max(1, round(minutes / 5))
    else:
        base = DEFAULT_POINTS[task["frequency"]]
    if owner_at == VOLUNTEER:
        base = math.ceil(base * VOLUNTEER_BONUS)
    return base


# ---------------------------------------------------------------- status ---

def _completions_for(keys):
    """Map (task_id, period_key) -> completion row (with doer's name) for the given period keys."""
    if not keys:
        return {}
    q = ("SELECT c.*, u.display_name AS doer, u.username AS doer_username, u.color AS doer_color"
         " FROM completions c JOIN users u ON u.id = c.user_id"
         f" WHERE c.period_key IN ({','.join('?' * len(keys))})")
    return {(r["task_id"], r["period_key"]): r for r in get_db().execute(q, list(keys)).fetchall()}


def _last_logged(task_ids):
    if not task_ids:
        return {}
    q = ("SELECT c.task_id, MAX(c.done_on) AS last_on FROM completions c"
         f" WHERE c.task_id IN ({','.join('?' * len(task_ids))}) GROUP BY c.task_id")
    return {r["task_id"]: r["last_on"] for r in get_db().execute(q, list(task_ids)).fetchall()}


def board(owners, d=None):
    """Active tasks owned by any of `owners`, grouped by frequency, each with its done state.

    Returns {freq: [ {task, done, completion, last_on, due} ]}. A finished
    one-off stays visible for the rest of the day it was done, then drops off.
    """
    d = d or periods.today()
    now = periods.now()
    ph = ",".join("?" * len(owners))
    tasks = get_db().execute(
        f"SELECT * FROM tasks WHERE active = 1 AND owner IN ({ph}) ORDER BY grp, code", list(owners)
    ).fetchall()
    keys = {periods.period_key(f, d) for f in periods.FREQUENCIES}
    done = _completions_for(keys)
    last = _last_logged([t["id"] for t in tasks if t["frequency"] == "other"])
    out = {f: [] for f in periods.FREQUENCIES}
    for t in tasks:
        c = done.get((t["id"], periods.period_key(t["frequency"], d)))
        if t["frequency"] == "once" and c and c["done_on"] != d.isoformat():
            continue
        out[t["frequency"]].append({"task": t, "done": c is not None, "completion": c,
                                    "last_on": last.get(t["id"]), "due": periods.due_info(t, now)})
    for f in out:   # undone first, then soonest due, then alphabetical-by-code
        out[f].sort(key=lambda r: (r["done"], r["due"]["date"] if r["due"] else date.max,
                                   r["task"]["due_time"] or "", r["task"]["code"]))
    return out


def progress(rows):
    total = len(rows)
    done = sum(1 for r in rows if r["done"])
    return {"done": done, "total": total, "pct": round(100 * done / total) if total else 0}


def dashboard(username, d=None):
    """Everything the dashboard needs for one member."""
    mine = board([username, SHARED], d)
    grabs = board([VOLUNTEER], d)
    return {
        "board": mine,
        "grabs": grabs,
        "progress": {f: progress(mine[f]) for f in periods.RECURRING},
        "grabs_open": sum(1 for f in periods.RECURRING + ("once",) for r in grabs[f] if not r["done"]),
    }


# ------------------------------------------------------------- crossing off ---

def can_complete(user, task):
    if user["role"] == "admin":
        return False   # admin manages; members do
    return task["owner"] in (user["username"], SHARED, VOLUNTEER) and task["active"]


def toggle(user, task, d=None):
    """Cross off (or un-cross) a task for its current period. Returns the new state or raises PermissionError."""
    d = d or periods.today()
    db = get_db()
    key = periods.period_key(task["frequency"], d)
    existing = db.execute("SELECT * FROM completions WHERE task_id = ? AND period_key = ?",
                          (task["id"], key)).fetchone()
    if existing:
        if existing["user_id"] != user["id"] and user["role"] != "admin":
            raise PermissionError("Only the person who crossed this off can undo it.")
        db.execute("DELETE FROM completions WHERE id = ?", (existing["id"],))
        db.commit()
        return False
    if not can_complete(user, task):
        raise PermissionError("That task belongs to someone else.")
    try:
        db.execute(
            "INSERT INTO completions(task_id, user_id, period_key, done_on, done_at, owner_at, points)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task["id"], user["id"], key, d.isoformat(),
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             task["owner"], points_for(task, task["owner"])),
        )
        db.commit()
    except sqlite3.IntegrityError:   # the other person crossed it off at the same moment
        db.rollback()
    return True


# ------------------------------------------------------------ game stats ---

def points_in(user_id, start, end):
    r = get_db().execute(
        "SELECT COALESCE(SUM(points), 0) AS pts, COUNT(*) AS n,"
        " SUM(CASE WHEN owner_at = 'volunteer' THEN 1 ELSE 0 END) AS vol"
        " FROM completions WHERE user_id = ? AND done_on >= ? AND done_on < ?",
        (user_id, start.isoformat(), end.isoformat())).fetchone()
    return {"points": r["pts"], "count": r["n"], "volunteer": r["vol"] or 0}


def daily_points(user_id, start, days):
    rows = get_db().execute(
        "SELECT done_on, SUM(points) AS pts FROM completions WHERE user_id = ? AND done_on >= ? AND done_on < ?"
        " GROUP BY done_on", (user_id, start.isoformat(), (start + timedelta(days=days)).isoformat())).fetchall()
    by = {r["done_on"]: r["pts"] for r in rows}
    return [by.get((start + timedelta(days=i)).isoformat(), 0) for i in range(days)]


def streak(username, d=None, limit=366):
    """Consecutive days on which every daily task this person holds (alone or shared) got done.

    Today counts once it's complete; an unfinished today doesn't break the streak.
    Uses today's assignments for past days: re-dealing a task changes the history view.
    """
    d = d or periods.today()
    db = get_db()
    ids = [r["id"] for r in db.execute(
        "SELECT id FROM tasks WHERE active = 1 AND frequency = 'daily' AND owner IN (?, ?)",
        (username, SHARED)).fetchall()]
    if not ids:
        return 0
    start = d - timedelta(days=limit)
    rows = db.execute(
        f"SELECT period_key, COUNT(*) AS n FROM completions WHERE task_id IN ({','.join('?' * len(ids))})"
        " AND period_key >= ? AND period_key LIKE 'D%' GROUP BY period_key",
        ids + [f"D{start.isoformat()}"]).fetchall()
    full = {r["period_key"][1:] for r in rows if r["n"] >= len(ids)}
    n, day = 0, d
    if day.isoformat() not in full:
        day -= timedelta(days=1)
    while day.isoformat() in full and n < limit:
        n += 1
        day -= timedelta(days=1)
    return n


def load_hours(username):
    """Assigned monthly hours: own tasks plus half of shared ones."""
    r = get_db().execute(
        "SELECT COALESCE(SUM(CASE WHEN owner = ? THEN hours_month END), 0) AS own,"
        " COALESCE(SUM(CASE WHEN owner = 'shared' THEN hours_month END), 0) AS shared,"
        " SUM(CASE WHEN owner = ? THEN 1 ELSE 0 END) AS n_own"
        " FROM tasks WHERE active = 1 AND frequency != 'once'", (username, username)).fetchone()
    return {"own": r["own"], "shared_half": r["shared"] / 2, "total": r["own"] + r["shared"] / 2,
            "tasks": r["n_own"] or 0}


BADGES = [
    # key, emoji, title, how to earn
    ("clean_sweep", "🧹", "Clean sweep", "Every daily task done today"),
    ("week_won", "📅", "Week, handled", "Every weekly task done this week"),
    ("month_won", "🗓️", "Month in the bag", "Every monthly task done this month"),
    ("streak3", "🔥", "On a roll", "3-day daily streak"),
    ("streak7", "⚡", "Unstoppable", "7-day daily streak"),
    ("streak30", "🏆", "Legend", "30-day daily streak"),
    ("volunteer", "🙋", "Volunteer", "Picked up a Needs Volunteer task this week"),
    ("hero", "🦸", "Household hero", "Picked up 5+ Needs Volunteer tasks this month"),
]


def badges(username, user_id, dash, streak_days, d=None):
    d = d or periods.today()
    p = dash["progress"]
    wk = points_in(user_id, *periods.range_for("week", d))
    mo = points_in(user_id, *periods.range_for("month", d))
    earned = {
        "clean_sweep": p["daily"]["total"] > 0 and p["daily"]["done"] == p["daily"]["total"],
        "week_won": p["weekly"]["total"] > 0 and p["weekly"]["done"] == p["weekly"]["total"],
        "month_won": p["monthly"]["total"] > 0 and p["monthly"]["done"] == p["monthly"]["total"],
        "streak3": streak_days >= 3, "streak7": streak_days >= 7, "streak30": streak_days >= 30,
        "volunteer": wk["volunteer"] >= 1, "hero": mo["volunteer"] >= 5,
    }
    return [dict(key=k, emoji=e, title=t, how=h, earned=earned[k]) for k, e, t, h in BADGES]


def recent_activity(user_id=None, limit=25):
    q = ("SELECT c.*, t.name AS task_name, t.code, t.frequency, u.display_name AS doer, u.color AS doer_color"
         " FROM completions c JOIN tasks t ON t.id = c.task_id JOIN users u ON u.id = c.user_id")
    args = []
    if user_id:
        q += " WHERE c.user_id = ?"
        args.append(user_id)
    q += " ORDER BY c.done_on DESC, c.done_at DESC LIMIT ?"
    args.append(limit)
    return get_db().execute(q, args).fetchall()


def history_grid(username, user_id, days=14, d=None):
    """Per-day completion of this person's daily tasks, for a little heat-strip."""
    d = d or periods.today()
    db = get_db()
    ids = [r["id"] for r in db.execute(
        "SELECT id FROM tasks WHERE active = 1 AND frequency = 'daily' AND owner IN (?, ?)",
        (username, SHARED)).fetchall()]
    start = d - timedelta(days=days - 1)
    counts = defaultdict(int)
    if ids:
        for r in db.execute(
                f"SELECT period_key, COUNT(*) AS n FROM completions WHERE task_id IN ({','.join('?' * len(ids))})"
                " AND period_key >= ? AND period_key LIKE 'D%' GROUP BY period_key",
                ids + [f"D{start.isoformat()}"]):
            counts[r["period_key"][1:]] = r["n"]
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        n = counts[day.isoformat()]
        out.append({"date": day, "done": n, "total": len(ids),
                    "pct": round(100 * n / len(ids)) if ids else 0})
    return out
