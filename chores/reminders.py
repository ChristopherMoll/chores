"""'Nag me about this': when each reminder is due to fire, and sending the ones that are."""
from datetime import datetime, time, timedelta, timezone

from . import periods, push, scheduling, planning
from .db import get_db

UNIT_MINUTES = {"minutes": 1, "hours": 60, "days": 1440}
MAX_AMOUNT = 365
# If the Pi was off when a reminder came due, still send it if it's this recent.
GRACE = timedelta(hours=2)


# ------------------------------------------------------------------ CRUD ---

def can_remind(task):
    """Reminders count back from a due date, so the task has to have one."""
    return task["frequency"] in periods.RECURRING or bool(task["frequency"] == "once" and task["due_date"])


def parse(form, task):
    """Validate the 'nag me' form. Returns (amount, unit, at_time) or raises ValueError."""
    try:
        amount = int(form.get("amount", ""))
    except ValueError:
        raise ValueError("How long before should that be? Enter a whole number.")
    unit = form.get("unit", "")
    if unit not in UNIT_MINUTES:
        raise ValueError("Pick minutes, hours or days.")
    if not 0 <= amount <= MAX_AMOUNT:
        raise ValueError(f"Pick a number between 0 and {MAX_AMOUNT}.")
    if unit != "days":
        return amount, unit, ""
    if task["frequency"] == "daily" and amount > 0:
        raise ValueError("A daily task is due every day, so use 0 days or switch to hours.")
    try:
        at_time = datetime.strptime(form.get("at_time", ""), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError("Pick a time of day for the reminder.")
    return amount, unit, at_time


def add(user_id, task_id, amount, unit, at_time, created=None):
    """Returns True if a new reminder was added, False if this one already existed."""
    db = get_db()
    created = (created or periods.now()).isoformat(timespec="minutes")
    cur = db.execute(
        "INSERT OR IGNORE INTO reminders(user_id, task_id, amount, unit, at_time, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)", (user_id, task_id, amount, unit, at_time, created))
    db.commit()
    return cur.rowcount > 0


def delete(user_id, reminder_id):
    """Removes the reminder and returns its task id, or None if it isn't this user's."""
    db = get_db()
    row = db.execute("SELECT task_id FROM reminders WHERE id = ? AND user_id = ?",
                     (reminder_id, user_id)).fetchone()
    if row is None:
        return None
    db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    db.commit()
    return row["task_id"]


def for_task(user_id, task_id):
    rows = get_db().execute(
        "SELECT * FROM reminders WHERE user_id = ? AND task_id = ? ORDER BY unit = 'days' DESC, amount DESC",
        (user_id, task_id)).fetchall()
    return [dict(r, text=describe(r)) for r in rows]


def task_ids_for(user_id):
    return {r["task_id"] for r in get_db().execute("SELECT DISTINCT task_id FROM reminders WHERE user_id = ?",
                                                   (user_id,))}


def describe(rem):
    amount, unit = rem["amount"], rem["unit"]
    if unit == "days":
        when = "On the due day" if amount == 0 else f"{amount} day{'s' if amount != 1 else ''} before"
        return f"{when} at {periods.format_time(rem['at_time'])}"
    if amount == 0:
        return "When it's due"
    return f"{amount} {unit[:-1] if amount == 1 else unit} before"


# ------------------------------------------------------------ scheduling ---

def fire_at(rem, task, now):
    """When this reminder fires for the task's current occurrence, or None.

    Never earlier than the start of the task's current period: before that,
    last period's cross-off still stands and this one can't be crossed off yet.
    A "1 day before" on a task due on the first day of its period therefore
    fires on that first day.
    """
    d = now.date()
    due = periods.due_date(task, d)
    if due is None:
        return None
    if rem["unit"] == "days":
        at = time.fromisoformat(rem["at_time"])
        fire = datetime.combine(due - timedelta(days=rem["amount"]), at)
    else:
        at = time.min
        fire = periods.due_at(task, d) - timedelta(minutes=rem["amount"] * UNIT_MINUTES[rem["unit"]])
    start = periods.period_start(task["frequency"], d)
    if start and fire.date() < start:
        fire = datetime.combine(start, at)
    return fire


def occurrence(task, d):
    """Identifies what's being nagged about. A one-off whose date moves can nag again."""
    key = periods.task_key(task, d)
    return f"{key}:{task['due_date']}" if task["frequency"] == "once" else key


def _is_done(task, d):
    key = periods.task_key(task, d)
    return bool(get_db().execute("SELECT 1 FROM completions WHERE task_id=? AND period_key=?"
                                 " UNION ALL SELECT 1 FROM task_skips WHERE task_id=? AND period_key=?",
                                 (task["id"], key, task["id"], key)).fetchone())


def _payload(task, now):
    info = periods.due_info(task, now, explicit_only=False)
    return {"title": f"⏰ {task['name']}", "body": f"Not done yet. {info['label']}.",
            "url": f"/tasks/{task['id']}", "tag": f"task-{task['id']}"}


def run_due(now=None):
    """Send every reminder that's due now. Called once a minute by `manage.py notify`.

    Returns the number of reminders that reached at least one device.
    """
    now = now or periods.now()
    d = now.date()
    scheduling.ensure_selected(d)
    db = get_db()
    tasks = {t["id"]: t for t in db.execute("SELECT * FROM tasks WHERE active = 1")}
    now_utc = now.replace(tzinfo=periods.tz()).astimezone(timezone.utc)
    sent = scheduling.send_ready_notifications(now_utc)
    for rem in db.execute("SELECT * FROM reminders").fetchall():
        if planning.is_away(rem["user_id"], d):
            continue
        task = tasks.get(rem["task_id"])
        if task is None:
            continue
        if task["frequency"] == "selected":
            sent += _selected_reminders(rem, task, now_utc)
            continue
        fire = fire_at(rem, task, now)
        if fire is None or fire > now or now - fire > GRACE:
            continue
        if planning.is_away(rem["user_id"], fire.date()):
            continue
        if fire < datetime.fromisoformat(rem["created_at"]) or _is_done(task, d):
            continue
        subs = push.subscriptions_for(rem["user_id"])
        if not subs:
            continue
        occ = occurrence(task, d)
        # Claim before sending, so overlapping runs can't both nag.
        claimed = db.execute("INSERT OR IGNORE INTO reminder_log(reminder_id, occurrence, sent_at) VALUES (?, ?, ?)",
                             (rem["id"], occ, now.isoformat(timespec="seconds"))).rowcount
        db.commit()
        if not claimed:
            continue
        results = push.deliver(subs, _payload(task, now))
        if results["ok"]:
            sent += 1
        elif results["error"]:    # nothing got through; let the next run try again
            db.execute("DELETE FROM reminder_log WHERE reminder_id = ? AND occurrence = ?", (rem["id"], occ))
            db.commit()
    return sent + send_evening_summary(now)


def _selected_reminders(rem, task, now_utc):
    db = get_db()
    sent = 0
    for occurrence in db.execute("SELECT * FROM task_occurrences WHERE task_id=? AND source='selected' AND state='open'",
                                 (task["id"],)).fetchall():
        if not occurrence["due_at"] or scheduling.completion(occurrence):
            continue
        due = scheduling.local(occurrence["due_at"])
        if rem["unit"] == "days":
            fire = datetime.combine(due.date() - timedelta(days=rem["amount"]), time.fromisoformat(rem["at_time"]), periods.tz()).astimezone(timezone.utc)
        else:
            fire = due.astimezone(timezone.utc) - timedelta(minutes=rem["amount"] * UNIT_MINUTES[rem["unit"]])
        created = datetime.fromisoformat(rem["created_at"]).replace(tzinfo=periods.tz()).astimezone(timezone.utc)
        if fire > now_utc or now_utc - fire > GRACE or fire < created:
            continue
        if planning.is_away(rem["user_id"], fire.astimezone(periods.tz()).date()):
            continue
        subs = push.subscriptions_for(rem["user_id"])
        if not subs:
            continue
        claimed = db.execute("INSERT OR IGNORE INTO reminder_log VALUES (?,?,?)",
                             (rem["id"], occurrence["occurrence_key"], scheduling.stamp(now_utc))).rowcount
        db.commit()
        if not claimed:
            continue
        result = push.deliver(subs, {"title": task["name"], "body": "A little reminder for " + due.strftime("%A, %B ") + str(due.day) + ".",
                                    "url": f"/occurrences/{occurrence['id']}", "tag": f"occurrence-{occurrence['id']}"})
        if result["ok"]:
            sent += 1
        elif result["error"]:
            db.execute("DELETE FROM reminder_log WHERE reminder_id=? AND occurrence=?", (rem["id"], occurrence["occurrence_key"]))
            db.commit()
    return sent


def send_evening_summary(now):
    from . import logic
    db, sent = get_db(), 0
    for pref in db.execute("SELECT p.*,u.username FROM evening_preferences p JOIN users u ON u.id=p.user_id WHERE u.role='member'").fetchall():
        fire = datetime.combine(now.date(), time.fromisoformat(pref["at_time"]))
        if fire > now or now - fire > GRACE or planning.is_away(pref["user_id"], now.date()):
            continue
        count = len(logic.task_views(logic.dashboard(pref["username"], now.date()), now.date())["today"]["open"])
        subs = push.subscriptions_for(pref["user_id"])
        if not count or not subs:
            continue
        claimed = db.execute("INSERT OR IGNORE INTO evening_log VALUES (?,?)", (pref["user_id"], now.date().isoformat())).rowcount
        db.commit()
        if not claimed:
            continue
        result = push.deliver(subs, {"title": "A little evening check-in", "body": f"{count} task{'s are' if count != 1 else ' is'} still on your list today. A little at a time.",
                                     "url": "/", "tag": f"evening-{now.date()}"})
        if result["ok"]:
            sent += 1
        elif result["error"]:
            db.execute("DELETE FROM evening_log WHERE user_id=? AND local_date=?", (pref["user_id"], now.date().isoformat()))
            db.commit()
    return sent
