"""Thank-yous: give someone the credit for a task they did, and tell them you noticed."""
import json
from datetime import timedelta

from flask import g

from . import logic, scheduling
from .db import get_db

KEEP_DAYS = 14   # an unread thank-you older than this quietly leaves the dashboard


def helpers(user):
    """The other members of the household: the people who might have done one of your tasks."""
    if "members" not in g:
        g.members = logic.members()
    return [m for m in g.members if m["id"] != user["id"]]


def can_credit(user, task):
    """A member may record that someone else did a task from their own list (theirs or Shared)."""
    return user["role"] == "member" and bool(task["active"]) and task["owner"] in (user["username"], logic.SHARED)


def _completion(completion_id):
    return get_db().execute(
        "SELECT c.*, t.name AS task_name, u.username AS doer_username, u.display_name AS doer, u.role AS doer_role,"
        " th.from_user_id, th.logged, f.display_name AS from_name"
        " FROM completions c JOIN tasks t ON t.id = c.task_id JOIN users u ON u.id = c.user_id"
        " LEFT JOIN thanks th ON th.completion_id = c.id LEFT JOIN users f ON f.id = th.from_user_id"
        " WHERE c.id = ?", (completion_id,)).fetchone()


def _thankable(user, c):
    """Someone else's work on a task that wasn't only theirs to do."""
    return (user["role"] == "member" and c["doer_role"] == "member" and c["user_id"] != user["id"]
            and c["owner_at"] != c["doer_username"])


def state(completion, viewer):
    """What a finished task's row shows this viewer: who said thanks, or whether they still can."""
    c = _completion(completion["id"])
    if not c:
        return {}
    return {"from_user_id": c["from_user_id"], "from_name": c["from_name"],
            "logged_by_viewer": bool(c["logged"]) and c["from_user_id"] == viewer["id"],
            "can_thank": c["from_user_id"] is None and _thankable(viewer, c)}


def logged_by(user, completion_id):
    """True when `user` recorded this completion on the doer's behalf, so they may also take it back."""
    return bool(get_db().execute("SELECT 1 FROM thanks WHERE completion_id = ? AND from_user_id = ? AND logged = 1",
                                 (completion_id, user["id"])).fetchone())


def _task_name(c):
    """A sequence step keeps the name it had when its run started."""
    row = get_db().execute("SELECT snapshot FROM task_occurrences WHERE task_id = ? AND occurrence_key = ?",
                           (c["task_id"], c["period_key"])).fetchone()
    return json.loads(row["snapshot"]).get("name", c["task_name"]) if row and row["snapshot"] else c["task_name"]


def give(sender, completion_id, *, logged=False):
    """One thank-you per completion, pushed to the person who did the work.

    Returns {name, new, sent}. A push that can't be delivered still leaves the
    thank-you waiting on their dashboard.
    """
    c = _completion(completion_id)
    if not c or not _thankable(sender, c):
        raise PermissionError("That isn't something you can send a thank-you for.")
    db = get_db()
    new = db.execute("INSERT OR IGNORE INTO thanks(completion_id, from_user_id, logged, created_at) VALUES (?, ?, ?, ?)",
                     (c["id"], sender["id"], int(logged), scheduling.stamp(scheduling.now_utc()))).rowcount
    db.commit()
    return {"name": c["doer"], "new": bool(new), "sent": bool(new) and _push(sender, c)}


def confirmation(result):
    """What the sender is told, in the same words wherever it's shown."""
    if not result["new"]:
        return f"{result['name']} has already been thanked for this."
    if result["sent"]:
        return f"Thank-you sent to {result['name']}."
    return f"Saved. {result['name']} will see your thank-you in the app."


def _push(sender, c):
    from . import push   # imports pywebpush only when there is something to send
    try:
        subs = push.subscriptions_for(c["user_id"])
        payload = {"title": "Thank you", "url": "/", "tag": f"thanks-{c['id']}",
                   "body": f"{sender['display_name']} says thank you for taking care of {_task_name(c)}."}
        return bool(subs) and push.deliver(subs, payload)["ok"] > 0
    except Exception as e:   # noqa: BLE001 - the thank-you is saved; it just waits in the app
        print(f"thanks: could not notify {c['doer_username']}: {e}", flush=True)
        return False


def pending(user):
    """Unread thank-yous for this person, one sentence per sender."""
    since = scheduling.stamp(scheduling.now_utc() - timedelta(days=KEEP_DAYS))
    rows = get_db().execute(
        "SELECT c.task_id, c.period_key, t.name AS task_name, f.display_name AS from_name"
        " FROM thanks th JOIN completions c ON c.id = th.completion_id JOIN tasks t ON t.id = c.task_id"
        " JOIN users f ON f.id = th.from_user_id"
        " WHERE c.user_id = ? AND th.seen_at IS NULL AND th.created_at >= ? ORDER BY th.created_at, c.id",
        (user["id"], since)).fetchall()
    by_sender = {}
    for row in rows:
        by_sender.setdefault(row["from_name"], []).append(_task_name(row))
    notes = []
    for sender, names in by_sender.items():
        listed = names[:3] + ([f"{len(names) - 3} more"] if len(names) > 3 else [])
        joined = listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]
        notes.append(f"{sender} says thank you for taking care of {joined}.")
    return notes


def mark_seen(user):
    db = get_db()
    db.execute("UPDATE thanks SET seen_at = ? WHERE seen_at IS NULL AND completion_id IN"
               " (SELECT id FROM completions WHERE user_id = ?)", (scheduling.stamp(scheduling.now_utc()), user["id"]))
    db.commit()
