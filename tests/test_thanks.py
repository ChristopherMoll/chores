"""Give your partner the credit for a task they did from your list, and thank them for it."""
from unittest import mock

from chores import db, push
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import subscribe, uid

JSON = {"Accept": "application/json"}
TODAY = "D2026-11-11"


def credit(client, token, tid, who="andrea", **extra):
    data = {"csrf": token, "done": "1", "credit": who, "period": TODAY, **extra}
    return client.post(f"/tasks/{tid}/toggle", data=data, headers=JSON)


def rows(app, sql, *args):
    with app.app_context():
        return db.get_db().execute(sql, args).fetchall()


def test_crediting_a_partner_gives_them_the_points_and_one_thank_you(app):
    chris = app.test_client(); token = login(chris, "chris")
    dishes = task_id(app, "H02")
    with app.app_context():
        subscribe("andrea")
        andrea = uid("andrea")
    page = chris.get("/").text
    assert "Andrea did this · send thanks" in page and 'name="credit" value="andrea"' in page
    with mock.patch.object(push, "send_one", return_value="ok") as sent:
        for _ in range(2):   # a retried request must not thank twice
            result = credit(chris, token, dishes).json
            assert result["ok"] and result["done"] and result["credited"] == "Andrea" and result["can_undo"]
        assert sent.call_count == 1
        payload = sent.call_args.args[1]
    assert payload["title"] == "Thank you" and payload["body"] == "Chris says thank you for taking care of Dishes."
    assert "Taken care of by Andrea" in result["tasks_html"] and "You said thank you." in result["tasks_html"]
    assert "Undo completion" in result["tasks_html"]
    done = rows(app, "SELECT * FROM completions WHERE task_id=?", dishes)
    assert [(c["user_id"], c["owner_at"]) for c in done] == [(andrea, "chris")]
    assert [(t["logged"], t["seen_at"]) for t in rows(app, "SELECT * FROM thanks")] == [(1, None)]

    # Andrea sees it in the app until she puts it away, and it stays on the finished task.
    hers = app.test_client(); her_token = login(hers, "andrea")
    assert "Chris says thank you for taking care of Dishes." in hers.get("/").text
    assert "Chris said thank you." in hers.get("/people/chris").text
    assert "says thank you" not in chris.get("/").text
    assert hers.post("/thanks/seen", data={"csrf": her_token}).status_code == 302
    assert "says thank you" not in hers.get("/").text

    # Chris recorded it, so Chris can take it back; the thank-you goes with it.
    undo = chris.post(f"/tasks/{dishes}/toggle", headers=JSON,
                      data={"csrf": token, "done": "0", "period": TODAY, "completion_id": done[0]["id"]})
    assert undo.status_code == 200 and not undo.json["done"]
    assert rows(app, "SELECT 1 FROM completions WHERE task_id=?", dishes) == [] and rows(app, "SELECT 1 FROM thanks") == []


def test_without_notifications_the_thank_you_waits_in_the_app(app):
    chris = app.test_client(); token = login(chris, "chris")
    dinners = task_id(app, "H05")   # a Shared task counts as "from my list" too
    result = credit(chris, token, dinners).json
    assert result["credited"] == "Andrea" and result["thanks_sent"] is False
    hers = app.test_client(); login(hers, "andrea")
    assert "Chris says thank you for taking care of" in hers.get("/").text


def test_only_your_own_list_and_only_another_member_can_be_credited(app):
    chris = app.test_client(); token = login(chris, "chris")
    dishes, laundry = task_id(app, "H02"), task_id(app, "H01")
    assert credit(chris, token, dishes, who="chris").status_code == 403       # not yourself
    assert credit(chris, token, dishes, who="admin").status_code == 403
    assert credit(chris, token, dishes, who="nobody").status_code == 403
    assert credit(chris, token, laundry, period="W2026-11-09").status_code == 403   # Andrea's own task
    hers = app.test_client(); her_token = login(hers, "andrea")
    assert credit(hers, her_token, dishes, who="chris").status_code == 403    # can't hand Chris his own task
    assert rows(app, "SELECT 1 FROM completions") == []
    assert "did this · send thanks" not in chris.get("/people/andrea").text


def test_say_thank_you_for_something_your_partner_crossed_off(app):
    chris = app.test_client(); token = login(chris, "chris")
    hers = app.test_client(); her_token = login(hers, "andrea")
    dishes, laundry = task_id(app, "H02"), task_id(app, "H01")
    with app.app_context():
        subscribe("andrea")
    with mock.patch.object(push, "send_one", return_value="ok") as sent:
        covered = hers.post(f"/tasks/{dishes}/toggle", headers=JSON,
                            data={"csrf": her_token, "done": "1", "cover": "1", "period": TODAY, "board_user": "chris"}).json
        cid = covered["completion_id"]
        assert f"/completions/{cid}/thank" in chris.get("/").text
        assert f"/completions/{cid}/thank" not in hers.get("/people/chris").text     # not your own work
        assert hers.post(f"/completions/{cid}/thank", data={"csrf": her_token}, headers=JSON).status_code == 403
        sent.reset_mock()
        first = chris.post(f"/completions/{cid}/thank", data={"csrf": token}, headers=JSON).json
        again = chris.post(f"/completions/{cid}/thank", data={"csrf": token}, headers=JSON).json
        assert first["ok"] and first["sent"] and first["message"] == "Thank-you sent to Andrea."
        assert again["ok"] and not again["new"] and sent.call_count == 1
    page = chris.get("/").text
    assert "You said thank you." in page and f"/completions/{cid}/thank" not in page
    # Thanking isn't recording: the completion is still only Andrea's to undo.
    undo = chris.post(f"/tasks/{dishes}/toggle", headers=JSON,
                      data={"csrf": token, "done": "0", "period": TODAY, "completion_id": cid})
    assert undo.status_code == 403

    # Her own task is simply hers; there is nothing to thank her for on Chris's behalf.
    own = hers.post(f"/tasks/{laundry}/toggle", headers=JSON,
                    data={"csrf": her_token, "done": "1", "period": "W2026-11-09"}).json
    assert chris.post(f"/completions/{own['completion_id']}/thank", data={"csrf": token}, headers=JSON).status_code == 403
    assert chris.post("/completions/99999/thank", data={"csrf": token}, headers=JSON).status_code == 403
    plain = hers.post(f"/tasks/{dishes}/toggle", data={"csrf": her_token, "done": "0", "period": TODAY, "completion_id": cid})
    assert plain.status_code == 302 and rows(app, "SELECT 1 FROM thanks") == []     # undone work takes its thanks along
