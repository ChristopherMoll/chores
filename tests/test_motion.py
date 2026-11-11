"""Artwork isolation and accessible, per-task undo through the rendered forms."""
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from chores import db, logic, mascots, scheduling
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task
from tests.test_scheduling import clock, laundry, person, start


class UndoForms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.current = [], None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and "undo-form" in attrs.get("class", "").split():
            self.current = {"action": attrs["action"], "data": {}}
        elif self.current is not None and tag == "input":
            self.current["data"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None


@pytest.mark.parametrize("pose", sorted(mascots.POSES))
def test_prepared_svg_is_inline_safe_and_preserves_vector_parts(pose):
    art = ET.fromstring(str(mascots.artwork(pose)))
    tags = [el.tag.rsplit("}", 1)[-1] for el in art.iter()]
    assert not {"style", "script", "image", "metadata", "foreignObject"}.intersection(tags)
    assert tags.count("path") > 20
    assert any(el.get("data-part") == "body" for el in art.iter())
    for el in art.iter():
        assert "id" not in el.attrib and "style" not in el.attrib
        assert not any(key.startswith("on") or "href" in key for key in el.attrib)
    assert art.get("viewBox").startswith("-14 -14")
    assert (Path(__file__).parents[1] / "chores/static/mascot" / f"{pose}.svg").exists()


def test_inline_artworks_render_with_strict_csp_and_static_fallback(app):
    client = app.test_client()
    login_page = client.get("/login")
    assert 'data-mascot data-pose="default"' in login_page.text
    assert 'class="mole-art"' in login_page.text and 'xmlns="http://www.w3.org/2000/svg"' in login_page.text
    assert "unsafe-inline" not in login_page.headers["Content-Security-Policy"]
    assert "/static/motion.js" in login_page.text
    login(client, "chris")
    page = client.get("/")
    assert 'data-mole-state="working"' in page.text
    assert 'data-mole-state="celebrate" hidden' in page.text


def test_multiple_completions_can_each_be_undone_from_the_ui_without_a_time_limit(app):
    client = app.test_client()
    token = login(client, "andrea")
    with app.app_context():
        ids = [set_task(code, owner="andrea", frequency=freq, due_weekday=2, due_monthday=11)["id"]
               for code, freq in (("H02", "daily"), ("H03", "weekly"), ("H04", "monthly"))]
    for tid in ids:
        assert client.post(f"/tasks/{tid}/toggle", data={"csrf": token, "done": "1"}).status_code == 302
    with app.app_context():
        db.get_db().execute("UPDATE completions SET done_at='2026-11-10T10:00:00+00:00'")
        db.get_db().commit()
    page = client.get("/").text
    assert "Review &amp; undo" in page
    forms = [f for f in UndoForms(page).forms if f["action"] in [f"/tasks/{tid}/toggle" for tid in ids]]
    assert len(forms) == 3
    for form in forms:
        assert form["data"]["done"] == "0"
        response = client.post(form["action"], data=form["data"], headers={"Accept": "application/json"})
        assert response.status_code == 200 and response.json["done"] is False
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 0


def test_shared_undo_is_visible_only_to_the_actual_doer(app):
    tid = task_id(app, "H05")
    a = app.test_client(); token = login(a, "andrea")
    a.post(f"/tasks/{tid}/toggle", data={"csrf": token, "done": "1"})
    assert any(f["action"] == f"/tasks/{tid}/toggle" for f in UndoForms(a.get("/").text).forms)
    c = app.test_client(); csrf = login(c, "chris")
    assert not any(f["action"] == f"/tasks/{tid}/toggle" for f in UndoForms(c.get("/").text).forms)
    assert "Andrea can undo this completion" in c.get("/").text
    assert c.post(f"/tasks/{tid}/toggle", data={"csrf": csrf, "done": "0"}, headers={"Accept": "application/json"}).status_code == 403


def test_early_sequence_completion_has_a_usable_undo_button_while_still_waiting(app):
    clock(app)
    with app.app_context():
        wash, dry, _ = start(laundry())
        scheduling.toggle(person(), dry, True, early=True)
        run_id = wash["run_id"]
    client = app.test_client(); login(client, "chris")
    forms = UndoForms(client.get(f"/sequences/{run_id}").text).forms
    undo = next(f for f in forms if f["data"].get("occurrence_id") == str(dry["id"]))
    response = client.post(undo["action"], data=undo["data"], headers={"Accept": "application/json"})
    assert response.status_code == 200 and response.json["done"] is False
    with app.app_context():
        assert not scheduling.completion(dry)


def test_ui_undo_protects_later_completed_sequence_steps(app):
    clock(app)
    with app.app_context():
        wash, dry, _ = start(laundry())
        scheduling.toggle(person(), dry, True, early=True)
    client = app.test_client(); login(client, "chris")
    undo = next(f for f in UndoForms(client.get(f"/sequences/{wash['run_id']}").text).forms
                if f["data"].get("occurrence_id") == str(wash["id"]))
    response = client.post(undo["action"], data=undo["data"], headers={"Accept": "application/json"})
    assert response.status_code == 409 and "later completed steps" in response.json["error"]
    with app.app_context():
        assert scheduling.completion(wash) and scheduling.completion(dry)
