"""'Nag me about this': when each reminder is due to fire, and sending the ones that are."""
from datetime import datetime, time, timedelta

from . import periods, push
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
    key = periods.period_key(task["frequency"], d)
    return f"{key}:{task['due_date']}" if task["frequency"] == "once" else key


def _is_done(task, d):
    return get_db().execute("SELECT 1 FROM completions WHERE task_id = ? AND period_key = ?",
                            (task["id"], periods.period_key(task["frequency"], d))).fetchone() is not None


def _payload(task, now):
    info = periods.due_info(task, now, explicit_only=False)
    return {"title": f"⏰ {task['name']}", "body": f"Not done yet. {info['label']}.",
            "url": f"/#task-{task['id']}", "tag": f"task-{task['id']}"}


def run_due(now=None):
    """Send every reminder that's due now. Called once a minute by `manage.py notify`.

    Returns the number of reminders that reached at least one device.
    """
    now = now or periods.now()
    d = now.date()
    db = get_db()
    tasks = {t["id"]: t for t in db.execute("SELECT * FROM tasks WHERE active = 1")}
    sent = 0
    for rem in db.execute("SELECT * FROM reminders").fetchall():
        task = tasks.get(rem["task_id"])
        if task is None:
            continue
        fire = fire_at(rem, task, now)
        if fire is None or fire > now or now - fire > GRACE:
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
    return sent
