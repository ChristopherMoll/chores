"""Focused detail screens preserve the same permissions and occurrence identity as lists."""
from html.parser import HTMLParser

from chores import db, periods
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task
from tests.test_scheduling import clock, laundry, start


class CompletionButton(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.attributes = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "button" and "detail-complete" in attrs.get("class", ""):
            self.attributes = attrs


def test_task_detail_requires_login_and_returns_to_a_safe_list(app):
    c = app.test_client()
    tid = task_id(app, "H02")
    assert c.get(f"/tasks/{tid}/detail").status_code == 302
    login(c, "chris")
    page = c.get(f"/tasks/{tid}/detail?back=/?view=week").text
    assert 'href="/?view=week" data-back-to-list' in page
    assert "task-detail-region" in page and "More actions" in page
    assert "disabled" not in CompletionButton(page).attributes
    unsafe = c.get(f"/tasks/{tid}/detail?back=//example.com").text
    assert 'href="/" data-back-to-list' in unsafe
    together = c.get(f"/tasks/{tid}/detail?back=/compare").text
    assert 'href="/compare" class="on"' in together


def test_detail_keeps_partner_undo_and_cover_rules(app):
    tid = task_id(app, "H05")
    a = app.test_client(); token = login(a, "andrea")
    a.post(f"/tasks/{tid}/toggle", data={"csrf": token, "done": "1"})
    c = app.test_client(); login(c, "chris")
    page = c.get(f"/tasks/{tid}/detail").text
    assert "disabled" in CompletionButton(page).attributes
    assert "Andrea can undo this completion" in page
    assert "Say thank you" in page
    partner = c.get(f"/tasks/{task_id(app, 'H01')}/detail").text
    assert "disabled" in CompletionButton(partner).attributes
    assert "I took care of this" in partner


def test_detail_uses_exact_sequence_occurrence(app):
    clock(app)
    with app.app_context():
        wash, dry, _ = start(laundry())
    c = app.test_client(); login(c, "chris")
    page = c.get(f"/tasks/{dry['task_id']}/detail?occurrence_id={dry['id']}")
    assert page.status_code == 200
    assert f'name="occurrence_id" value="{dry["id"]}"' in page.text
    assert "disabled" in CompletionButton(page.text).attributes
    wrong = c.get(f"/tasks/{wash['task_id']}/detail?occurrence_id={dry['id']}")
    assert wrong.status_code == 404


def test_interval_detail_keeps_completed_receipt_after_deadline_advances(app):
    with app.app_context():
        task = set_task("H03", owner="chris", frequency="interval", repeat_every=2,
                        repeat_unit="weeks", repeat_start="2026-11-11")
        period = periods.task_key(task)
    c = app.test_client(); token = login(c, "chris")
    c.post(f"/tasks/{task['id']}/toggle", data={"csrf": token, "done": "1", "period": period})
    page = c.get(f"/tasks/{task['id']}/detail?period={period}")
    assert page.status_code == 200
    assert f'name="period" value="{period}"' in page.text
    assert 'aria-pressed="true"' in page.text
    assert "Undo completion" in page.text


def test_together_leads_with_shared_work_and_refreshes_after_completion(app):
    c = app.test_client(); token = login(c, "chris")
    page = c.get("/compare").text
    assert page.index('id="shared-region"') < page.index('id="household-overview"')
    tid = task_id(app, "H05")
    shared = page.split('id="shared-region"', 1)[1].split('id="volunteer-section"', 1)[0]
    assert f'id="task-{tid}"' in shared
    c.post(f"/tasks/{tid}/toggle", data={"csrf": token, "done": "1"})
    shared = c.get("/compare").text.split('id="shared-region"', 1)[1].split('id="volunteer-section"', 1)[0]
    assert "Shared work is taken care of" in shared and "Taken care of by Chris" in shared


def test_bulk_save_can_return_to_pending_live_filters_without_losing_edits(app):
    c = app.test_client(); token = login(c, "chris")
    tid = task_id(app, "H02")
    data = {"csrf": token, "id": tid, f"owner_{tid}": "andrea", f"frequency_{tid}": "daily",
            f"hours_{tid}": "3", f"active_{tid}": "on", "return_to": "/manage/tasks?q=Laundry"}
    response = c.post("/manage/tasks/bulk", data=data)
    assert response.location == "/manage/tasks?q=Laundry"
    with app.app_context():
        assert db.get_db().execute("SELECT owner FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "andrea"
    response = c.post("/manage/tasks/bulk", data={**data, "return_to": "//example.com"})
    assert response.location == "/"
