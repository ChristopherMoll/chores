"""Personal planning helpers: quick capture, skipped work, and time away."""
from datetime import date, timedelta
import uuid

from . import periods, scheduling
from .db import get_db


def is_away(user_id, day=None):
    day = (day or periods.today()).isoformat()
    return bool(get_db().execute("SELECT 1 FROM away_dates WHERE user_id=? AND starts_on<=? AND ends_on>=?",
                                 (user_id, day, day)).fetchone())


def quick_add(user, name, due_date="", request_key=None):
    if user["role"] != "member":
        raise PermissionError("Sign in with a household member account to add a task for yourself.")
    name = " ".join(name.split())
    if not name or len(name) > 80:
        raise ValueError("Give your task a name of 1 to 80 characters.")
    due = date.fromisoformat(due_date).isoformat() if due_date else None
    key = request_key or uuid.uuid4().hex
    db = get_db()
    with db:
        db.execute("UPDATE users SET id=id WHERE id=?", (user["id"],))
        existing = db.execute("SELECT task_id FROM quick_add_requests WHERE user_id=? AND request_key=?", (user["id"], key)).fetchone()
        if existing:
            return existing["task_id"]
        task_id = db.execute("INSERT INTO tasks(code,grp,name,frequency,owner,due_date) VALUES (?,'Home',?,'once',?,?)",
                             ("Q" + uuid.uuid4().hex[:7].upper(), name, user["username"], due)).lastrowid
        db.execute("INSERT INTO quick_add_requests VALUES (?,?,?)", (user["id"], key, task_id))
    return task_id


def skip_task(user, task, key, occurrence=None, restore=False):
    from .logic import can_complete
    if not can_complete(user, task):
        raise PermissionError("Only the assigned person can change this task.")
    if task["frequency"] == "selected" and not occurrence:
        scheduling.ensure_selected()
        found = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?", (task["id"], key)).fetchone()
        if not found:
            raise ValueError("Choose a scheduled date from the task list.")
        occurrence = scheduling.get_occurrence(found["id"])
    if occurrence and occurrence["run_id"]:
        raise ValueError("Use Cancel the rest on this run to stop its unfinished steps.")
    if occurrence and (occurrence["task_id"] != task["id"] or occurrence["occurrence_key"] != key):
        raise ValueError("This task's date has changed. Refresh the list.")
    db = get_db()
    with db:
        db.execute("UPDATE tasks SET id=id WHERE id=?", (task["id"],))
        skip = db.execute("SELECT * FROM task_skips WHERE task_id=? AND period_key=?", (task["id"], key)).fetchone()
        if restore:
            if skip and skip["user_id"] != user["id"]:
                raise PermissionError("Only the person who skipped this task can restore it.")
            db.execute("DELETE FROM task_skips WHERE task_id=? AND period_key=?", (task["id"], key))
            if occurrence:
                db.execute("UPDATE task_occurrences SET state='open' WHERE id=? AND state='skipped'", (occurrence["id"],))
            return
        if key != (occurrence["occurrence_key"] if occurrence else periods.task_key(task)):
            raise ValueError("This task's schedule has changed. Refresh the list.")
        if db.execute("SELECT 1 FROM completions WHERE task_id=? AND period_key=?", (task["id"], key)).fetchone():
            raise ValueError("Undo the completion before skipping this task.")
        db.execute("INSERT OR IGNORE INTO task_skips VALUES (?,?,?,?)", (task["id"], key, user["id"], periods.today().isoformat()))
        if occurrence:
            db.execute("UPDATE task_occurrences SET state='skipped' WHERE id=? AND run_id IS NULL", (occurrence["id"],))


def clear_older(user, task):
    from .logic import can_complete
    if not can_complete(user, task) or task["frequency"] != "selected":
        raise PermissionError("Only the assigned person can clear these dates.")
    scheduling.ensure_selected()
    rows = get_db().execute("SELECT * FROM task_occurrences o WHERE task_id=? AND source='selected'"
                            " AND run_id IS NULL AND state='open' AND scheduled_date<? AND NOT EXISTS"
                            " (SELECT 1 FROM completions c WHERE c.task_id=o.task_id AND c.period_key=o.occurrence_key)"
                            " ORDER BY scheduled_date DESC", (task["id"], periods.today().isoformat())).fetchall()
    # Keep today's date, or the most recent missed date when today isn't scheduled.
    has_today = get_db().execute("SELECT 1 FROM task_occurrences WHERE task_id=? AND scheduled_date=? AND state='open'",
                                 (task["id"], periods.today().isoformat())).fetchone()
    older = rows if has_today else rows[1:]
    changed = 0
    with get_db():
        get_db().execute("UPDATE tasks SET id=id WHERE id=?", (task["id"],))
        for row in older:
            if get_db().execute("SELECT 1 FROM completions WHERE task_id=? AND period_key=?", (task["id"], row["occurrence_key"])).fetchone():
                continue
            get_db().execute("INSERT OR IGNORE INTO task_skips VALUES (?,?,?,?)", (task["id"], row["occurrence_key"], user["id"], periods.today().isoformat()))
            get_db().execute("UPDATE task_occurrences SET state='skipped' WHERE id=?", (row["id"],))
            changed += 1
    return changed


def yesterday(user, task, expected_period=None):
    from . import logic
    if not logic.can_complete(user, task):
        raise PermissionError("That task belongs to someone else.")
    if task["frequency"] == "sequence" or scheduling.recipe(task["id"]):
        raise ValueError("Use the sequence run to correct its steps; their timers depend on completion times.")
    day = periods.today() - timedelta(days=1)
    occurrence = None
    if task["frequency"] == "selected":
        scheduling.ensure_selected()
        occurrence = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?",
                                       (task["id"], periods.period_key("selected", day))).fetchone()
        if not occurrence:
            raise ValueError("This task wasn't scheduled yesterday.")
    with get_db():
        get_db().execute("UPDATE tasks SET id=id WHERE id=?", (task["id"],))
        if expected_period and task["frequency"] in ("interval", "yearly") and expected_period != periods.task_key(task):
            if get_db().execute("SELECT 1 FROM completions WHERE task_id=? AND period_key=? AND user_id=? AND done_on=?",
                                 (task["id"], expected_period, user["id"], day.isoformat())).fetchone():
                return
            raise ValueError("This task's deadline has moved on. Refresh the list before recording yesterday.")
        if occurrence:
            # Dedicated transaction below records the true work date, with no follow-up activation.
            row = scheduling.get_occurrence(occurrence["id"])
            if row["state"] != "open" or row["run_id"]:
                raise ValueError("That date is no longer available. Review it in the app.")
            key = row["occurrence_key"]
        else:
            key = periods.task_key(task, day)
        get_db().execute("DELETE FROM task_skips WHERE task_id=? AND period_key=?", (task["id"], key))
        get_db().execute("INSERT OR IGNORE INTO completions(task_id,user_id,period_key,done_on,done_at,owner_at,points) VALUES (?,?,?,?,?,?,?)",
                         (task["id"], user["id"], key, day.isoformat(), scheduling.stamp(scheduling.now_utc()), task["owner"], logic.points_for(task, task["owner"])))


def routine_history(username, days, end):
    """Expected daily + selected-day work. Off days and away days don't break streaks."""
    db = get_db()
    tasks = db.execute("SELECT * FROM tasks WHERE active=1 AND frequency IN ('daily','selected') AND owner IN (?,'shared')", (username,)).fetchall()
    user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    done = {(r["task_id"], r["period_key"]) for r in db.execute("SELECT task_id,period_key FROM completions WHERE period_key LIKE 'D%' OR period_key LIKE 'S%'")}
    skipped = {(r["task_id"], r["period_key"]) for r in db.execute("SELECT task_id,period_key FROM task_skips")}
    skipped.update((r["task_id"], r["occurrence_key"]) for r in db.execute("SELECT task_id,occurrence_key FROM task_occurrences WHERE state='skipped'"))
    away = db.execute("SELECT starts_on,ends_on FROM away_dates WHERE user_id=?", (user["id"],)).fetchall() if user else []
    result = []
    for index in range(days):
        day = end - timedelta(days=days - index - 1)
        expected = [(t["id"], periods.period_key(t["frequency"], day)) for t in tasks
                    if t["frequency"] == "daily" or (str(day.weekday()) in t["selected_days"].split(",")
                    and (not t["selected_start"] or t["selected_start"] <= day.isoformat()))]
        absent = any(r["starts_on"] <= day.isoformat() <= r["ends_on"] for r in away)
        count = sum(key in done for key in expected)
        excused = sum(key in skipped for key in expected)
        needed = len(expected) - excused
        result.append({"date": day, "done": count, "total": needed, "skipped": excused, "away": absent,
                       "pct": round(100 * count / needed) if needed else 0,
                       "handled": bool(expected) and count + excused == len(expected)})
    return result
