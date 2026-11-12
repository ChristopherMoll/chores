"""Selected dates and durable sequences, tested through the same paths as the app and Siri."""
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from werkzeug.datastructures import MultiDict

from chores import cms, db, logic, periods, reminders, scheduling
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task, subscribe, uid
from tests.test_voice import make_key, say


def clock(app, value="2026-11-11T10:00:00-05:00"):
    app.config["NOW_OVERRIDE"] = value


def person(name="chris"):
    return logic.user_by_name(name)


def selected(days="0,2,5,6", start="2026-11-09"):
    return set_task("H01", owner="chris", frequency="selected", selected_days=days,
                    selected_start=start, selected_until=None, active=1, due_time="20:00", hours_month=4)


def laundry(frequency="weekly", owner="chris"):
    root = set_task("H01", name="Wash Laundry", owner=owner, active=1, frequency=frequency,
                    due_weekday=2, hours_month=4)
    scheduling.save_recipe(root, [{"task_id": None, "name": name, "delay": 60, "hours": 2}
                                  for name in ("Dry the Laundry", "Fold the Laundry")])
    db.get_db().commit()
    return root


def start(root, key=None):
    if key:
        with db.get_db():
            run_id = scheduling.start_run(root, key)
    else:
        logic.toggle(person(), root, desired_done=True)
        run_id = db.get_db().execute("SELECT id FROM sequence_runs WHERE root_task_id=?", (root["id"],)).fetchone()[0]
    return [scheduling.get_occurrence(row["occurrence_id"]) for row in scheduling.run_rows(run_id)]


def form_for(task, **overrides):
    values = {k: str(task[k] or "") for k in cms.FIELDS if k not in ("active", "selected_days")}
    values.update(active="1", sequence_editor="1", **overrides)
    return MultiDict(values)


def test_all_seven_days_are_valid_and_weekend_dates_are_independent(app):
    clock(app, "2026-11-15T10:00:00-05:00")
    with app.app_context():
        task = selected("5,6", "2026-11-14")
        assert scheduling.parse_days("Sun, Saturday, Sun") == "5,6"
        assert scheduling.parse_days(list(range(7))) == "0,1,2,3,4,5,6"
        scheduling.ensure_selected()
        dates = scheduling.candidates(task["id"])
        assert [r["scheduled_date"] for r in dates] == ["2026-11-14", "2026-11-15"]
        with pytest.raises(ValueError, match="More than one"):
            logic.toggle(person(), task, desired_done=True)
        scheduling.toggle(person(), scheduling.get_occurrence(dates[0]["id"]), True)
        assert not scheduling.completion(dates[1])
        assert len(scheduling.candidates(task["id"])) == 1
        assert logic.points_for(task, "chris") == round(4 * 60 / (2 * 4.35) / 5)
        assert periods.due_summary(task) == "Sat, Sun by 8:00 pm"


def test_selected_schedule_edits_preserve_history_and_do_not_create_past_dates(app):
    clock(app)
    with app.app_context():
        task = selected()
        scheduling.ensure_selected()
        monday = scheduling.candidates(task["id"])[0]
        scheduling.toggle(person(), scheduling.get_occurrence(monday["id"]), True)
        set_task("H01", selected_days="4,6")
        scheduling.sync_selected(task["id"])
        db.get_db().commit()
        scheduling.ensure_selected()
        assert scheduling.completion(monday)
        current = db.get_db().execute("SELECT scheduled_date FROM task_occurrences WHERE task_id=? AND state='open' ORDER BY scheduled_date", (task["id"],)).fetchall()
        assert current[0][0] == "2026-11-09"  # its completed occurrence is kept
        assert [r[0] for r in current if r[0] < "2026-11-11"] == ["2026-11-09"]
        assert "2026-11-11" not in [r[0] for r in current]


def test_selected_skip_is_individual_and_survives_restart(app):
    clock(app)
    with app.app_context():
        task = selected("2", "2026-11-11")
        scheduling.ensure_selected()
        occurrence = scheduling.resolve(task)
        with pytest.raises(PermissionError):
            scheduling.skip(person("andrea"), occurrence)
        scheduling.skip(person(), occurrence)
        scheduling.ensure_selected()
        db.init_schema(db.get_db())
        assert scheduling.get_occurrence(occurrence["id"])["state"] == "skipped"
        assert not scheduling.completion(occurrence)
        assert db.get_db().execute("SELECT COUNT(*) FROM task_occurrences WHERE task_id=? AND state='open'", (task["id"],)).fetchone()[0] > 0


def test_delays_follow_actual_completion_and_waiting_is_not_due_today(app):
    clock(app)
    with app.app_context():
        db.get_db().execute("UPDATE tasks SET active=0")
        root = laundry()
        wash, dry, fold = start(root)
        assert scheduling.local(dry["ready_at"]).hour == 11
        assert fold["ready_at"] is None
        view = logic.task_views(logic.dashboard("chris"))["today"]
        assert not view["open"]
        assert len(view["upcoming"]) == 2
        assert view["progress"] == {"done": 1, "total": 1, "pct": 100}
        with pytest.raises(ValueError, match="still waiting"):
            scheduling.toggle(person(), dry, True)
        with pytest.raises(ValueError, match="previous step"):
            scheduling.toggle(person(), fold, True, early=True)
        clock(app, "2026-11-11T11:20:00-05:00")
        scheduling.toggle(person(), dry, True)
        assert scheduling.local(scheduling.get_occurrence(fold["id"])["ready_at"]).strftime("%H:%M") == "12:20"
        # Retry must neither add points nor move the next step.
        clock(app, "2026-11-11T11:30:00-05:00")
        scheduling.toggle(person(), dry, True)
        assert scheduling.local(scheduling.get_occurrence(fold["id"])["ready_at"]).strftime("%H:%M") == "12:20"
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 2


def test_early_completion_undo_and_dependency_protection(app):
    clock(app)
    with app.app_context():
        root = laundry()
        wash, dry, fold = start(root)
        scheduling.toggle(person(), dry, True, early=True)
        with pytest.raises(ValueError, match="later completed"):
            scheduling.toggle(person(), wash, False)
        scheduling.toggle(person(), dry, False)
        assert scheduling.get_occurrence(fold["id"])["ready_at"] is None
        scheduling.toggle(person(), wash, False)
        assert scheduling.get_occurrence(dry["id"])["ready_at"] is None
        clock(app, "2026-11-11T14:00:00-05:00")
        scheduling.toggle(person(), wash, True)
        assert scheduling.local(scheduling.get_occurrence(dry["id"])["ready_at"]).hour == 15


def test_runs_overlap_and_keep_steps_owners_delays_and_points_snapshots(app):
    clock(app)
    with app.app_context():
        root = laundry()
        first = start(root, "Rload1")
        scheduling.toggle(person(), first[0], True)
        child = scheduling.occurrence_task(first[1])
        db.get_db().execute("UPDATE tasks SET name='Different dryer',owner='andrea',hours_month=40 WHERE id=?", (child["id"],))
        scheduling.save_recipe(root, [{"task_id": child["id"], "delay": 15, "hours": None}])
        db.get_db().commit()
        second = start(root, "Rload2")
        assert len(second) == 2 and len(scheduling.run_rows(first[0]["run_id"])) == 3
        assert scheduling.occurrence_task(first[1])["name"] == "Dry the Laundry"
        assert scheduling.occurrence_task(first[1])["owner"] == "chris"
        assert first[1]["delay_minutes"] == 60
        assert second[1]["delay_minutes"] == 15
        scheduling.toggle(person(), first[1], True, early=True)
        assert scheduling.completion(first[1])["points"] == child["completion_points"]
        with pytest.raises(PermissionError):
            scheduling.toggle(person(), second[1], True, early=True)
        assert not scheduling.completion(second[0])


def test_cancel_preserves_completed_work_and_cannot_be_bypassed_by_stale_row(app):
    clock(app)
    with app.app_context():
        root = laundry()
        wash, dry, fold = start(root)
        with pytest.raises(PermissionError):
            scheduling.cancel_run(person("andrea"), wash["run_id"])
        scheduling.cancel_run(person(), wash["run_id"])
        assert scheduling.completion(wash)
        with pytest.raises(ValueError, match="no longer active"):
            scheduling.toggle(person(), dry, True, early=True)
        assert not scheduling.completion(dry)
        assert not scheduling.get_occurrence(fold["id"])["ready_at"]


def test_rescheduling_changes_only_this_run_and_respects_ownership(app):
    clock(app)
    with app.app_context():
        root = laundry()
        wash, dry, fold = start(root)
        with pytest.raises(PermissionError):
            scheduling.reschedule(person("andrea"), dry, 90)
        with pytest.raises(ValueError):
            scheduling.reschedule(person(), fold, 90)
        scheduling.reschedule(person(), dry, 90)
        assert scheduling.local(scheduling.get_occurrence(dry["id"])["ready_at"]).strftime("%H:%M") == "11:30"
        assert scheduling.recipe(root["id"])[0]["delay"] == 60
        assert not scheduling.get_occurrence(fold["id"])["ready_at"]


@pytest.mark.parametrize("instant,expected", [
    ("2026-11-11T23:30:00-05:00", "2026-11-12T00:30:00-05:00"),
    ("2027-03-14T01:30:00-05:00", "2027-03-14T03:30:00-04:00"),
    ("2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"),
])
def test_delays_are_elapsed_time_across_midnight_and_both_dst_changes(app, instant, expected):
    clock(app, instant)
    with app.app_context():
        _, dry, _ = start(laundry())
        assert scheduling.local(dry["ready_at"]).isoformat() == expected


def test_selected_root_uses_the_same_occurrence_and_json_is_idempotent(app):
    clock(app)
    with app.app_context():
        selected("2", "2026-11-11")
        root = laundry("selected")
        scheduling.ensure_selected()
        occurrence_id = scheduling.resolve(root)["id"]
    client = app.test_client()
    csrf = login(client, "chris")
    data = {"csrf": csrf, "done": "1", "occurrence_id": occurrence_id, "period": "S2026-11-11"}
    for _ in range(2):
        response = client.post(f"/tasks/{root['id']}/toggle", data=data, headers={"Accept": "application/json"})
        assert response.status_code == 200
        assert response.json["occurrence_id"] == occurrence_id
        assert response.json["next_step"]["name"] == "Dry the Laundry"
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM sequence_runs").fetchone()[0] == 1
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 1
        assert len(scheduling.run_rows(scheduling.get_occurrence(occurrence_id)["run_id"])) == 3


def test_manual_run_retry_uses_one_run_and_distinct_tokens_allow_overlaps(app):
    clock(app)
    with app.app_context():
        root = laundry()
    client = app.test_client()
    csrf = login(client, "chris")
    first = client.post(f"/tasks/{root['id']}/start-run", data={"csrf": csrf, "run_token": "a" * 32})
    retry = client.post(f"/tasks/{root['id']}/start-run", data={"csrf": csrf, "run_token": "a" * 32})
    assert first.location == retry.location
    assert client.get(first.location).status_code == 200
    assert client.post(f"/tasks/{root['id']}/start-run", data={"csrf": csrf}).status_code == 400
    second = client.post(f"/tasks/{root['id']}/start-run", data={"csrf": csrf, "run_token": "b" * 32})
    assert first.location != second.location
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM sequence_runs").fetchone()[0] == 2
        assert not db.get_db().execute("SELECT * FROM completions").fetchall()


def test_editor_saves_weekends_and_new_followups_with_validation(app):
    clock(app)
    client = app.test_client()
    csrf = login(client, "chris")
    with app.app_context():
        task = set_task("H01", owner="chris")
        form = form_for(task, frequency="selected", csrf=csrf)
    for k, values in {"selected_days": ["5", "6"], "step_task": ["", ""], "step_name": ["Dry the Laundry", "Fold the Laundry"],
                      "step_delay": ["60", "60"], "step_hours": ["2", "2"]}.items():
        form.setlist(k, values)
    assert client.post(f"/manage/tasks/{task['id']}", data=form).status_code == 302
    with app.app_context():
        saved = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        assert saved["selected_days"] == "5,6"
        assert len(scheduling.recipe(task["id"])) == 2
    form.setlist("selected_days", [])
    response = client.post(f"/manage/tasks/{task['id']}", data=form)
    assert response.status_code == 200 and b"Choose at least one day" in response.data
    assert b'value="6"' in response.data


def test_new_task_can_create_its_sequence_before_it_has_a_task_id(app):
    clock(app)
    client = app.test_client()
    csrf = login(client, "chris")
    form = MultiDict({"csrf": csrf, "code": "QA01", "grp": "Home", "name": "Wash the Laundry",
                      "frequency": "selected", "owner": "chris", "active": "1", "selected_days": "6",
                      "sequence_editor": "1", "step_task": "", "step_name": "Dry the Laundry",
                      "step_delay": "60", "step_hours": "2"})
    response = client.post("/manage/tasks/new", data=form)
    assert response.status_code == 302
    with app.app_context():
        task = db.get_db().execute("SELECT * FROM tasks WHERE code='QA01'").fetchone()
        assert len(scheduling.recipe(task["id"])) == 1


def test_future_occurrence_deep_link_and_admin_view(app):
    clock(app)
    with app.app_context():
        task = selected("6", "2026-11-11")
        scheduling.ensure_selected()
        future = db.get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND scheduled_date='2026-12-06'", (task["id"],)).fetchone()[0]
    for user in ("chris", "admin"):
        client = app.test_client()
        login(client, user)
        response = client.get(f"/occurrences/{future}", follow_redirects=True)
        assert response.status_code == 200
        assert f'id="occurrence-{future}"'.encode() in response.data


def test_sequence_reminders_are_opt_in_deduplicated_and_survive_restart(app):
    clock(app)
    with app.app_context(), patch("chores.push.send_one", return_value="ok") as sent:
        root = laundry(owner="shared")
        _, dry, _ = start(root)
        subscribe("chris")
        subscribe("andrea", "https://web.push.apple.com/second")
        clock(app, "2026-11-11T11:00:00-05:00")
        assert reminders.run_due() == 0
        db.get_db().execute("INSERT INTO sequence_subscriptions VALUES (?,?)", (root["id"], uid("chris")))
        db.get_db().commit()
        assert reminders.run_due() == 1
        db.init_schema(db.get_db())
        assert reminders.run_due() == 0
        assert sent.call_count == 1
        assert f"/occurrences/{dry['id']}" in str(sent.call_args)


def test_selected_reminder_is_for_one_date_and_skips_completed_date(app):
    clock(app, "2026-11-14T10:00:00-05:00")
    with app.app_context(), patch("chores.push.send_one", return_value="ok") as sent:
        task = selected("5,6", "2026-11-14")
        subscribe("chris")
        reminders.add(uid("chris"), task["id"], 60, "minutes", "")
        clock(app, "2026-11-14T19:00:00-05:00")
        assert reminders.run_due() == 1
        assert reminders.run_due() == 0
        scheduling.toggle(person(), scheduling.resolve(task), True)
        clock(app, "2026-11-15T10:00:00-05:00")
        scheduling.toggle(person(), scheduling.resolve(task), True)
        clock(app, "2026-11-15T19:00:00-05:00")
        assert reminders.run_due() == 0
        assert sent.call_count == 1


def test_siri_advances_ready_step_and_uses_oldest_ready_load(app):
    clock(app)
    with app.app_context():
        root = laundry()
    client = app.test_client()
    key = make_key(app, "chris")
    status, reply = say(client, key, "wash laundry")
    assert status == 200 and reply["ok"] and "Dry the Laundry will be ready" in reply["speech"]
    _, reply = say(client, key, "dry the laundry")
    assert not reply["ok"]  # it is still waiting
    clock(app, "2026-11-11T11:20:00-05:00")
    _, reply = say(client, key, "dry the laundry")
    assert reply["ok"] and "12:20 pm" in reply["speech"]
    with app.app_context():
        for key in ("Rsecond", "Rthird"):
            wash, _, _ = start(root, key)
            scheduling.toggle(person(), wash, True)
    clock(app, "2026-11-11T13:00:00-05:00")
    _, reply = say(client, key=make_key(app, "chris"), text="dry the laundry")
    assert reply["ok"] and "oldest ready load" in reply["speech"]
    with app.app_context():
        assert len(scheduling.candidates(scheduling.recipe(root["id"])[0]["task_id"])) == 1


def test_csv_round_trip_keeps_selected_days_and_sequence_links(app):
    clock(app)
    with app.app_context():
        selected("5,6")
        root = laundry("selected")
    client = app.test_client()
    login(client, "chris")
    response = client.get("/manage/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    with app.app_context():
        before = scheduling.recipe(root["id"])
        _, errors = cms.apply_import(rows)
        assert not errors
        assert scheduling.recipe(root["id"]) == before
        assert db.get_db().execute("SELECT selected_days FROM tasks WHERE id=?", (root["id"],)).fetchone()[0] == "5,6"


def test_v2_migration_preserves_all_existing_tables_and_can_be_repeated(tmp_path):
    conn = db.connect(tmp_path / "v2.sqlite3")
    old_tasks = db.TASKS_TABLE.replace(", 'selected', 'sequence', 'interval', 'yearly'", "")
    old_tasks = old_tasks[:old_tasks.index("    selected_days")] + ");"
    old_tasks = "\n".join(line for line in old_tasks.splitlines() if "selected_" not in line)
    old_tasks = old_tasks.replace("due_time     TEXT,", "due_time     TEXT")
    conn.executescript(old_tasks.format(name="tasks") + "CREATE TABLE IF NOT EXISTS users" + db.SCHEMA.split("CREATE TABLE IF NOT EXISTS users", 1)[1])
    conn.execute("INSERT INTO meta VALUES ('schema_version','2')")
    conn.execute("INSERT INTO users(id,username,display_name,password_hash,role) VALUES (1,'chris','Chris','x','member')")
    conn.execute("INSERT INTO tasks(id,code,grp,name,frequency,owner,due_time) VALUES (1,'H01','Home','Wash','daily','chris','20:00')")
    conn.execute("INSERT INTO completions VALUES (1,1,1,'D2026-11-09','2026-11-09','x','chris',17)")
    conn.execute("INSERT INTO reminders VALUES (1,1,1,60,'minutes','','2026-11-09T10:00:00')")
    conn.execute("INSERT INTO reminder_log VALUES (1,'D2026-11-09','x')")
    conn.execute("INSERT INTO api_tokens(id,user_id,label,token_hash) VALUES (1,1,'phone','abc')")
    conn.commit()
    for _ in range(2):
        db.init_schema(conn)
        assert conn.execute("SELECT points FROM completions").fetchone()[0] == 17
        assert conn.execute("SELECT due_time FROM tasks").fetchone()[0] == "20:00"
        assert conn.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0] == 1
        assert conn.execute("SELECT token_hash FROM api_tokens").fetchone()[0] == "abc"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.execute("UPDATE tasks SET frequency='selected',selected_days='5,6'")
    conn.close()


def test_concurrent_retries_create_one_run_and_one_shared_completion(app):
    clock(app)
    with app.app_context():
        root = laundry(owner="shared")
        root_id = root["id"]
    barrier = Barrier(2)
    def run_retry(_):
        with app.app_context():
            task = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (root_id,)).fetchone()
            barrier.wait(timeout=10)
            with db.get_db():
                return scheduling.start_run(task, "Rsame-request")
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run_retry, range(2)))
    assert ids[0] == ids[1]
    with app.app_context():
        occurrence_id = scheduling.run_rows(ids[0])[0]["occurrence_id"]
    barrier = Barrier(2)
    def complete(name):
        with app.app_context():
            occurrence = scheduling.get_occurrence(occurrence_id)
            user = person(name)
            barrier.wait(timeout=10)
            return scheduling.toggle(user, occurrence, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(complete, ("chris", "andrea")))
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 1
        assert db.get_db().execute("SELECT activation FROM task_occurrences WHERE run_id=? AND position=1", (ids[0],)).fetchone()[0] == 1


def test_template_edit_keeps_undone_selected_run_and_archiving_keeps_snapshots(app):
    clock(app)
    with app.app_context():
        selected("2", "2026-11-11")
        root = laundry("selected")
        wash, dry, _ = start(root)
        scheduling.toggle(person(), wash, False)
        set_task("H01", selected_days="6", active=0)
        scheduling.sync_selected(root["id"])
        db.get_db().commit()
        scheduling.ensure_selected()
        assert scheduling.get_occurrence(wash["id"])["state"] == "open"
        scheduling.toggle(person(), wash, True)
        assert scheduling.get_occurrence(dry["id"])["ready_at"]


def test_failed_sequence_start_rolls_back_completion_and_run(app):
    clock(app)
    with app.app_context():
        root = laundry()
        child_id = scheduling.recipe(root["id"])[0]["task_id"]
        db.get_db().execute("UPDATE tasks SET active=0 WHERE id=?", (child_id,))
        db.get_db().commit()
        with pytest.raises(ValueError, match="archived"):
            logic.toggle(person(), root, desired_done=True)
        assert not db.get_db().execute("SELECT * FROM completions").fetchall()
        assert not db.get_db().execute("SELECT * FROM sequence_runs").fetchall()


def test_ready_notification_retries_failures_but_never_notifies_cancelled_runs(app):
    clock(app)
    with app.app_context(), patch("chores.push.deliver", return_value={"ok": 0, "error": 1}) as deliver:
        root = laundry()
        wash, dry, _ = start(root)
        subscribe("chris")
        db.get_db().execute("INSERT INTO sequence_subscriptions VALUES (?,?)", (root["id"], uid("chris")))
        db.get_db().commit()
        clock(app, "2026-11-11T11:00:00-05:00")
        assert reminders.run_due() == 0
        deliver.return_value = {"ok": 1, "error": 0}
        assert reminders.run_due() == 1
        assert deliver.call_count == 2
        scheduling.reschedule(person(), dry, 0)
        scheduling.cancel_run(person(), wash["run_id"])
        assert reminders.run_due() == 0
        assert deliver.call_count == 2


def test_tomorrows_waiting_step_stays_visible_without_counting_against_today(app):
    clock(app, "2026-11-11T23:30:00-05:00")
    with app.app_context():
        db.get_db().execute("UPDATE tasks SET active=0")
        root = laundry()
        _, dry, _ = start(root)
        view = logic.task_views(logic.dashboard("chris"))["today"]
        assert dry["id"] in [row["occurrence_id"] for row in view["upcoming"]]
        assert view["progress"] == {"done": 1, "total": 1, "pct": 100}


def test_past_completions_do_not_inflate_todays_selected_day_progress(app):
    clock(app, "2026-11-14T10:00:00-05:00")
    with app.app_context():
        db.get_db().execute("UPDATE tasks SET active=0")
        task = selected("5,6", "2026-11-14")
        scheduling.toggle(person(), scheduling.resolve(task), True)
        clock(app, "2026-11-15T10:00:00-05:00")
        views = logic.task_views(logic.dashboard("chris"))
        assert views["today"]["progress"] == {"done": 0, "total": 1, "pct": 0}
        assert views["week"]["progress"] == {"done": 1, "total": 2, "pct": 50}
