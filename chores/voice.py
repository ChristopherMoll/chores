"""Voice commands from the Siri Shortcut: one spoken sentence in, one short spoken reply out.

Kept free of Flask request objects so it can be tested directly. Replies are short because
they're heard on a phone, a watch or in the car. Nothing here ever guesses between two
tasks or claims a Needs Volunteer task. Crossing off another member's task ("stealing" it)
is allowed, and that member gets a push notification.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from . import logic, periods, scheduling
from .db import get_db

MAX_PHRASE = 300
MATCH_THRESHOLD = 0.5   # how much of a task's name has to be heard
AMBIGUOUS_GAP = 0.15    # a runner-up this close means "did you mean A or B?"
UNDO_WINDOW = timedelta(hours=1)
MISSES_KEPT_DAYS = 30

# Words that carry no meaning for picking a task.
FILLER = frozenset("""
    a all already am an and are as at away be been chore chores complete completed cross crossed did do
    done finish finished for got handled have has i im ive is it just made make mark my of off our out
    please put some take taken task tasks that the this to today took up we with
""".split())

UNDO = re.compile(r"\b(undo|take (that|it) back|scratch that|oops|cancel that)\b")
LEFT = re.compile(r"\b(what|whats|anything|how many)\b.*\b(left|next|to do|remaining)\b"
                  r"|\b(whats next|what should i do)\b")
SCORE = re.compile(r"\b(score|points|streak|how am i doing)\b")


# ---------------------------------------------------------------- keys ---

def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(user_id, label):
    """Make a new key and return it. This is the only time the key itself exists."""
    raw = "mole_" + secrets.token_urlsafe(32)
    db = get_db()
    db.execute("INSERT INTO api_tokens(user_id, label, token_hash) VALUES (?, ?, ?)",
               (user_id, (label.strip() or "iPhone")[:40], _hash(raw)))
    db.commit()
    return raw


def user_for_token(raw):
    """The member a key belongs to, or None. Admin accounts never get voice access."""
    if not raw or len(raw) > 200:
        return None
    db = get_db()
    row = db.execute("SELECT t.id AS token_id, u.* FROM api_tokens t JOIN users u ON u.id = t.user_id"
                     " WHERE t.token_hash = ? AND u.role = 'member'", (_hash(raw),)).fetchone()
    if row:
        db.execute("UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?", (row["token_id"],))
        db.commit()
    return row


def tokens_for(user_id):
    return get_db().execute("SELECT id, label, created_at, last_used_at FROM api_tokens WHERE user_id = ?"
                            " ORDER BY id DESC", (user_id,)).fetchall()


def revoke(user_id, token_id):
    db = get_db()
    gone = db.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id)).rowcount
    db.commit()
    return bool(gone)


def recent_misses(limit=50):
    return get_db().execute(
        "SELECT m.phrase, m.heard_at, u.display_name FROM voice_misses m JOIN users u ON u.id = m.user_id"
        " ORDER BY m.id DESC LIMIT ?", (limit,)).fetchall()


def _record_miss(user_id, phrase):
    db = get_db()
    db.execute("DELETE FROM voice_misses WHERE heard_at < datetime('now', ?)", (f"-{MISSES_KEPT_DAYS} days",))
    db.execute("INSERT INTO voice_misses(user_id, phrase) VALUES (?, ?)", (user_id, phrase))
    db.commit()


# ------------------------------------------------------------- matching ---

def normalize(text):
    text = text.casefold().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if suffix in ("ing", "ed") and len(word) > 3 and word[-1] == word[-2]:
                word = word[:-1]   # mopped -> mop, scrubbing -> scrub
            return word
    return word


def _words(text):
    return [_stem(w) for w in normalize(text).split() if w not in FILLER]


def _closeness(a, b):
    """1 for the same word, partial credit for a near miss such as dictation hearing "watch" for "wash"."""
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 3:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.8 and min(len(a), len(b)) >= 4:
        return 1.0
    return ratio if ratio >= 0.6 else 0.0


def _name_word_credit(heard, wanted):
    return [max(_closeness(w, h) for h in heard) for w in wanted]


def _fully_heard(spoken, name):
    """How many words the task's name has, if every one of them was clearly heard; otherwise 0."""
    heard, wanted = _words(spoken), _words(name)
    if heard and wanted and all(c == 1.0 for c in _name_word_credit(heard, wanted)):
        return len(wanted)
    return 0


def _most_specific(phrase, tasks):
    """'wash laundry' means Wash Laundry, not Laundry: the longest name heard in full wins outright.

    Only when its name is longer than every other candidate's, so a mishearing like "watch laundry"
    still asks rather than settling for the shorter name.
    """
    lengths = sorted(((len(_words(t["name"])), _fully_heard(phrase, t["name"]), t) for t in tasks),
                     key=lambda x: x[0], reverse=True)
    (top_len, top_full, top), runner_up = lengths[0], lengths[1]
    return top if top_full and top_len > runner_up[0] else None


def score(spoken, name):
    """0..1: mostly how much of the task's name was heard, a little for how few words were left over.

    At least one word must be a clear match; near misses only add to that, so they can
    separate two similar tasks but never pick a task on their own.
    """
    heard, wanted = _words(spoken), _words(name)
    if not heard or not wanted:
        return 0.0
    best_wanted = _name_word_credit(heard, wanted)
    if not any(c == 1.0 for c in best_wanted):
        return 0.0
    best_heard = [max(_closeness(h, w) for w in wanted) for h in heard]
    return 0.7 * sum(best_wanted) / len(wanted) + 0.3 * sum(best_heard) / len(heard)


def _spoken_list(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


# ------------------------------------------------------------- commands ---

def handle(user, text):
    """Act on one sentence and return {"ok", "action", "speech"} for the Shortcut to read out."""
    phrase = " ".join(str(text or "").split())[:MAX_PHRASE]
    said = normalize(phrase)
    if not said:
        return _reply(False, "none", "I didn't catch that. Try saying the chore's name.")
    if UNDO.search(said):
        return _undo(user)
    if SCORE.search(said):
        return _score(user)
    if LEFT.search(said):
        return _left(user)
    return _complete(user, phrase)


def _reply(ok, action, speech, **extra):
    return dict(ok=ok, action=action, speech=speech, **extra)


def _complete(user, phrase):
    tasks = get_db().execute("SELECT * FROM tasks WHERE active = 1").fetchall()
    ranked = sorted(((score(phrase, t["name"]), t) for t in tasks), key=lambda st: st[0], reverse=True)
    ranked = [(s, t) for s, t in ranked if s >= MATCH_THRESHOLD]
    mine = [(s, t) for s, t in ranked if t["owner"] in (user["username"], logic.SHARED)]
    pool = mine or ranked   # your own tasks win; otherwise anyone's
    if pool:
        close = [t for s, t in pool if s >= pool[0][0] - AMBIGUOUS_GAP]
        if len(close) > 1 and (specific := _most_specific(phrase, close)):
            close = [specific]
        if len(close) > 1:
            options = [t["name"] for t in close[:3]]
            _record_miss(user["id"], f"{phrase} (asked: {_spoken_list(options)})")
            return _reply(False, "ambiguous", f"I heard “{phrase}”. Did you mean {_spoken_list(options)}? "
                          "Say the full name and I'll cross it off.", options=options, heard=phrase)
        task = close[0]
        if task["owner"] == logic.VOLUNTEER:
            return _reply(False, "volunteer", f"{task['name']} is up for grabs. Claim it in the app first.")
        return _cross_off(user, task)
    _record_miss(user["id"], phrase)
    return _reply(False, "no_match", f"I couldn't find a chore called {phrase}. Try its name from your list.")


def _completion(task):
    occurrence = scheduling.resolve(task)
    if occurrence:
        return scheduling.completion(occurrence)
    return get_db().execute(
        "SELECT c.*, u.display_name AS doer FROM completions c JOIN users u ON u.id = c.user_id"
        " WHERE c.task_id = ? AND c.period_key = ?", (task["id"], periods.period_key(task["frequency"]))).fetchone()


def _cross_off(user, task):
    try:
        occurrence = scheduling.resolve(task)
        if task["frequency"] not in ("selected", "sequence"):
            choices = scheduling.candidates(task["id"])
            if len(choices) > 1 or (choices and choices[0]["occurrence_key"] != periods.period_key(task["frequency"]) and not _completion(task)):
                raise ValueError("More than one load could match. Choose the run in the app.")
            if choices:
                occurrence = scheduling.get_occurrence(choices[0]["id"])
        before = scheduling.completion(occurrence) if occurrence else _completion(task)
    except ValueError as e:
        return _reply(False, "choose_occurrence", str(e))
    if before:
        who = "You already" if before["user_id"] == user["id"] else f"{before['doer']} already"
        return _reply(True, "already_done", f"{who} crossed off {task['name']}.", task=task["name"])
    effective_task = scheduling.occurrence_task(occurrence) if occurrence else task
    owner = _other_owner(user, effective_task)
    # Explicit state: a retried request can't undo it.
    try:
        logic.toggle(user, task, desired_done=True, steal=owner is not None, occurrence_id=occurrence["id"] if occurrence else None)
    except (ValueError, PermissionError) as e:
        return _reply(False, "not_ready", str(e))
    after = scheduling.completion(occurrence) if occurrence else _completion(task)
    if after and after["user_id"] != user["id"]:   # someone else got there at the same moment
        return _reply(True, "already_done", f"{after['doer']} already crossed off {task['name']}.", task=task["name"])
    points = after["points"] if after else 0
    left = len(logic.task_views(logic.dashboard(user["username"]))["today"]["open"])
    tail = "That's everything for today." if not left else f"{left} left today."
    head = f"Crossed off {task['name']}."
    active = scheduling.get_occurrence(occurrence["id"]) if occurrence else scheduling.resolve(task)
    if active and active["run_id"]:
        following = get_db().execute("SELECT * FROM task_occurrences WHERE run_id=? AND position=?", (active["run_id"], active["position"] + 1)).fetchone()
        if following and following["ready_at"]:
            at = scheduling.local(following["ready_at"])
            tail += " " + scheduling.occurrence_task(following)["name"] + " will be ready " + at.strftime('%A ') + periods.format_time(at.time()) + "."
    if owner:
        sent = _notify(owner, task, "Task stolen!", f"{user['display_name']} crossed off {task['name']} for you.")
        head = (f"Crossed off {task['name']} for {owner['display_name']}, and I've let {owner['display_name']} know."
                if sent else f"Crossed off {task['name']} for {owner['display_name']}. I couldn't send "
                f"{owner['display_name']} a notification, so you might mention it.")
    return _reply(True, "stolen" if owner else "completed",
                  f"{head} {points} point{'s' if points != 1 else ''}. {tail}",
                  task=task["name"], points=points, notified=bool(owner and sent))


def _other_owner(user, task):
    """The member who owns this task, when it isn't the speaker's own or Shared."""
    if task["owner"] in (user["username"], logic.SHARED, logic.VOLUNTEER):
        return None
    return logic.user_by_name(task["owner"])


def _notify(owner, task, title, body):
    """Push to every device the owner has set up. A failure never undoes the cross-off."""
    from . import push   # imports pywebpush only when someone is actually told
    try:
        subs = push.subscriptions_for(owner["id"])
        return bool(subs) and push.deliver(subs, {"title": title, "body": body, "url": f"/tasks/{task['id']}",
                                                  "tag": f"task-{task['id']}"})["ok"] > 0
    except Exception as e:   # noqa: BLE001 - the task is already crossed off; just report it
        print(f"voice: could not notify {owner['username']}: {e}", flush=True)
        return False


def _undo(user):
    row = get_db().execute(
        "SELECT c.done_at, c.period_key, o.id AS occurrence_id, t.* FROM completions c JOIN tasks t ON t.id = c.task_id"
        " LEFT JOIN task_occurrences o ON o.task_id=c.task_id AND o.occurrence_key=c.period_key"
        " WHERE c.user_id = ? ORDER BY c.done_at DESC, c.id DESC LIMIT 1", (user["id"],)).fetchone()
    recent = row and (scheduling.now_utc() if row["occurrence_id"] else datetime.now(timezone.utc)) - datetime.fromisoformat(row["done_at"]) <= UNDO_WINDOW
    if not recent or (not row["occurrence_id"] and row["period_key"] != periods.period_key(row["frequency"])):
        return _reply(False, "nothing_to_undo", "There's nothing from the last hour to undo.")
    try:
        logic.toggle(user, row, desired_done=False, occurrence_id=row["occurrence_id"])
    except (ValueError, PermissionError) as e:
        return _reply(False, "cannot_undo", str(e))
    owner = _other_owner(user, row)
    if owner:   # they were told it was stolen, so tell them it's theirs again
        sent = _notify(owner, row, "Never mind", f"{user['display_name']} put {row['name']} back on your list.")
        told = f" I've let {owner['display_name']} know." if sent else ""
        return _reply(True, "undone", f"Okay, {row['name']} is back on {owner['display_name']}'s list.{told}",
                      task=row["name"])
    return _reply(True, "undone", f"Okay, {row['name']} is back on your list.", task=row["name"])


MAX_SPOKEN_TASKS = 10   # past this, a list is too long to follow in the car


def _and_list(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _left(user):
    """Today's open list, as the app shows it: daily chores, then anything due today, then overdue."""
    d = periods.today()
    open_ = logic.task_views(logic.dashboard(user["username"], d), d)["today"]["open"]
    if not open_:
        upcoming = logic.task_views(logic.dashboard(user["username"], d), d)["today"]["upcoming"]
        timed = [row for row in upcoming if row.get("ready_at")]
        if timed:
            first = min(timed, key=lambda row: row["ready_at"])
            return _reply(True, "left", "Nothing ready right now. " + first["task"]["name"] + " will be ready " + first["ready_at"].strftime("%A ") + periods.format_time(first["ready_at"].time()) + ".", count=0, daily=[], due_today=[], overdue=[])
        return _reply(True, "left", "Nothing left today. Nice work.", count=0, daily=[], due_today=[], overdue=[])
    groups = {"daily": [], "due_today": [], "overdue": []}
    for r in open_:
        task = r["task"]
        if task["frequency"] == "daily":
            groups["daily"].append(task["name"])
        else:
            due = r["due"]["date"] if r.get("due") else r.get("scheduled_date") or periods.due_date(task, d)
            groups["overdue" if due and due < d else "due_today"].append(task["name"])
    parts, budget = [], MAX_SPOKEN_TASKS
    for key, label in (("daily", "Daily"), ("due_today", "Due today"), ("overdue", "Overdue")):
        names = groups[key][:budget]
        budget -= len(names)
        if names:
            parts.append(f"{label}: {_and_list(names)}.")
    more = len(open_) - MAX_SPOKEN_TASKS
    if more > 0:
        parts.append(f"And {more} more in the app.")
    return _reply(True, "left", f"{len(open_)} left today. " + " ".join(parts), count=len(open_), **groups)


def _score(user):
    week = logic.points_in(user["id"], *periods.range_for("week"))
    days = logic.streak(user["username"])
    streak = f" and a {days}-day streak" if days else ""
    return _reply(True, "score", f"You have {week['points']} points this week{streak}.", points=week["points"])
