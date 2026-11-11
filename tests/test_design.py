"""Behavioral checks for the focused lists, safe retries, and management flows."""
from datetime import date
from urllib.parse import urlsplit

from chores import db, logic
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task


def test_today_surfaces_due_tasks_across_schedules_without_duplicates(app):
    with app.app_context():
        set_task("H03", owner="chris", frequency="weekly", due_weekday=2)
        set_task("H04", owner="chris", frequency="monthly", due_monthday=10)
        set_task("H06", owner="chris", frequency="once", due_date="2026-11-11")
        set_task("H07", owner="chris", frequency="once", due_date="2026-11-20")
        set_task("H08", owner="chris", frequency="once", due_date=None)
        views = logic.task_views(logic.dashboard("chris"))
        codes = [r["task"]["code"] for r in views["today"]["rows"]]
        assert set(codes) == {"H02", "H03", "H04", "H05", "H06"}
        assert len(codes) == len(set(codes))
        assert {r["task"]["code"] for r in views["once"]["rows"]} == {"H06", "H07", "H08"}
        assert views["today"]["progress"]["total"] == len(codes)


def test_implicit_period_deadlines_are_visible_in_today(app):
    with app.app_context():
        set_task("H03", owner="chris", frequency="weekly", due_weekday=None)
        d = date(2026, 11, 15)  # Sunday, the end of this household's week.
        view = logic.task_views(logic.dashboard("chris", d), d)["today"]
        assert any(r["task"]["code"] == "H03" for r in view["rows"])


def test_completion_retries_do_not_reverse_a_successful_save(app):
    client = app.test_client()
    token = login(client, "chris")
    tid = task_id(app, "H02")
    for state in ("1", "1", "0", "0"):
        result = client.post(f"/tasks/{tid}/toggle", data={"done": state, "view": "today", "return_to": "/?view=today"},
                             headers={"Accept": "application/json", "X-CSRF-Token": token})
        assert result.status_code == 200
        assert result.json["done"] == (state == "1")
        assert result.json["today"]["done"] == (1 if state == "1" else 0)
        assert result.json["counts"]["today"] == (1 if state == "1" else 2)
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 0


def test_shared_completion_reports_actual_doer_and_blocks_partner_undo(app):
    tid = task_id(app, "H05")
    a = app.test_client(); ta = login(a, "andrea")
    a.post(f"/tasks/{tid}/toggle", data={"done": "1"}, headers={"Accept": "application/json", "X-CSRF-Token": ta})
    c = app.test_client(); tc = login(c, "chris")
    response = c.post(f"/tasks/{tid}/toggle", data={"done": "1"}, headers={"Accept": "application/json", "X-CSRF-Token": tc})
    assert response.json["done"] and response.json["by"] == "Andrea"
    assert response.json["can_undo"] is False
    assert c.post(f"/tasks/{tid}/toggle", data={"done": "0"}, headers={"Accept": "application/json", "X-CSRF-Token": tc}).status_code == 403


def test_new_destinations_are_available_and_authenticated(app):
    c = app.test_client()
    for url in ("/progress", "/more"):
        assert c.get(url).status_code == 302
    login(c, "chris")
    for url in ("/progress", "/more", "/?view=week", "/?view=month", "/?view=once", "/?view=other"):
        assert c.get(url).status_code == 200
    assert 'data-view="today"' in c.get("/?view=invalid").text
    assert "push-card" not in c.get("/").text
    assert "push-card" in c.get("/more").text


def test_task_deep_links_find_future_and_volunteer_tasks(app):
    c = app.test_client(); login(c, "chris")
    with app.app_context():
        tid = set_task("H03", owner="chris", frequency="once", due_date="2026-11-20")["id"]
    response = c.get(f"/tasks/{tid}", follow_redirects=True)
    assert response.status_code == 200 and f'id="task-{tid}"' in response.text
    assert response.request.args["view"] == "once"
    volunteer = task_id(app, "C01")
    assert urlsplit(c.get(f"/tasks/{volunteer}").location).path == "/compare"


def test_edit_returns_to_filters_and_rejects_external_return_paths(app):
    c = app.test_client(); token = login(c, "chris")
    tid = task_id(app, "H02")
    fields = {"csrf": token, "code": "H02", "name": "Dishes", "description": "", "notes": "",
              "grp": "Home", "owner": "chris", "frequency": "daily", "hours_month": "2", "active": "on"}
    response = c.post(f"/manage/tasks/{tid}", data={**fields, "back": "/manage/tasks?owner=chris&q=Dishes"})
    assert response.location == "/manage/tasks?owner=chris&q=Dishes"
    response = c.post(f"/manage/tasks/{tid}", data={**fields, "back": "https://example.com"})
    assert response.location == "/"


def test_completed_tasks_are_kept_accessible_for_undo(app):
    c = app.test_client(); token = login(c, "chris")
    tid = task_id(app, "H02")
    c.post(f"/tasks/{tid}/toggle", data={"csrf": token, "done": "1"})
    page = c.get("/").text
    completed = page[page.index('<details class="completed-group"'):]
    assert f'id="task-{tid}"' in completed
    assert 'aria-label="Undo Dishes"' in completed


def test_stale_retry_cannot_complete_a_new_period(app):
    c = app.test_client(); token = login(c, "chris")
    tid = task_id(app, "H02")
    app.config["TODAY_OVERRIDE"] = "2026-11-12"
    response = c.post(f"/tasks/{tid}/toggle", data={"done": "1", "period": "D2026-11-11"},
                      headers={"Accept": "application/json", "X-CSRF-Token": token})
    assert response.status_code == 409
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 0
