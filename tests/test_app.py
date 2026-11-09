import re
from datetime import date

import pytest

from chores import create_app, db, logic, periods
from chores.auth import hash_password

PW = "correct-horse"


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"),
                      "SECRET_KEY": "test", "TODAY_OVERRIDE": "2026-11-11"})   # a Wednesday
    with app.app_context():
        conn = db.get_db()
        db.init_schema(conn)
        assert db.seed_tasks(conn) == 109
        for u, role in (("chris", "member"), ("andrea", "member"), ("admin", "admin")):
            conn.execute("INSERT INTO users(username, display_name, password_hash, role) VALUES (?,?,?,?)",
                         (u, u.title(), hash_password(PW), role))
        conn.execute("UPDATE tasks SET owner='chris' WHERE code='H02'")        # Dishes, daily
        conn.execute("UPDATE tasks SET owner='andrea' WHERE code='H01'")       # Laundry, weekly
        conn.execute("UPDATE tasks SET owner='shared', hours_month=15 WHERE code='H05'")  # Dinners, daily
        conn.commit()
    return app


def login(client, user):
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = client.post("/login", data={"username": user, "password": PW, "csrf": token})
    assert r.status_code == 302
    # login clears the session (fixation defence), so pick up the fresh token
    page = client.get("/account").get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def task_id(app, code):
    with app.app_context():
        return db.get_db().execute("SELECT id FROM tasks WHERE code=?", (code,)).fetchone()["id"]


def test_period_keys(app):
    with app.test_request_context():
        d = date(2026, 11, 11)
        assert periods.period_key("daily", d) == "D2026-11-11"
        assert periods.period_key("weekly", d) == "W2026-11-09"   # Monday
        assert periods.period_key("monthly", d) == "M2026-11"
        app.config["WEEK_START"] = 6
        assert periods.period_key("weekly", d) == "W2026-11-08"   # Sunday


def test_points(app):
    with app.app_context():
        t = {"hours_month": 15, "frequency": "daily"}
        assert logic.points_for(t, "chris") == 6              # 15h*60/30.44 ≈ 29.6 min → 6
        assert logic.points_for(t, "volunteer") == 9          # +50%, rounded up
        assert logic.points_for({"hours_month": None, "frequency": "weekly"}, "chris") == 5


def test_auth(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    page = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = c.post("/login", data={"username": "chris", "password": "nope", "csrf": token})
    assert b"don&#39;t match" in r.data
    assert c.post("/login", data={"username": "chris", "password": PW}).status_code == 400   # no CSRF


def test_cross_off_and_undo(app):
    c = app.test_client()
    token = login(c, "chris")
    dishes = task_id(app, "H02")
    r = c.post(f"/tasks/{dishes}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": token})
    assert r.json["ok"] and r.json["done"]
    r = c.post(f"/tasks/{dishes}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": token})
    assert r.json["done"] is False


def test_cannot_cross_off_partners_task(app):
    c = app.test_client()
    token = login(c, "chris")
    laundry = task_id(app, "H01")
    r = c.post(f"/tasks/{laundry}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": token})
    assert r.status_code == 403


def test_shared_counts_for_both_and_only_doer_can_undo(app):
    dinners = task_id(app, "H05")
    a = app.test_client(); ta = login(a, "andrea")
    assert a.post(f"/tasks/{dinners}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": ta}).json["done"]
    with app.app_context():
        d = logic.dashboard("chris")
        assert any(r["done"] for r in d["board"]["daily"] if r["task"]["code"] == "H05")
    c = app.test_client(); tc = login(c, "chris")
    r = c.post(f"/tasks/{dinners}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": tc})
    assert r.status_code == 403


def test_daily_resets_next_day(app):
    dishes = task_id(app, "H02")
    c = app.test_client(); t = login(c, "chris")
    c.post(f"/tasks/{dishes}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": t})
    with app.app_context():
        assert logic.streak("chris") == 0     # shared Dinners still open
    c.post(f"/tasks/{task_id(app, 'H05')}/toggle", headers={"Accept": "application/json", "X-CSRF-Token": t})
    with app.app_context():
        assert logic.streak("chris") == 1     # every daily task done today
    app.config["TODAY_OVERRIDE"] = "2026-11-12"
    with app.app_context():
        rows = logic.dashboard("chris")["board"]["daily"]
        assert not next(r for r in rows if r["task"]["code"] == "H02")["done"]
        assert logic.streak("chris") == 1     # yesterday was complete, today not yet → still 1


def test_everyone_can_manage_tasks(app):
    for user in ("chris", "andrea", "admin"):
        c = app.test_client(); login(c, user)
        assert c.get("/manage/tasks").status_code == 200, user
        assert c.get("/manage/export.csv").data.startswith(b"Code,Group,Task")


def test_admin_area_is_admin_only(app):
    c = app.test_client(); t = login(c, "chris")
    for url in ("/admin/", "/admin/users"):
        assert c.get(url).status_code == 403, url
    assert c.post("/admin/completions/1/delete", data={"csrf": t}).status_code == 403
    assert c.post("/admin/users", data={"csrf": t, "id": 1, "password": "hijacked1"}).status_code == 403
    a = app.test_client(); login(a, "admin")
    assert a.get("/").headers["Location"].endswith("/admin/")
    assert b"Schema version" in a.get("/admin/").data
    assert a.get("/admin/users").status_code == 200


def test_member_nav_hides_admin(app):
    c = app.test_client(); login(c, "chris")
    page = c.get("/").get_data(as_text=True)
    assert "/manage/tasks" in page and "/admin/" not in page


def test_bulk_deal(app):
    a = app.test_client(); t = login(a, "andrea")      # members can deal
    tid = task_id(app, "H03")
    r = a.post("/manage/tasks/bulk", data={"csrf": t, "id": [tid], f"owner_{tid}": "andrea",
                                          f"frequency_{tid}": "weekly", f"hours_{tid}": "2.5", f"active_{tid}": "on"})
    assert r.status_code == 302
    with app.app_context():
        row = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        assert (row["owner"], row["hours_month"]) == ("andrea", 2.5)


def test_import_rows(app):
    with app.app_context():
        n, problems = __import__("chores.cms", fromlist=["x"]).apply_import([
            {"Code": "H04", "Owner": "Needs Volunteer", "Hours / Month": 6, "Notes": "Sunday shop"},
            {"Code": "Z99", "Owner": "Chris"},
            {"Code": "H06", "Owner": "Bob"},
        ])
        row = db.get_db().execute("SELECT * FROM tasks WHERE code='H04'").fetchone()
        assert (row["owner"], row["hours_month"], row["notes"]) == ("volunteer", 6, "Sunday shop")
        assert n == 1 and len(problems) == 2


def test_pages_render(app):
    c = app.test_client(); login(c, "chris")
    for url in ("/", "/people/andrea", "/compare", "/compare?range=month", "/account"):
        assert c.get(url).status_code == 200, url
    for url in ("/manage/tasks", "/manage/tasks/new", "/manage/import", f"/manage/tasks/{task_id(app, 'H01')}"):
        assert c.get(url).status_code == 200, url
    a = app.test_client(); login(a, "admin")
    for url in ("/compare", "/admin/", "/admin/users", "/manage/tasks"):
        assert a.get(url).status_code == 200, url
