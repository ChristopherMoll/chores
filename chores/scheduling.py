"""Dated occurrences and durable, linear sequences. All delays are elapsed UTC time."""
import json
import math
import uuid
from datetime import date, datetime, time, timedelta, timezone

from . import periods
from .db import get_db


def now_utc():
    return periods.now().replace(tzinfo=periods.tz()).astimezone(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def local(value):
    return datetime.fromisoformat(value).astimezone(periods.tz()) if value else None


def parse_days(values):
    """Return a canonical set; all seven days are valid."""
    if isinstance(values, str):
        names = {name.lower(): i for i, name in enumerate(periods.WEEKDAYS)}
        names.update({name[:3].lower(): i for i, name in enumerate(periods.WEEKDAYS)})
        values = [part.strip().lower() for part in values.replace(";", ",").split(",") if part.strip()]
        values = [names.get(v, v) for v in values]
    try:
        days = sorted({int(value) for value in values})
    except (TypeError, ValueError):
        raise ValueError("Choose days from Monday through Sunday.") from None
    if not days or any(day < 0 or day > 6 for day in days):
        raise ValueError("Choose at least one day, Monday through Sunday.")
    return ",".join(map(str, days))


def monthly_occurrences(task):
    if task["frequency"] == "selected":
        return 4.35 * len([v for v in task["selected_days"].split(",") if v])
    return periods.OCCURRENCES_PER_MONTH.get(task["frequency"], 1)


def sync_selected(task_id):
    """Rebuild uncompleted current/future dates after an explicit schedule edit."""
    db = get_db()
    today = periods.today().isoformat()
    db.execute("UPDATE task_occurrences SET state='cancelled' WHERE task_id=? AND source='selected'"
               " AND scheduled_date>=? AND state='open' AND run_id IS NULL AND NOT EXISTS"
               " (SELECT 1 FROM completions c WHERE c.task_id=task_occurrences.task_id"
               " AND c.period_key=task_occurrences.occurrence_key)", (task_id, today))
    db.execute("UPDATE tasks SET selected_until=?, selected_start=COALESCE(selected_start,?) WHERE id=?",
               ((periods.today() - timedelta(days=1)).isoformat(), today, task_id))


def ensure_selected(d=None):
    """Materialize through the next month boundary; missed dates survive restarts."""
    db = get_db()
    d = d or periods.today()
    horizon = periods.next_month(d) + timedelta(days=7)
    for task in db.execute("SELECT * FROM tasks WHERE active=1 AND frequency='selected'").fetchall():
        if not task["selected_days"]:
            continue
        start = date.fromisoformat(task["selected_start"]) if task["selected_start"] else d
        cursor = date.fromisoformat(task["selected_until"]) + timedelta(days=1) if task["selected_until"] else start
        days = {int(v) for v in task["selected_days"].split(",")}
        while cursor <= horizon:
            if cursor.weekday() in days:
                ready = datetime.combine(cursor, time.min, periods.tz())
                due = datetime.combine(cursor, time.fromisoformat(task["due_time"]) if task["due_time"] else periods.END_OF_DAY, periods.tz())
                db.execute("INSERT INTO task_occurrences(task_id,occurrence_key,source,scheduled_date,ready_at,due_at)"
                           " VALUES (?,?,'selected',?,?,?) ON CONFLICT(task_id,occurrence_key) DO UPDATE SET"
                           " state='open',ready_at=excluded.ready_at,due_at=excluded.due_at"
                           " WHERE task_occurrences.state='cancelled' AND task_occurrences.run_id IS NULL AND NOT EXISTS"
                           " (SELECT 1 FROM completions c WHERE c.task_id=task_occurrences.task_id"
                           " AND c.period_key=task_occurrences.occurrence_key)",
                           (task["id"], f"S{cursor.isoformat()}", cursor.isoformat(), stamp(ready), stamp(due)))
            cursor += timedelta(days=1)
        db.execute("UPDATE tasks SET selected_start=?,selected_until=? WHERE id=?",
                   (start.isoformat(), max(horizon, cursor - timedelta(days=1)).isoformat(), task["id"]))
    db.commit()


def recipe(task_id):
    row = get_db().execute("SELECT steps_json FROM task_sequences WHERE root_task_id=?", (task_id,)).fetchone()
    return json.loads(row["steps_json"]) if row else []


def parse_recipe(form, root_id=None):
    """An empty editor removes future sequences; active runs keep their snapshots."""
    if "sequence_editor" not in form:
        return None
    ids, names, delays, hours = (form.getlist(name) for name in ("step_task", "step_name", "step_delay", "step_hours"))
    if not (len(ids) == len(names) == len(delays) == len(hours)) or len(ids) > 40:
        raise ValueError("Check the sequence steps and try again.")
    result, used = [], {root_id}
    db = get_db()
    for task_id, name, delay, effort in zip(ids, names, delays, hours):
        name = name.strip()
        if not task_id and not name:
            continue
        if len(result) >= 20:
            raise ValueError("A sequence can have up to 20 follow-ups.")
        try:
            tid = int(task_id) if task_id else None
            minutes = int(delay)
            h = float(effort) if effort.strip() else None
        except ValueError:
            raise ValueError("Step delays must be whole minutes, and effort must be a number.") from None
        if not 0 <= minutes <= 525600 or (h is not None and (not math.isfinite(h) or not 0 <= h <= 744)):
            raise ValueError("Use a delay from 0 to 525600 minutes and effort from 0 to 744 hours/month.")
        if (tid is not None and tid in used) or (not tid and len(name) > 80):
            raise ValueError("Each sequence step needs a different task and a name of at most 80 characters.")
        if tid:
            task = db.execute("SELECT * FROM tasks WHERE id=? AND active=1", (tid,)).fetchone()
            if not task:
                raise ValueError("A follow-up task is no longer available.")
            if task["frequency"] != "sequence":
                raise ValueError("Existing follow-ups must be Sequence step tasks, so they don't also repeat independently.")
            if recipe(tid):
                raise ValueError("A sequence cannot contain another sequence.")
            name = task["name"]
            used.add(tid)
        result.append({"task_id": tid, "name": name, "delay": minutes, "hours": h})
    return result


def save_recipe(root_task, steps):
    if steps is None:
        return
    db = get_db()
    if steps and root_task["frequency"] == "sequence":
        raise ValueError("A follow-up cannot also start a sequence.")
    saved = []
    for step in steps:
        tid = step["task_id"]
        if not tid:
            code = "S" + uuid.uuid4().hex[:7].upper()
            tid = db.execute("INSERT INTO tasks(code,grp,name,frequency,owner,hours_month) VALUES (?,?,?,'sequence',?,?)",
                             (code, root_task["grp"], step["name"], root_task["owner"], step["hours"])).lastrowid
        saved.append({"task_id": tid, "delay": step["delay"]})
    if saved:
        db.execute("INSERT INTO task_sequences(root_task_id,steps_json) VALUES (?,?)"
                   " ON CONFLICT(root_task_id) DO UPDATE SET steps_json=excluded.steps_json",
                   (root_task["id"], json.dumps(saved)))
    else:
        db.execute("DELETE FROM task_sequences WHERE root_task_id=?", (root_task["id"],))


def _snapshot(task, root):
    from .logic import points_for
    data = dict(task)
    if task["frequency"] == "sequence":
        base = max(1, round(task["hours_month"] * 60 / monthly_occurrences(root) / 5)) if task["hours_month"] else 2
        data["completion_points"] = math.ceil(base * 1.5) if task["owner"] == "volunteer" else base
    else:
        data["completion_points"] = points_for(task, task["owner"])
    return json.dumps(data)


def start_run(root, key, *, root_ready=True):
    """Called inside the same transaction that saves the initiating completion."""
    db = get_db()
    # Lock before checking the key: two retries may reach this process together.
    db.execute("UPDATE tasks SET id=id WHERE id=?", (root["id"],))
    existing = db.execute("SELECT id FROM sequence_runs WHERE root_task_id=? AND root_key=?", (root["id"], key)).fetchone()
    if existing:
        return existing["id"]
    steps = recipe(root["id"])
    if not steps:
        return None
    run_id = uuid.uuid4().hex
    now = now_utc()
    db.execute("INSERT INTO sequence_runs(id,root_task_id,root_key,name,created_at) VALUES (?,?,?,?,?)",
               (run_id, root["id"], key, root["name"], stamp(now)))
    due = periods.due_at(root, periods.today()) if key == periods.period_key(root["frequency"]) else None
    db.execute("INSERT INTO task_occurrences(task_id,occurrence_key,source,scheduled_date,ready_at,due_at,run_id,position,snapshot)"
               " VALUES (?,?,'sequence',?,?,?,?,0,?)", (root["id"], key, periods.today().isoformat(), stamp(now),
                                                      stamp(due.replace(tzinfo=periods.tz())) if due else None, run_id, _snapshot(root, root)))
    for position, step in enumerate(steps, 1):
        task = db.execute("SELECT * FROM tasks WHERE id=?", (step["task_id"],)).fetchone()
        if not task or not task["active"]:
            raise ValueError("A sequence step was archived. Update the sequence before starting it.")
        db.execute("INSERT INTO task_occurrences(task_id,occurrence_key,source,run_id,position,delay_minutes,snapshot)"
                   " VALUES (?,?,'sequence',?,?,?,?)", (task["id"], f"R{run_id}:{position}", run_id, position,
                                                        step["delay"], _snapshot(task, root)))
    if not root_ready:
        advance(run_id, 0, now)
    return run_id


def advance(run_id, position, completed_at):
    db = get_db()
    following = db.execute("SELECT * FROM task_occurrences WHERE run_id=? AND position=?", (run_id, position + 1)).fetchone()
    if following:
        ready = completed_at + timedelta(minutes=following["delay_minutes"])
        db.execute("UPDATE task_occurrences SET ready_at=?,scheduled_date=?,activation=activation+1"
                   " WHERE id=? AND ready_at IS NULL AND state='open'",
                   (stamp(ready), ready.astimezone(periods.tz()).date().isoformat(), following["id"]))
    else:
        db.execute("UPDATE sequence_runs SET state='completed' WHERE id=?", (run_id,))


def after_completion(task, key):
    # Legacy/root completion and the next activation are committed together.
    if recipe(task["id"]):
        start_run(task, key, root_ready=False)


def get_occurrence(occurrence_id):
    return get_db().execute("SELECT o.*,r.state AS run_state,r.name AS run_name,r.root_task_id FROM task_occurrences o"
                            " LEFT JOIN sequence_runs r ON r.id=o.run_id WHERE o.id=?", (occurrence_id,)).fetchone()


def completion(occurrence):
    return get_db().execute("SELECT c.*,u.display_name AS doer,u.color AS doer_color FROM completions c"
                            " JOIN users u ON u.id=c.user_id WHERE task_id=? AND period_key=?",
                            (occurrence["task_id"], occurrence["occurrence_key"])).fetchone()


def occurrence_task(occurrence):
    if occurrence["snapshot"]:
        return json.loads(occurrence["snapshot"])
    return dict(get_db().execute("SELECT * FROM tasks WHERE id=?", (occurrence["task_id"],)).fetchone())


def candidates(task_id, include_done=False):
    ensure_selected()
    return get_db().execute("SELECT o.* FROM task_occurrences o LEFT JOIN sequence_runs r ON r.id=o.run_id"
                            " WHERE o.task_id=? AND o.state='open' AND (r.state IS NULL OR r.state!='cancelled')"
                            " AND o.ready_at IS NOT NULL AND o.ready_at<=?"
                            + ("" if include_done else " AND NOT EXISTS(SELECT 1 FROM completions c WHERE c.task_id=o.task_id AND c.period_key=o.occurrence_key)")
                            + " ORDER BY o.ready_at,o.id", (task_id, stamp(now_utc()))).fetchall()


def resolve(task, occurrence_id=None):
    if occurrence_id:
        row = get_occurrence(occurrence_id)
        if not row or row["task_id"] != task["id"]:
            raise ValueError("That occurrence could not be found. Refresh your list.")
        return row
    if task["frequency"] in ("selected", "sequence"):
        choices = candidates(task["id"])
        if len(choices) > 1:
            raise ValueError("More than one occurrence is ready. Choose the date or load in the app.")
        if choices:
            return get_occurrence(choices[0]["id"])
        choices = candidates(task["id"], include_done=True)
        today = [row for row in choices if row["scheduled_date"] == periods.today().isoformat()]
        if len(today) == 1:
            return get_occurrence(today[0]["id"])
        raise ValueError("There isn't a ready occurrence of this task. Check its scheduled days or waiting steps in the app.")
    return get_db().execute("SELECT o.*,r.state AS run_state FROM task_occurrences o JOIN sequence_runs r ON r.id=o.run_id"
                            " WHERE o.task_id=? AND o.occurrence_key=?", (task["id"], periods.period_key(task["frequency"]))).fetchone()


def toggle(user, occurrence, desired_done=None, *, early=False, steal=False):
    from .logic import can_complete, can_steal, points_for
    db = get_db()
    now = now_utc()
    with db:
        # Serialize the completion, dependency check, and follow-up activation.
        db.execute("UPDATE task_occurrences SET state=state WHERE id=?", (occurrence["id"],))
        occurrence = get_occurrence(occurrence["id"])
        task = occurrence_task(occurrence)
        existing = completion(occurrence)
        allowed = can_complete(user, task) or (steal and can_steal(user, task))
        if not allowed and not (existing and existing["user_id"] == user["id"]):
            raise PermissionError("That task belongs to someone else.")
        if occurrence["state"] != "open" or occurrence["run_state"] == "cancelled":
            raise ValueError("This occurrence is no longer active. Refresh your list.")
        target = not bool(existing) if desired_done is None else desired_done
        if target and existing:
            return True
        if not target:
            if not existing:
                return False
            if existing["user_id"] != user["id"] and user["role"] != "admin":
                raise PermissionError("Only the person who crossed this off can undo it.")
            if occurrence["run_id"]:
                later = db.execute("SELECT 1 FROM task_occurrences o JOIN completions c ON c.task_id=o.task_id AND c.period_key=o.occurrence_key"
                                   " WHERE o.run_id=? AND o.position>?", (occurrence["run_id"], occurrence["position"])).fetchone()
                if later:
                    raise ValueError("Undo the later completed steps first. Their work will be kept until then.")
                db.execute("UPDATE task_occurrences SET ready_at=NULL,scheduled_date=NULL,activation=activation+1"
                           " WHERE run_id=? AND position>?", (occurrence["run_id"], occurrence["position"]))
                db.execute("UPDATE sequence_runs SET state='active' WHERE id=?", (occurrence["run_id"],))
            db.execute("DELETE FROM completions WHERE id=?", (existing["id"],))
            return False
        if not occurrence["ready_at"]:
            raise ValueError("Complete the previous step first.")
        if datetime.fromisoformat(occurrence["ready_at"]) > now and not early:
            raise ValueError("This step is still waiting. Choose Done early if you've already taken care of it.")
        points = task["completion_points"] if "completion_points" in task else points_for(task, task["owner"])
        db.execute("INSERT OR IGNORE INTO completions(task_id,user_id,period_key,done_on,done_at,owner_at,points) VALUES (?,?,?,?,?,?,?)",
                   (task["id"], user["id"], occurrence["occurrence_key"], periods.today().isoformat(), stamp(now), task["owner"], points))
        if occurrence["run_id"]:
            advance(occurrence["run_id"], occurrence["position"], now)
        elif recipe(task["id"]):
            # Promote the selected-day root into the run without creating another root occurrence.
            run_id = uuid.uuid4().hex
            db.execute("INSERT INTO sequence_runs(id,root_task_id,root_key,name,created_at) VALUES (?,?,?,?,?)",
                       (run_id, task["id"], occurrence["occurrence_key"], task["name"], stamp(now)))
            db.execute("UPDATE task_occurrences SET run_id=?,snapshot=? WHERE id=?", (run_id, _snapshot(task, task), occurrence["id"]))
            for position, step in enumerate(recipe(task["id"]), 1):
                child = db.execute("SELECT * FROM tasks WHERE id=? AND active=1", (step["task_id"],)).fetchone()
                if not child:
                    raise ValueError("A follow-up was archived. Update the sequence before starting it.")
                db.execute("INSERT INTO task_occurrences(task_id,occurrence_key,source,run_id,position,delay_minutes,snapshot) VALUES (?,?,'sequence',?,?,?,?)",
                           (child["id"], f"R{run_id}:{position}", run_id, position, step["delay"], _snapshot(child, task)))
            advance(run_id, 0, now)
    return True


def _display_row(occurrence, d=None):
    d = d or periods.today()
    task = occurrence_task(occurrence)
    c = completion(occurrence)
    ready = local(occurrence["ready_at"])
    due_at = local(occurrence["due_at"])
    scheduled = date.fromisoformat(occurrence["scheduled_date"]) if occurrence["scheduled_date"] else None
    due = None
    if due_at:
        label = "Due today" if due_at.date() == d else "Due " + due_at.strftime("%a %b ") + str(due_at.day)
        if due_at.astimezone(timezone.utc) < now_utc():
            label = "From " + due_at.strftime("%a %b ") + str(due_at.day)
        due = {"date": due_at.date(), "label": label, "overdue": due_at.astimezone(timezone.utc) < now_utc()}
    return {"task": task, "done": bool(c), "completion": c, "period_key": occurrence["occurrence_key"],
            "occurrence_id": occurrence["id"], "run_id": occurrence["run_id"], "run_name": occurrence["run_name"],
            "source": occurrence["source"], "position": occurrence["position"], "scheduled_date": scheduled,
            "waiting": bool(ready and ready.astimezone(timezone.utc) > now_utc()), "blocked": ready is None,
            "ready_at": ready, "due": due, "last_on": None, "cancelled": occurrence["state"] != "open" or occurrence["run_state"] == "cancelled",
            "skipped": occurrence["state"] == "skipped",
            "delay_minutes": occurrence["delay_minutes"]}


def rows(owners, d=None):
    ensure_selected(d)
    d = d or periods.today()
    results = get_db().execute(
        "SELECT o.*,r.state AS run_state,r.name AS run_name,r.root_task_id FROM task_occurrences o"
        " JOIN tasks t ON t.id=o.task_id LEFT JOIN sequence_runs r ON r.id=o.run_id"
        " WHERE o.state='open' AND (r.state IS NULL OR r.state!='cancelled')"
        " AND (o.run_id IS NOT NULL OR t.active=1) AND (o.scheduled_date IS NULL OR o.scheduled_date<=?)"
        " ORDER BY o.ready_at,o.id", ((periods.next_month(d) + timedelta(days=7)).isoformat(),)).fetchall()
    out = []
    for occurrence in results:
        if occurrence_task(occurrence)["owner"] not in owners:
            continue
        c = completion(occurrence)
        if c and c["done_on"] < periods.month_start(d).isoformat() and occurrence["run_state"] != "active":
            continue
        out.append(_display_row(occurrence, d))
    return out


def run_rows(run_id):
    results = get_db().execute("SELECT o.*,r.state AS run_state,r.name AS run_name,r.root_task_id FROM task_occurrences o"
                               " JOIN sequence_runs r ON r.id=o.run_id WHERE o.run_id=? ORDER BY o.position", (run_id,)).fetchall()
    return [_display_row(row) for row in results]


def skip(user, occurrence):
    from .logic import can_complete
    with get_db():
        get_db().execute("UPDATE task_occurrences SET state=state WHERE id=?", (occurrence["id"],))
        occurrence = get_occurrence(occurrence["id"])
        if occurrence["run_id"] or occurrence["source"] != "selected":
            raise ValueError("Cancel the sequence run to stop its remaining steps.")
        if not can_complete(user, occurrence_task(occurrence)):
            raise PermissionError("That task belongs to someone else.")
        if completion(occurrence):
            raise ValueError("Undo this completion before skipping it.")
        get_db().execute("UPDATE task_occurrences SET state='skipped' WHERE id=?", (occurrence["id"],))


def reschedule(user, occurrence, minutes):
    """Adjust just this run's next ready time, leaving the reusable recipe alone."""
    from .logic import can_complete
    if not 0 <= minutes <= 525600:
        raise ValueError("Choose 0 to 525600 minutes from now.")
    with get_db():
        get_db().execute("UPDATE task_occurrences SET state=state WHERE id=?", (occurrence["id"],))
        occurrence = get_occurrence(occurrence["id"])
        if not can_complete(user, occurrence_task(occurrence)):
            raise PermissionError("That task belongs to someone else.")
        if (not occurrence["run_id"] or not occurrence["position"] or not occurrence["ready_at"]
                or occurrence["state"] != "open" or occurrence["run_state"] != "active" or completion(occurrence)):
            raise ValueError("Only an unfinished next step can have its ready time changed.")
        ready = now_utc() + timedelta(minutes=minutes)
        get_db().execute("UPDATE task_occurrences SET ready_at=?,scheduled_date=?,activation=activation+1 WHERE id=?",
                         (stamp(ready), ready.astimezone(periods.tz()).date().isoformat(), occurrence["id"]))


def cancel_run(user, run_id):
    run = get_db().execute("SELECT * FROM sequence_runs WHERE id=?", (run_id,)).fetchone()
    root = get_db().execute("SELECT * FROM task_occurrences WHERE run_id=? AND position=0", (run_id,)).fetchone()
    if not run or not root:
        raise ValueError("This sequence run could not be found.")
    owner = occurrence_task(root)["owner"]
    if user["role"] != "member" or owner not in (user["username"], "shared", "volunteer"):
        raise PermissionError("Only the assigned person can cancel this sequence run.")
    with get_db():
        get_db().execute("UPDATE sequence_runs SET state='cancelled' WHERE id=?", (run_id,))
        get_db().execute("UPDATE task_occurrences SET state='cancelled' WHERE run_id=? AND NOT EXISTS"
                         " (SELECT 1 FROM completions c WHERE c.task_id=task_occurrences.task_id AND c.period_key=task_occurrences.occurrence_key)", (run_id,))


def send_ready_notifications(now=None):
    """Use the existing minute job. No browser timers or in-memory queues."""
    from . import push
    db = get_db()
    now = now or now_utc()
    sent = 0
    for o in db.execute("SELECT o.*,r.root_task_id FROM task_occurrences o JOIN sequence_runs r ON r.id=o.run_id"
                         " WHERE r.state='active' AND o.state='open' AND o.position>0 AND o.ready_at IS NOT NULL").fetchall():
        ready = datetime.fromisoformat(o["ready_at"])
        if ready > now or now - ready > timedelta(hours=2) or completion(o):
            continue
        task = occurrence_task(o)
        for member in db.execute("SELECT u.id,u.username FROM sequence_subscriptions s JOIN users u ON u.id=s.user_id"
                                 " WHERE s.root_task_id=? AND u.role='member'", (o["root_task_id"],)).fetchall():
            root = db.execute("SELECT * FROM task_occurrences WHERE run_id=? AND position=0", (o["run_id"],)).fetchone()
            doer = completion(root) if root else None
            volunteered = task["owner"] == "volunteer" and doer and doer["user_id"] == member["id"]
            if task["owner"] not in (member["username"], "shared") and not volunteered:
                continue
            subs = push.subscriptions_for(member["id"])
            if not subs:
                continue
            claimed = db.execute("INSERT OR IGNORE INTO sequence_notice_log VALUES (?,?,?,?)",
                                 (o["id"], member["id"], o["activation"], stamp(now))).rowcount
            db.commit()
            if not claimed:
                continue
            result = push.deliver(subs, {"title": task["name"], "body": "Ready when you are. The next step is waiting for you.",
                                         "url": f"/occurrences/{o['id']}", "tag": f"occurrence-{o['id']}-{o['activation']}"})
            if result["ok"]:
                sent += 1
            elif result["error"]:
                db.execute("DELETE FROM sequence_notice_log WHERE occurrence_id=? AND user_id=? AND activation=?",
                           (o["id"], member["id"], o["activation"]))
                db.commit()
    return sent
