"""Siri voice commands: personal keys, the API boundary, and what each spoken phrase is allowed to do."""
import re
from collections import Counter
from unittest import mock

import pytest

from chores import db, voice
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task, subscribe, uid


def make_key(app, username):
    with app.app_context():
        uid = db.get_db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
        return voice.create_token(uid, "test phone")


def say(client, key, text):
    r = client.post("/api/v1/voice", json={"text": text}, headers={"Authorization": f"Bearer {key}"})
    return r.status_code, r.get_json()


def completions(app, code):
    with app.app_context():
        return db.get_db().execute("SELECT c.*, u.username FROM completions c JOIN users u ON u.id=c.user_id"
                                   " JOIN tasks t ON t.id=c.task_id WHERE t.code=?", (code,)).fetchall()


@pytest.fixture
def chris(app):
    return app.test_client(), make_key(app, "chris")


# ------------------------------------------------------------ boundary ---

def test_api_needs_a_valid_key_and_ignores_the_session_cookie(app):
    client = app.test_client()
    login(client, "chris")   # a logged-in browser session alone must not be enough
    for headers in ({}, {"Authorization": "Bearer mole_wrong"}, {"Authorization": "Basic abc"}):
        r = client.post("/api/v1/voice", json={"text": "dishes"}, headers=headers)
        assert r.status_code == 401
        assert "isn't connected" in r.get_json()["speech"]
    assert completions(app, "H02") == []


def test_keys_are_stored_hashed_and_revocation_takes_effect(app, chris):
    client, key = chris
    with app.app_context():
        stored = [r["token_hash"] for r in db.get_db().execute("SELECT token_hash FROM api_tokens")]
        assert key not in stored and len(stored) == 1
        token_id = db.get_db().execute("SELECT id FROM api_tokens").fetchone()["id"]
    assert say(client, key, "what's left")[0] == 200
    csrf = login(client, "chris")
    assert client.post(f"/voice/keys/{token_id}/delete", data={"csrf": csrf}).status_code == 302
    assert say(client, key, "what's left")[0] == 401


def test_other_people_cannot_remove_your_key(app, chris):
    with app.app_context():
        token_id = db.get_db().execute("SELECT id FROM api_tokens").fetchone()["id"]
    other = app.test_client()
    csrf = login(other, "andrea")
    assert other.post(f"/voice/keys/{token_id}/delete", data={"csrf": csrf}).status_code == 404


def test_admin_accounts_get_no_voice_access(app):
    client = app.test_client()
    csrf = login(client, "admin")
    assert client.post("/voice", data={"csrf": csrf}).status_code == 403
    assert say(app.test_client(), make_key(app, "admin"), "what's left")[0] == 401


def test_csrf_is_still_enforced_outside_the_api(app):
    client = app.test_client()
    login(client, "chris")
    assert client.post("/voice", data={}).status_code == 400


def test_setup_page_shows_a_new_key_once(app):
    client = app.test_client()
    csrf = login(client, "chris")
    created = client.post("/voice", data={"csrf": csrf, "label": "Chris's iPhone"}).get_data(as_text=True)
    key = re.search(r'value="(mole_[^"]+)"', created).group(1)
    assert say(app.test_client(), key, "what's left")[0] == 200
    again = client.get("/voice").get_data(as_text=True)
    assert key not in again and "Chris&#39;s iPhone" in again
    assert "<code>http://localhost/api/v1/voice</code>" in again and "main.voice_setup" not in again


def test_setup_page_shows_the_public_https_address_behind_the_proxy(app):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # what CHORES_BEHIND_PROXY=1 does
    client = app.test_client()
    login(client, "chris")
    page = client.get("/voice", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "chores.moll.casa"})
    assert "<code>https://chores.moll.casa/api/v1/voice</code>" in page.get_data(as_text=True)


def test_bad_bodies_get_a_spoken_error(app, chris):
    client, key = chris
    r = client.post("/api/v1/voice", data="nope", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 400 and r.get_json()["speech"]


# ------------------------------------------------------------- phrases ---

def test_crossing_off_is_safe_to_repeat(app, chris):
    client, key = chris
    status, body = say(client, key, "I did the dishes")
    assert status == 200 and body["action"] == "completed" and body["speech"].startswith("Crossed off Dishes.")
    assert say(client, key, "dishes")[1]["action"] == "already_done"
    assert [r["username"] for r in completions(app, "H02")] == ["chris"]


def test_shared_tasks_can_be_crossed_off(app, chris):
    client, key = chris
    assert say(client, key, "made weeknight dinner")[1]["action"] == "completed"
    assert len(completions(app, "H05")) == 1


def test_stealing_someone_elses_task_crosses_it_off_and_tells_them(app, chris):
    client, key = chris
    with app.app_context():
        subscribe("andrea")
    with mock.patch("chores.push.deliver", return_value=Counter(ok=1)) as deliver:
        body = say(client, key, "did the laundry")[1]
    assert body["action"] == "stolen" and body["notified"] is True
    assert body["speech"].startswith("Crossed off Laundry for Andrea, and I've let Andrea know.")
    rows = completions(app, "H01")
    assert [(r["username"], r["owner_at"]) for r in rows] == [("chris", "andrea")]   # the doer gets the points
    (subs, payload), _ = deliver.call_args
    with app.app_context():
        assert [s["user_id"] for s in subs] == [uid("andrea")]
    assert payload["title"] == "Task stolen!" and payload["body"] == "Chris crossed off Laundry for you."
    assert payload["url"] == f"/tasks/{task_id(app, 'H01')}"


def test_stealing_still_works_when_the_owner_has_no_notifications(app, chris):
    client, key = chris
    with mock.patch("chores.push.deliver") as deliver:
        body = say(client, key, "laundry")[1]
    deliver.assert_not_called()
    assert body["action"] == "stolen" and body["notified"] is False and "you might mention it" in body["speech"]
    assert len(completions(app, "H01")) == 1


def test_undoing_a_steal_tells_the_owner_again(app, chris):
    client, key = chris
    with app.app_context():
        subscribe("andrea")
    with mock.patch("chores.push.deliver", return_value=Counter(ok=1)) as deliver:
        say(client, key, "laundry")
        body = say(client, key, "undo")[1]
    assert body["speech"] == "Okay, Laundry is back on Andrea's list. I've let Andrea know."
    assert deliver.call_args[0][1]["title"] == "Never mind"
    assert completions(app, "H01") == []


def test_the_app_itself_still_blocks_other_peoples_tasks(app):
    client = app.test_client()
    csrf = login(client, "chris")
    r = client.post(f"/tasks/{task_id(app, 'H01')}/toggle", data={"done": "1"},
                    headers={"Accept": "application/json", "X-CSRF-Token": csrf})
    assert r.status_code == 403 and completions(app, "H01") == []


def test_volunteer_tasks_are_never_claimed_by_voice(app, chris):
    client, key = chris
    body = say(client, key, "took out the trash")[1]
    assert body["action"] == "volunteer" and "up for grabs" in body["speech"]
    assert completions(app, "H03") == []


def test_close_matches_ask_instead_of_guessing(app, chris):
    client, key = chris
    with app.app_context():
        set_task("H03", owner="chris", name="Water front plants")
        set_task("H04", owner="chris", name="Water back plants")
    body = say(client, key, "watered the plants")[1]
    assert body["action"] == "ambiguous" and set(body["options"]) == {"Water front plants", "Water back plants"}
    assert completions(app, "H03") == [] and completions(app, "H04") == []
    assert say(client, key, "water the back plants")[1]["task"] == "Water back plants"


def test_unmatched_phrases_are_kept_for_admins(app, chris):
    client, key = chris
    assert say(client, key, "fed the goldfish")[1]["action"] == "no_match"
    admin = app.test_client()
    login(admin, "admin")
    page = admin.get("/admin/").get_data(as_text=True)
    assert "fed the goldfish" in page


def test_undo_reverses_only_your_latest_cross_off(app, chris):
    client, key = chris
    say(client, key, "dishes")
    body = say(client, key, "undo that")[1]
    assert body["action"] == "undone" and "Dishes" in body["speech"]
    assert completions(app, "H02") == []
    assert say(client, key, "undo")[1]["action"] == "nothing_to_undo"


def test_whats_left_lists_daily_due_today_and_overdue(app, chris):
    client, key = chris
    with app.app_context():
        set_task("H03", owner="chris", frequency="weekly", due_weekday=2)           # Wednesday: today
        set_task("H04", owner="chris", frequency="once", due_date="2026-11-10")     # yesterday
        set_task("H06", owner="chris", frequency="once", due_date="2026-11-20")     # not yet
    body = say(client, key, "what chores do I have left today")[1]
    assert body["action"] == "left"
    assert body["daily"] == ["Dishes", "Weeknight Dinners"]
    assert body["due_today"] == ["Trash & Recycling"] and body["overdue"] == ["Groceries"]
    assert body["speech"] == ("4 left today. Daily: Dishes and Weeknight Dinners. "
                              "Due today: Trash & Recycling. Overdue: Groceries.")


def test_whats_left_keeps_long_lists_short(app, chris):
    client, key = chris
    with app.app_context():
        db.get_db().execute("UPDATE tasks SET owner='chris' WHERE frequency='daily' AND active=1")
        db.get_db().commit()
    body = say(client, key, "what's left")[1]
    assert body["count"] > voice.MAX_SPOKEN_TASKS
    assert body["speech"].endswith(f"And {body['count'] - voice.MAX_SPOKEN_TASKS} more in the app.")


def test_whats_left_and_score(app, chris):
    client, key = chris
    left = say(client, key, "what's left")[1]
    assert left["action"] == "left" and left["count"] >= 2 and "left today" in left["speech"]
    say(client, key, "dishes")
    assert say(client, key, "what's left")[1]["count"] == left["count"] - 1
    score = say(client, key, "what's my score")[1]
    assert score["action"] == "score" and score["points"] > 0


@pytest.mark.parametrize("spoken,name,expected", [
    ("did the dishes", "Dishes", True),
    ("took out the trash", "Trash & Recycling", True),
    ("kitchen reset", "Kitchen Reset", True),
    ("did the dishes", "Laundry", False),
    ("", "Dishes", False),
])
def test_score(spoken, name, expected):
    assert (voice.score(spoken, name) >= voice.MATCH_THRESHOLD) is expected
