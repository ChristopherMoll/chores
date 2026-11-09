"""Due dates, one-off tasks, reminders, push, and the fixes that shipped alongside them."""
import base64
import csv
import io
import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pytest
from pywebpush import WebPushException

from chores import cms, db, logic, periods, push, reminders
from tests.test_app import PW, app, login, task_id  # noqa: F401  (app is a fixture)

ROOT = Path(__file__).parent.parent
JSON_HEADERS = {"Accept": "application/json"}
APPLE = "https://web.push.apple.com/abc"


def uid(name):
    return db.get_db().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()["id"]


def set_task(code, **fields):
    conn = db.get_db()
    conn.execute(f"UPDATE tasks SET {', '.join(k + ' = ?' for k in fields)} WHERE code = ?",
                 [*fields.values(), code])
    conn.commit()
    return conn.execute("SELECT * FROM tasks WHERE code = ?", (code,)).fetchone()


def subscribe(name, endpoint=APPLE):
    push.save_subscription(uid(name), {"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}})


@pytest.fixture
def sent():
    """Replaces the network call; each test can change the return value."""
    with mock.patch.object(push, "send_one", return_value="ok") as m:
        yield m


# ----------------------------------------------------------------- fixes ---

def test_csrf_needs_a_token_in_the_session(app):
    assert app.test_client().post("/login", data={"username": "chris", "password": PW}).status_code == 400


def test_csrf_non_ascii_token_is_rejected_cleanly(app):
    c = app.test_client()
    c.get("/login")
    assert c.post("/login", data={"username": "chris", "password": PW, "csrf": "é"}).status_code == 400


@pytest.mark.parametrize("target,expected", [
    ("/compare", "/compare"),
    ("//evil.example", "/"),
    ("/\\evil.example", "/"),
    ("/\t/evil.example", "/"),
    ("https://evil.example", "/"),
])
def test_login_only_redirects_within_the_site(app, target, expected):
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = c.post("/login", query_string={"next": target}, data={"username": "chris", "password": PW, "csrf": token})
    assert r.headers["Location"] == expected


def test_completion_from_old_schedule_does_not_hide_current_one(app):
    with app.app_context():
        t = set_task("H02", frequency="daily")
        conn = db.get_db()
        for key in ("D2026-11-11", "W2026-11-09"):
            conn.execute("INSERT INTO completions(task_id, user_id, period_key, done_on, done_at, owner_at, points)"
                         " VALUES (?, ?, ?, '2026-11-11', 'x', 'chris', 1)", (t["id"], uid("chris"), key))
        conn.commit()
        row = next(r for r in logic.dashboard("chris")["board"]["daily"] if r["task"]["code"] == "H02")
        assert row["done"]


def test_simultaneous_cross_off_is_not_an_error(app):
    class Racy:
        """The existence check sees nothing, as if the other tap hadn't landed yet."""
        def __init__(self, real):
            self.real, self.first = real, True

        def execute(self, sql, *args):
            if self.first:
                self.first = False
                return self.real.execute("SELECT * FROM completions WHERE 0")
            return self.real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.real, name)

    with app.app_context():
        task = set_task("H02", frequency="daily")
        chris = db.get_db().execute("SELECT * FROM users WHERE username = 'chris'").fetchone()
        assert logic.toggle(chris, task) is True
        with mock.patch.object(logic, "get_db", lambda: Racy(db.get_db())):
            assert logic.toggle(chris, task) is True
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 1


def test_env_example_has_no_end_of_line_comments():
    """systemd would read them as part of the value."""
    for line in (ROOT / "deploy" / "chores.env.example").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            assert re.fullmatch(r"[A-Z_]+=\S*", line), line


@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit")
def test_install_script_is_executable():
    assert os.access(ROOT / "deploy" / "install.sh", os.X_OK)


def test_windows_installer_is_ascii():
    """Windows PowerShell 5.1 misreads non-ASCII characters in a file without a BOM."""
    for name in ("install.ps1", "update.ps1"):
        (ROOT / "deploy" / "windows" / name).read_bytes().decode("ascii")


def test_env_file_loading(tmp_path, monkeypatch):
    import manage

    f = tmp_path / "chores.env"
    f.write_bytes("\ufeff# a comment\nCHORES_TEST_A=C:\\ProgramData\\chores\n\nCHORES_TEST_B = x=y\n".encode())
    monkeypatch.setenv("CHORES_TEST_A", "before")
    monkeypatch.delenv("CHORES_TEST_B", raising=False)
    manage.load_env_file(f)
    assert os.environ["CHORES_TEST_A"] == "C:\\ProgramData\\chores"
    assert os.environ["CHORES_TEST_B"] == "x=y"
    monkeypatch.delenv("CHORES_TEST_B")


# ------------------------------------------------------------- migration ---

def test_v1_database_migrates_without_losing_history(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite3")
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#000');
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, grp TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly', 'other')),
            owner TEXT NOT NULL DEFAULT 'volunteer', hours_month REAL, notes TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE completions (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, period_key TEXT NOT NULL,
            done_on TEXT NOT NULL, done_at TEXT NOT NULL, owner_at TEXT NOT NULL, points INTEGER NOT NULL,
            UNIQUE (task_id, period_key));
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        INSERT INTO users VALUES (1, 'chris', 'Chris', 'x', 'member', '#000');
        INSERT INTO tasks(id, code, grp, name, frequency, hours_month) VALUES (7, 'H01', 'Home', 'Laundry', 'weekly', 4);
        INSERT INTO completions VALUES (1, 7, 1, 'W2026-11-02', '2026-11-03', 'x', 'chris', 5);
    """)
    db.init_schema(conn)
    db.init_schema(conn)     # running it again must be harmless
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "2"
    assert conn.execute("SELECT points FROM completions WHERE task_id = 7").fetchone()[0] == 5
    assert conn.execute("SELECT hours_month FROM tasks WHERE id = 7").fetchone()[0] == 4
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.execute("UPDATE tasks SET frequency = 'once', due_date = '2026-12-01' WHERE id = 7")
    conn.execute("DELETE FROM tasks WHERE id = 7")      # the cascade still works after the rebuild
    assert conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 0
    conn.close()


def test_failed_migration_changes_nothing(tmp_path):
    conn = db.connect(tmp_path / "x.sqlite3")
    conn.executescript("CREATE TABLE tasks (id INTEGER PRIMARY KEY);"     # missing every other column
                       "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                       "INSERT INTO meta VALUES ('schema_version', '1');")
    with pytest.raises(sqlite3.Error):
        db.init_schema(conn)
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'tasks_new'").fetchone() is None
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


# ------------------------------------------------------------ due dates ---

@pytest.mark.parametrize("fields,today_,expected", [
    ({"frequency": "weekly", "due_weekday": 2}, date(2026, 11, 9), date(2026, 11, 11)),    # Wed of this week
    ({"frequency": "weekly", "due_weekday": None}, date(2026, 11, 9), date(2026, 11, 15)),   # end of week
    ({"frequency": "monthly", "due_monthday": 15}, date(2026, 11, 2), date(2026, 11, 15)),
    ({"frequency": "monthly", "due_monthday": 31}, date(2027, 2, 2), date(2027, 2, 28)),     # short month
    ({"frequency": "monthly", "due_monthday": None}, date(2026, 11, 2), date(2026, 11, 30)),
    ({"frequency": "once", "due_date": "2026-12-25"}, date(2026, 11, 2), date(2026, 12, 25)),
    ({"frequency": "once", "due_date": None}, date(2026, 11, 2), None),
    ({"frequency": "other"}, date(2026, 11, 2), None),
])
def test_due_date(app, fields, today_, expected):
    with app.app_context():
        assert periods.due_date(set_task("H03", **fields), today_) == expected


def test_due_day_follows_the_configured_week_start(app):
    app.config["WEEK_START"] = 6     # Sunday
    with app.app_context():
        task = set_task("H03", frequency="weekly", due_weekday=0)
        assert periods.due_date(task, date(2026, 11, 11)) == date(2026, 11, 9)


@pytest.mark.parametrize("value,expected", [("00:05", "12:05 am"), ("12:00", "12:00 pm"), ("20:30", "8:30 pm")])
def test_format_time(value, expected):
    assert periods.format_time(value) == expected


def test_due_labels(app):
    now = datetime(2026, 11, 11, 12, 0)    # Wednesday noon
    with app.app_context():
        label = lambda **f: periods.due_info(set_task("H03", **f), now)     # noqa: E731
        assert label(frequency="daily", due_time="20:00") == {"date": now.date(), "label": "By 8:00 pm", "overdue": False}
        assert label(frequency="daily", due_time="08:00")["overdue"] is True
        assert label(frequency="once", due_date="2026-11-11")["label"] == "Due today"
        assert label(frequency="once", due_date="2026-11-12")["label"] == "Due tomorrow"
        assert label(frequency="once", due_date="2026-11-14")["label"] == "Due Sat"
        assert label(frequency="once", due_date="2026-12-25")["label"] == "Due Dec 25"
        over = label(frequency="once", due_date="2026-11-03")
        assert over["label"] == "Overdue · Nov 3" and over["overdue"]
        assert periods.due_info(set_task("H03", frequency="weekly", due_weekday=None), now) is None


@pytest.mark.parametrize("form", [
    {"frequency": "once", "due_date": "not-a-date"},
    {"frequency": "weekly", "due_weekday": "9"},
    {"frequency": "monthly", "due_monthday": "32"},
    {"frequency": "daily", "due_time": "25:00"},
])
def test_task_form_rejects_bad_due_settings(app, form):
    c = app.test_client()
    t = login(c, "chris")
    data = {"csrf": t, "code": "H99", "grp": "Home", "name": "Thing", "owner": "chris", "active": "on", **form}
    assert b"look right" in c.post("/manage/tasks/new", data=data).data
    with app.app_context():
        assert db.get_db().execute("SELECT 1 FROM tasks WHERE code = 'H99'").fetchone() is None


def test_task_form_keeps_only_the_due_setting_for_its_frequency(app):
    c = app.test_client()
    t = login(c, "chris")
    c.post("/manage/tasks/new", data={
        "csrf": t, "code": "H99", "grp": "Home", "name": "Filter", "owner": "chris", "active": "on",
        "frequency": "weekly", "due_weekday": "4", "due_date": "2026-12-01", "due_time": "08:00"})
    with app.app_context():
        row = db.get_db().execute("SELECT * FROM tasks WHERE code = 'H99'").fetchone()
        assert (row["due_weekday"], row["due_date"], row["due_time"]) == (4, None, None)


def test_changing_frequency_in_the_grid_clears_stale_due_settings(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="weekly", due_weekday=2)["id"]
    c.post("/manage/tasks/bulk", data={"csrf": t, "id": [tid], f"owner_{tid}": "chris",
                                       f"frequency_{tid}": "monthly", f"hours_{tid}": "", f"active_{tid}": "on"})
    with app.app_context():
        row = db.get_db().execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        assert (row["frequency"], row["due_weekday"]) == ("monthly", None)


def test_due_settings_survive_csv_export_and_import(app):
    c = app.test_client()
    login(c, "chris")
    with app.app_context():
        set_task("H03", frequency="weekly", due_weekday=4)
        set_task("H04", frequency="once", due_date="2026-12-01")
    rows = list(csv.DictReader(io.StringIO(c.get("/manage/export.csv").get_data(as_text=True))))
    with app.app_context():
        set_task("H03", due_weekday=None)
        set_task("H04", due_date=None)
        _, problems = cms.apply_import(rows)
        assert not problems
        conn = db.get_db()
        assert conn.execute("SELECT due_weekday FROM tasks WHERE code = 'H03'").fetchone()[0] == 4
        assert conn.execute("SELECT due_date FROM tasks WHERE code = 'H04'").fetchone()[0] == "2026-12-01"


def test_import_reports_a_bad_weekday(app):
    with app.app_context():
        set_task("H03", frequency="weekly")
        _, problems = cms.apply_import([{"Code": "H03", "Due Weekday": "Blursday"}])
        assert len(problems) == 1 and "Blursday" in problems[0]


# -------------------------------------------------------------- one-offs ---

def test_one_off_lifecycle(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="once", due_date="2026-11-10", owner="chris")["id"]
    page = c.get("/").get_data(as_text=True)
    assert "One-off" in page and "Overdue · Nov 10" in page
    assert c.post(f"/tasks/{tid}/toggle", headers={**JSON_HEADERS, "X-CSRF-Token": t}).json["done"]
    with app.app_context():
        assert logic.dashboard("chris")["board"]["once"][0]["done"]    # still visible today, so it can be undone
    app.config["TODAY_OVERRIDE"] = "2026-11-12"
    with app.app_context():
        assert logic.dashboard("chris")["board"]["once"] == []
    assert b"Overdue" not in c.get("/").data


def test_one_offs_do_not_count_toward_monthly_load(app):
    with app.app_context():
        before = logic.load_hours("chris")["total"]
        set_task("H03", frequency="once", owner="chris", hours_month=20)
        assert logic.load_hours("chris")["total"] == before


# ------------------------------------------------------------- reminders ---

def rem(amount, unit, at_time=""):
    return {"amount": amount, "unit": unit, "at_time": at_time}


def test_fire_at(app):
    now = datetime(2026, 11, 11, 12, 0)
    with app.app_context():
        once = set_task("H03", frequency="once", due_date="2026-11-13")
        assert reminders.fire_at(rem(1, "days", "09:00"), once, now) == datetime(2026, 11, 12, 9, 0)
        daily = set_task("H03", frequency="daily", due_date=None, due_time="20:00")
        assert reminders.fire_at(rem(30, "minutes"), daily, now) == datetime(2026, 11, 11, 19, 30)
        assert reminders.fire_at(rem(0, "days", "18:00"), daily, now) == datetime(2026, 11, 11, 18, 0)
        untimed = set_task("H03", due_time=None)
        assert reminders.fire_at(rem(1, "hours"), untimed, now) == datetime(2026, 11, 11, 22, 59)
        assert reminders.fire_at(rem(1, "days", "09:00"), set_task("H03", frequency="other"), now) is None


def test_reminder_does_not_fire_before_the_period_opens(app):
    with app.app_context():
        monday = set_task("H03", frequency="weekly", due_weekday=0)
        assert reminders.fire_at(rem(1, "days", "09:00"), monday, datetime(2026, 11, 9, 8, 0)) \
            == datetime(2026, 11, 9, 9, 0)
        first = set_task("H03", frequency="monthly", due_weekday=None, due_monthday=1)
        assert reminders.fire_at(rem(3, "days", "07:30"), first, datetime(2026, 11, 1, 6, 0)) \
            == datetime(2026, 11, 1, 7, 30)
        early = set_task("H03", frequency="daily", due_monthday=None, due_time="00:10")
        assert reminders.fire_at(rem(1, "hours"), early, datetime(2026, 11, 11, 0, 0)) == datetime(2026, 11, 11, 0, 0)


def setup_nag(code="H03", when=datetime(2026, 11, 1), **task_fields):
    fields = {"frequency": "once", "due_date": "2026-11-13", **task_fields}
    task = set_task(code, **fields)
    reminders.add(uid("chris"), task["id"], 1, "days", "09:00", created=when)
    return task


FIRE = datetime(2026, 11, 12, 9, 0)


def test_run_due_sends_once(app, sent):
    with app.app_context():
        task = setup_nag()
        subscribe("chris")
        assert reminders.run_due(FIRE) == 1
        payload = sent.call_args.args[1]
        assert payload["url"] == f"/#task-{task['id']}" and task["name"] in payload["title"]
        assert payload["body"] == "Not done yet. Due tomorrow."
        assert reminders.run_due(FIRE.replace(minute=1)) == 0
        assert sent.call_count == 1


def test_run_due_waits_for_the_time(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("chris")
        assert reminders.run_due(FIRE.replace(hour=8, minute=59)) == 0
        assert not sent.called


def test_run_due_skips_finished_tasks(app, sent):
    with app.app_context():
        task = setup_nag()
        subscribe("chris")
        chris = db.get_db().execute("SELECT * FROM users WHERE username = 'chris'").fetchone()
        set_task("H03", owner="chris")
        logic.toggle(chris, db.get_db().execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone())
        assert reminders.run_due(FIRE) == 0
        assert not sent.called


def test_run_due_skips_inactive_tasks(app, sent):
    with app.app_context():
        setup_nag(active=0)
        subscribe("chris")
        assert reminders.run_due(FIRE) == 0


def test_run_due_catches_up_only_within_the_grace_period(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("chris")
        assert reminders.run_due(FIRE.replace(hour=12)) == 0
        assert reminders.run_due(FIRE.replace(hour=10, minute=30)) == 1


def test_reminder_added_after_its_time_waits_for_the_next_occurrence(app, sent):
    with app.app_context():
        setup_nag(when=FIRE.replace(hour=10))
        subscribe("chris")
        assert reminders.run_due(FIRE.replace(hour=10, minute=1)) == 0


def test_run_due_only_pushes_to_the_person_who_asked(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("andrea", APPLE + "-andrea")
        assert reminders.run_due(FIRE) == 0
        subscribe("chris")
        assert reminders.run_due(FIRE) == 1
        assert sent.call_args.args[0]["endpoint"] == APPLE


def test_recurring_reminder_fires_again_next_period(app, sent):
    with app.app_context():
        setup_nag(frequency="weekly", due_date=None, due_weekday=4)    # Friday
        subscribe("chris")
        assert reminders.run_due(datetime(2026, 11, 12, 9, 0)) == 1
        assert reminders.run_due(datetime(2026, 11, 19, 9, 0)) == 1


def test_one_off_can_nag_again_if_its_date_moves(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("chris")
        assert reminders.run_due(FIRE) == 1
        set_task("H03", due_date="2026-11-20")
        assert reminders.run_due(datetime(2026, 11, 19, 9, 0)) == 1


def test_failed_send_is_retried(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("chris")
        sent.return_value = "error"
        assert reminders.run_due(FIRE) == 0
        sent.return_value = "ok"
        assert reminders.run_due(FIRE.replace(minute=1)) == 1


def test_expired_subscription_is_deleted(app, sent):
    with app.app_context():
        setup_nag()
        subscribe("chris")
        sent.return_value = "gone"
        assert reminders.run_due(FIRE) == 0
        assert push.subscriptions_for(uid("chris")) == []


def test_reminder_page_add_and_remove(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="once", due_date="2026-11-20")["id"]
    assert b"Add a reminder" in c.get(f"/tasks/{tid}/remind").data
    r = c.post(f"/tasks/{tid}/remind", data={"csrf": t, "amount": "2", "unit": "days", "at_time": "18:30"},
               follow_redirects=True)
    assert b"2 days before at 6:30 pm" in r.data
    r = c.post(f"/tasks/{tid}/remind", data={"csrf": t, "amount": "2", "unit": "days", "at_time": "18:30"},
               follow_redirects=True)
    assert b"already have" in r.data
    with app.app_context():
        set_task("H03", owner="chris")
    assert "You have a reminder on this" in c.get("/").get_data(as_text=True)
    with app.app_context():
        rid = db.get_db().execute("SELECT id FROM reminders").fetchone()["id"]
    a = app.test_client()
    ta = login(a, "andrea")
    assert a.post(f"/reminders/{rid}/delete", data={"csrf": ta}).status_code == 404     # someone else's reminder
    c.post(f"/reminders/{rid}/delete", data={"csrf": t})
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


@pytest.mark.parametrize("form,error", [
    ({"amount": "x", "unit": "days", "at_time": "09:00"}, b"whole number"),
    ({"amount": "-1", "unit": "days", "at_time": "09:00"}, b"between 0"),
    ({"amount": "1", "unit": "weeks", "at_time": "09:00"}, b"Pick minutes"),
    ({"amount": "1", "unit": "days", "at_time": ""}, b"time of day"),
])
def test_reminder_form_validation(app, form, error):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="once", due_date="2026-11-20")["id"]
    assert error in c.post(f"/tasks/{tid}/remind", data={"csrf": t, **form}).data


def test_daily_task_rejects_days_before(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="daily", due_time="20:00")["id"]
    r = c.post(f"/tasks/{tid}/remind", data={"csrf": t, "amount": "1", "unit": "days", "at_time": "09:00"})
    assert b"due every day" in r.data


def test_task_without_a_due_date_explains_itself(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", frequency="once", due_date=None)["id"]
    assert b"doesn't have one" in c.get(f"/tasks/{tid}/remind").data
    c.post(f"/tasks/{tid}/remind", data={"csrf": t, "amount": "1", "unit": "days", "at_time": "09:00"})
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


def test_admin_cannot_set_reminders(app):
    c = app.test_client()
    login(c, "admin")
    assert c.get(f"/tasks/{task_id(app, 'H03')}/remind").status_code == 403


# ------------------------------------------------------------------ push ---

def test_service_worker_is_served_from_the_root(app):
    r = app.test_client().get("/sw.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript"
    assert "no-cache" in r.headers["Cache-Control"] and b"showNotification" in r.data


def test_home_screen_icons_exist(app):
    c = app.test_client()
    manifest = json.loads(c.get("/static/manifest.webmanifest").data)
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert c.get(icon["src"]).status_code == 200
    r = c.get("/static/icon-180.png")
    assert r.status_code == 200 and r.data.startswith(b"\x89PNG")


def test_every_mascot_pose_used_in_a_template_exists():
    used = {"default", "reminder", "celebrate", "award", "question", "suggestion"}
    for path in (ROOT / "chores" / "templates").rglob("*.html"):
        used |= set(re.findall(r"mole\('(\w+)'", path.read_text(encoding="utf-8")))
    for pose in used:
        assert (ROOT / "chores" / "static" / "mascot" / "small" / f"{pose}.png").is_file()


def test_app_is_branded_molechores(app):
    c = app.test_client()
    assert "<h1>MoleChores</h1>" in c.get("/login").get_data(as_text=True)
    assert json.loads(c.get("/static/manifest.webmanifest").data)["name"] == "MoleChores"


def test_dashboard_mascot_follows_the_days_progress(app):
    c = app.test_client()
    t = login(c, "chris")

    def pose():
        page = c.get("/").get_data(as_text=True)
        return re.search(r'id="hero-mole" src="[^"]*/mascot/small/(\w+)\.png', page).group(1)

    assert pose() == "default"
    for code in ("H02", "H05"):              # Chris's two daily tasks in the fixture
        c.post(f"/tasks/{task_id(app, code)}/toggle", headers={**JSON_HEADERS, "X-CSRF-Token": t})
    assert pose() == "celebrate"


def test_dashboard_offers_notifications_only_on_your_own_page(app):
    c = app.test_client()
    login(c, "chris")
    page = c.get("/").get_data(as_text=True)
    key = re.search(r'data-vapid="([^"]+)"', page).group(1)
    assert len(base64.urlsafe_b64decode(key + "==")) == 65       # uncompressed P-256 point
    assert "push-card" not in c.get("/people/andrea").get_data(as_text=True)


def test_vapid_keys_are_stable_and_private(app):
    with app.app_context():
        first = push.public_key()
        assert push.public_key() == first
        private, _ = push._key_paths()
        if os.name != "nt":
            assert private.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("endpoint,ok", [
    (APPLE, True),
    ("https://fcm.googleapis.com/fcm/send/x", True),
    ("http://web.push.apple.com/abc", False),
    ("https://evil.example/abc", False),
    ("https://push.apple.com.evil.example/abc", False),
    ("https://127.0.0.1/abc", False),
])
def test_subscribe_only_accepts_real_push_services(app, endpoint, ok):
    c = app.test_client()
    t = login(c, "chris")
    r = c.post("/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}},
               headers={"X-CSRF-Token": t})
    assert r.status_code == (200 if ok else 400)
    with app.app_context():
        assert len(push.subscriptions_for(uid("chris"))) == (1 if ok else 0)


def test_subscribe_requires_login_and_csrf(app):
    assert app.test_client().post("/push/subscribe", json={}).status_code == 400
    c = app.test_client()
    login(c, "chris")
    assert c.post("/push/subscribe", json={"endpoint": APPLE}).status_code == 400


def test_device_moves_to_whoever_subscribes_last(app):
    with app.app_context():
        subscribe("chris")
        subscribe("andrea")
        assert push.subscriptions_for(uid("chris")) == []
        assert len(push.subscriptions_for(uid("andrea"))) == 1


def test_unsubscribe_removes_only_your_own(app):
    c = app.test_client()
    t = login(c, "chris")
    with app.app_context():
        subscribe("andrea")
    c.post("/push/unsubscribe", json={"endpoint": APPLE}, headers={"X-CSRF-Token": t})
    with app.app_context():
        assert len(push.subscriptions_for(uid("andrea"))) == 1


def test_test_button_reports_result(app, sent):
    c = app.test_client()
    t = login(c, "chris")
    assert c.post("/push/test", json={}, headers={"X-CSRF-Token": t}).json == {"ok": False, "sent": 0}
    with app.app_context():
        subscribe("chris")
    assert c.post("/push/test", json={}, headers={"X-CSRF-Token": t}).json == {"ok": True, "sent": 1}


def device_keys():
    """A real browser-style key pair, so pywebpush does its real encryption."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()     # noqa: E731
    return encode(point), encode(os.urandom(16))


def real_subscription():
    p256dh, auth = device_keys()
    return {"id": 1, "endpoint": APPLE, "p256dh": p256dh, "auth": auth}


def test_send_one_builds_a_valid_signed_request(app):
    with app.app_context():
        push.public_key()
        with mock.patch("requests.post", return_value=mock.Mock(status_code=201, text="")) as post:
            assert push.send_one(real_subscription(), {"title": "x"}) == "ok"
    args, kwargs = post.call_args
    assert args[0] == APPLE
    headers = kwargs["headers"]
    assert headers["ttl"] == str(push.TTL_SECONDS) and headers["content-encoding"] == "aes128gcm"
    scheme, token, key = re.fullmatch(r"(vapid) t=([^,]+), ?k=(.+)", headers["Authorization"]).groups()
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims["aud"] == "https://web.push.apple.com"
    assert claims["sub"] == "https://chores.moll.casa"
    with app.app_context():
        assert key.rstrip("=") == push.public_key()
    assert b'"title"' not in kwargs["data"]        # the payload left encrypted


@pytest.mark.parametrize("status,expected", [(404, "gone"), (410, "gone"), (403, "error"), (429, "error")])
def test_send_one_maps_push_service_errors(app, status, expected):
    with app.app_context():
        push.public_key()
        response = mock.Mock(status_code=status, text="nope")
        with mock.patch("pywebpush.webpush", side_effect=WebPushException("x", response=response)):
            assert push.send_one(real_subscription(), {"title": "x"}) == expected


def test_send_one_survives_a_network_failure(app):
    with app.app_context():
        push.public_key()
        with mock.patch("pywebpush.webpush", side_effect=ConnectionError("down")):
            assert push.send_one(real_subscription(), {"title": "x"}) == "error"


def test_new_pages_render(app):
    c = app.test_client()
    login(c, "admin")
    assert b"Push subject" in c.get("/admin/").data
    login_client = app.test_client()
    login(login_client, "chris")
    assert login_client.get("/manage/tasks/new").status_code == 200
    with app.app_context():
        tid = set_task("H03", frequency="weekly", due_weekday=2)["id"]
    page = login_client.get("/manage/tasks").get_data(as_text=True)
    assert "⏱ Wed" in page
    assert login_client.get(f"/manage/tasks/{tid}").status_code == 200
