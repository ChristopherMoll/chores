"""Who owns what, what's done, and how points are earned.

Kept free of Flask request objects so it can be tested directly.
"""
import math
from datetime import date, datetime, timedelta, timezone

from . import periods, scheduling
from .db import get_db

SHARED, VOLUNTEER = "shared", "volunteer"
DEFAULT_POINTS = {"daily": 2, "weekly": 5, "monthly": 10, "once": 8, "other": 8, "selected": 2, "sequence": 2, "interval": 8, "yearly": 10}
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
    if dict(task).get("minutes_each") is not None:
        base = max(1, round(task["minutes_each"] / 5))
    elif hours:
        minutes = hours * 60 / max(.01, scheduling.monthly_occurrences(task))
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
    occurrence_rows = scheduling.rows(owners, d)
    occurrence_keys = {(r["task"]["id"], r["period_key"]) for r in occurrence_rows}
    sequence_roots = {r[0] for r in get_db().execute("SELECT s.root_task_id FROM task_sequences s JOIN tasks t ON t.id=s.root_task_id WHERE t.active=1")}
    now = periods.now()
    ph = ",".join("?" * len(owners))
    tasks = get_db().execute(
        f"SELECT * FROM tasks WHERE active = 1 AND owner IN ({ph}) ORDER BY grp, code", list(owners)
    ).fetchall()
    keys = {periods.period_key(f, d) for f in periods.FREQUENCIES}
    keys.update(periods.task_key(t, d) for t in tasks)
    done = _completions_for(keys)
    last = _last_logged([t["id"] for t in tasks])
    skips = {(r["task_id"], r["period_key"]): r["user_id"] for r in get_db().execute("SELECT task_id,period_key,user_id FROM task_skips")}
    out = {f: [] for f in periods.FREQUENCIES}
    for t in tasks:
        if t["frequency"] in ("selected", "sequence") or (t["id"], periods.task_key(t, d)) in occurrence_keys:
            continue
        key = periods.task_key(t, d)
        c = done.get((t["id"], key))
        if t["frequency"] == "once" and c and c["done_on"] != d.isoformat():
            continue
        out[t["frequency"]].append({"task": t, "done": c is not None, "completion": c,
                                    "period_key": key, "skipped": (t["id"], key) in skips, "skip_user_id": skips.get((t["id"], key)),
                                    "last_on": last.get(t["id"]), "due": periods.due_info(t, now)})
    for row in occurrence_rows:
        freq = row["task"]["frequency"]
        bucket = "selected" if row["source"] == "selected" else freq if row["period_key"] == periods.task_key(row["task"], d) else "sequence"
        out[bucket].append(row)
    # Interval/yearly deadlines advance on completion, but today's receipt stays undoable.
    for t in tasks:
        if t["frequency"] not in ("interval", "yearly"):
            continue
        for c in get_db().execute("SELECT c.*,u.display_name AS doer,u.color AS doer_color FROM completions c JOIN users u ON u.id=c.user_id"
                                  " WHERE c.task_id=? AND c.done_on=? AND (c.period_key LIKE 'I%' OR c.period_key LIKE 'Y%')", (t["id"], d.isoformat())):
            if (t["id"], c["period_key"]) not in occurrence_keys:
                out[t["frequency"]].append({"task": t, "done": True, "completion": c, "period_key": c["period_key"], "last_on": c["done_on"], "due": None, "row_key": f"receipt-{c['id']}"})
    existing_keys = {(r["task"]["id"], r["period_key"]) for group in out.values() for r in group}
    for t in tasks:
        for skip in get_db().execute("SELECT * FROM task_skips WHERE task_id=? AND skipped_on=?", (t["id"], d.isoformat())):
            prefix = {"daily":"D", "weekly":"W", "monthly":"M", "once":"ONCE", "other":"O", "selected":"S", "interval":"I", "yearly":"Y"}.get(t["frequency"])
            if not prefix or (skip["period_key"] != "ONCE" if prefix == "ONCE" else not skip["period_key"].startswith(prefix) or not skip["period_key"][1:5].isdigit()):
                continue
            if (t["id"], skip["period_key"]) in existing_keys:
                continue
            occurrence = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?", (t["id"], skip["period_key"])).fetchone()
            if occurrence:
                row = scheduling._display_row(scheduling.get_occurrence(occurrence["id"]), d)
                row["skip_user_id"] = skip["user_id"]
            else:
                row = {"task": t, "done": False, "completion": None, "period_key": skip["period_key"], "last_on": last.get(t["id"]), "due": None,
                       "skipped": True, "skip_user_id": skip["user_id"], "row_key": f"skip-{t['id']}-{skip['period_key']}",
                       "scheduled_date": date.fromisoformat(skip["period_key"][1:11]) if t["frequency"] in ("interval", "yearly") else None}
            out[t["frequency"]].append(row)
    for f in out:   # undone first, then soonest due, then alphabetical-by-code
        for row in out[f]:
            row["has_sequence"] = row["task"]["id"] in sequence_roots
        out[f].sort(key=row_sort_key)
    return out


def progress(rows):
    rows = [r for r in rows if not r.get("skipped")]
    total = len(rows)
    done = sum(1 for r in rows if r["done"])
    return {"done": done, "total": total, "pct": round(100 * done / total) if total else 0}


def row_sort_key(row):
    due = row.get("due")
    timed = bool(row["task"]["due_time"] or (row.get("run_id") and row.get("ready_at") and row.get("position")))
    rank = 0 if due and due["overdue"] else 1 if timed else 2 if due else 3
    day = due["date"] if due else row.get("scheduled_date") or date.max
    clock = row["task"]["due_time"] or (row["ready_at"].strftime("%H:%M") if row.get("ready_at") and timed else "23:59")
    return bool(row["done"]), rank, day, clock, row["task"]["name"].casefold()


def collapse_missed(rows, d):
    """Keep every date in storage; represent older unfinished dates together in lists."""
    grouped, result = {}, []
    for row in rows:
        if row.get("source") == "selected" and not row.get("run_id") and not row["done"] and not row.get("skipped") and row["scheduled_date"] <= d:
            grouped.setdefault(row["task"]["id"], []).append(row)
        else:
            result.append(row)
    for group in grouped.values():
        group.sort(key=lambda r: r["scheduled_date"], reverse=True)
        result.append(dict(group[0], backlog=group[1:], backlog_count=len(group)-1))
    return result


def dashboard(username, d=None):
    """Everything the dashboard needs for one member."""
    mine = board([username, SHARED], d)
    grabs = board([VOLUNTEER], d)
    d = d or periods.today()
    grabs_rows = sorted(collapse_missed([r for group in grabs.values() for r in group], d), key=row_sort_key)
    weekly_rows = mine["weekly"] + [r for r in mine["selected"] if r["scheduled_date"] and periods.week_start(d) <= r["scheduled_date"] < periods.week_start(d) + timedelta(days=7)]
    return {
        "board": mine,
        "grabs": grabs,
        "grabs_rows": grabs_rows,
        "progress": {f: progress(weekly_rows if f == "weekly" else mine["daily"] + [r for r in mine["selected"] if r["scheduled_date"] == d] if f == "daily" else mine[f]) for f in periods.RECURRING},
        "grabs_open": sum(1 for r in grabs_rows
                          if not r["done"] and not r.get("skipped") and not r.get("waiting") and not r.get("blocked")),
    }


def task_views(dash, d=None):
    """Focused lists without changing the periods used to record completions."""
    d = d or periods.today()
    board_ = dash["board"]
    week_end = periods.week_start(d) + timedelta(days=6)
    month_end = periods.next_month(d) - timedelta(days=1)

    def due_by(row, end):
        due = periods.due_date(row["task"], d)
        return due is not None and due <= end

    views = {
        "today": {"label": "Today", "hint": "Daily routines and deadlines that need your attention today.",
                  "rows": board_["daily"] + [r for f in ("weekly", "monthly", "once")
                                             for r in board_[f] if due_by(r, d)]},
        "week": {"label": "This week", "hint": "Weekly tasks and other deadlines through "
                 + week_end.strftime("%A") + ". Daily routines stay in Today.",
                 "rows": board_["weekly"] + [r for f in ("monthly", "once")
                                             for r in board_[f] if due_by(r, week_end)]},
        "month": {"label": "This month", "hint": "Monthly tasks and one-off deadlines this month.",
                  "rows": board_["monthly"] + [r for r in board_["once"] if due_by(r, month_end)]},
        "once": {"label": "One-off", "hint": "Things to do once, including those without a due date.",
                 "rows": board_["once"]},
        "other": {"label": "As needed", "hint": "No fixed schedule. Record a task when you take care of it.",
                  "rows": board_["other"]},
    }
    selected = board_["selected"]
    sequences = board_["sequence"]
    for key, end in (("today", d), ("week", week_end), ("month", month_end)):
        views[key]["rows"] += [r for freq in ("interval", "yearly") for r in board_[freq]
                               if r["done"] or r.get("skipped") or due_by(r, end)]
    views["today"]["rows"] += [r for r in selected if r["scheduled_date"] <= d]
    views["week"]["rows"] += [r for r in selected if r["scheduled_date"] <= week_end]
    views["month"]["rows"] += [r for r in selected if r["scheduled_date"] <= month_end]
    for key, end in (("today", d), ("week", week_end), ("month", month_end)):
        views[key]["rows"] += [r for r in sequences if r["scheduled_date"] is None or r["scheduled_date"] <= end or r.get("waiting")]
    for key, view in views.items():
        start = {"today": d, "week": periods.week_start(d), "month": periods.month_start(d)}.get(key)
        if start:
            view["rows"] = [r for r in view["rows"] if not (r.get("occurrence_id") and r["done"]
                            and r["completion"]["done_on"] < start.isoformat())]
        view["rows"] = [dict(r, due=None if r["task"]["frequency"] == "daily" and not r["task"]["due_time"] else r["due"] if r.get("occurrence_id") or r.get("skipped") or r["done"] else periods.due_info(r["task"], datetime.combine(d, periods.now().time()), explicit_only=False))
                        for r in view["rows"]]
        view["rows"] = sorted(collapse_missed(view["rows"], d), key=row_sort_key)
        view["progress"] = progress([r for r in view["rows"] if not r.get("blocked") and (key != "today" or not r.get("waiting"))])
        view["open"] = [r for r in view["rows"] if not r["done"] and not r.get("skipped") and not r.get("waiting") and not r.get("blocked")]
        view["upcoming"] = [r for r in view["rows"] if not r["done"] and not r.get("skipped") and (r.get("waiting") or r.get("blocked"))]
        view["remaining"] = len(view["open"]) + (len(view["upcoming"]) if key != "today" else 0)
        view["completed"] = [r for r in view["rows"] if r["done"]]
        view["skipped"] = [r for r in view["rows"] if r.get("skipped")]
    return views


# ------------------------------------------------------------- crossing off ---

def can_complete(user, task):
    if user["role"] == "admin":
        return False   # admin manages; members do
    return task["owner"] in (user["username"], SHARED, VOLUNTEER) and task["active"]


def can_steal(user, task):
    """A member may cover another member's task; the actual doer receives credit."""
    return (user["role"] == "member" and task["active"]
            and task["owner"] not in (user["username"], SHARED, VOLUNTEER))


def toggle(user, task, d=None, desired_done=None, steal=False, occurrence_id=None, early=False, period_override=None, expected_period=None):
    """Cross off (or un-cross) a task for its current period. Returns the new state or raises PermissionError.

    steal=True also allows crossing off another member's task (see can_steal); the doer gets the points.
    """
    d = d or periods.today()
    if period_override and desired_done is False and not occurrence_id:
        old = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?", (task["id"], period_override)).fetchone()
        occurrence_id = old["id"] if old else None
        occurrence = scheduling.get_occurrence(occurrence_id) if occurrence_id else None
    else:
        occurrence = scheduling.resolve(task, occurrence_id)
    if occurrence:
        return scheduling.toggle(user, occurrence, desired_done, early=early, steal=steal)
    db = get_db()
    with db:
        # Serialize skip/complete and interval advancement, including two phones
        # sending the same deadline at once.
        db.execute("UPDATE tasks SET id=id WHERE id=?", (task["id"],))
        allowed = can_complete(user, task) or (steal and can_steal(user, task))
        key = period_override if desired_done is False and period_override else periods.task_key(task, d)
        if expected_period and key != expected_period:
            raise ValueError("This task's schedule has moved on. Refresh your list before changing it.")
        existing = db.execute("SELECT * FROM completions WHERE task_id = ? AND period_key = ?",
                              (task["id"], key)).fetchone()
        # Explicit state makes a repeated request safe after an interrupted connection.
        if desired_done is True and existing:
            if not allowed:
                raise PermissionError("That task belongs to someone else.")
            return True
        if desired_done is False and not existing:
            if not allowed:
                raise PermissionError("That task belongs to someone else.")
            return False
        if existing:
            if existing["user_id"] != user["id"] and user["role"] != "admin":
                raise PermissionError("Only the person who crossed this off can undo it.")
            db.execute("DELETE FROM completions WHERE id = ?", (existing["id"],))
            return False
        if not allowed:
            raise PermissionError("That task belongs to someone else.")
        if db.execute("SELECT 1 FROM task_skips WHERE task_id=? AND period_key=?", (task["id"], key)).fetchone():
            raise ValueError("Restore this skipped task before completing it.")
        db.execute(
            "INSERT INTO completions(task_id, user_id, period_key, done_on, done_at, owner_at, points)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task["id"], user["id"], key, d.isoformat(),
             scheduling.stamp(scheduling.now_utc()) if scheduling.recipe(task["id"]) else datetime.now(timezone.utc).isoformat(timespec="seconds"),
             task["owner"], points_for(task, task["owner"])),
        )
        scheduling.after_completion(task, key)
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
    """Completed routine days; off/away/skipped days don't award or break a streak."""
    from .planning import routine_history
    count = 0
    for row in reversed(routine_history(username, limit, d or periods.today())):
        if row["away"] or not row["total"]:
            continue
        if row["handled"]:
            count += bool(row["done"])
        elif row["date"] != (d or periods.today()):
            break
    return count


def load_hours(username):
    """Assigned monthly hours: own tasks plus half of shared ones."""
    db = get_db()
    recurring_steps = set()
    for seq in db.execute("SELECT s.root_task_id FROM task_sequences s JOIN tasks t ON t.id=s.root_task_id WHERE t.active=1 AND t.frequency!='once'"):
        recurring_steps.update(step["task_id"] for step in scheduling.recipe(seq["root_task_id"]))
    tasks = [t for t in db.execute("SELECT * FROM tasks WHERE active=1 AND frequency!='once'")
             if t["frequency"] != "sequence" or t["id"] in recurring_steps]
    own = sum(monthly_hours(t) for t in tasks if t["owner"] == username)
    shared = sum(monthly_hours(t) for t in tasks if t["owner"] == SHARED) / 2
    return {"own": own, "shared_half": shared, "total": own + shared,
            "tasks": sum(t["owner"] == username for t in tasks)}


def monthly_hours(task):
    if task["minutes_each"] is None:
        return task["hours_month"] or 0
    count = scheduling.monthly_occurrences(task)
    if task["frequency"] == "sequence":
        count = sum(scheduling.monthly_occurrences(root) for root in get_db().execute("SELECT t.* FROM task_sequences s JOIN tasks t ON t.id=s.root_task_id WHERE t.active=1 AND t.frequency!='once'")
                    if any(step["task_id"] == task["id"] for step in scheduling.recipe(root["id"])))
    return task["minutes_each"] * count / 60


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
    """Routine history includes selected days and marks excused days."""
    from .planning import routine_history
    return routine_history(username, days, d or periods.today())
