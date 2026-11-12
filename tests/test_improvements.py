"""Household planning, resilient schedules, and everyday UI actions."""
import csv
import io
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from chores import cms, db, logic, operations, periods, planning, reminders, scheduling
from tests.test_app import app, login  # noqa: F401
from tests.test_reminders import set_task, subscribe, uid
from tests.test_scheduling import clock, laundry, selected, start
from tests.test_voice import make_key, say


def member(name="chris"):
    return logic.user_by_name(name)


def isolated():
    db.get_db().execute("UPDATE tasks SET active=0")
    db.get_db().commit()


def repeating(**values):
    return set_task("H01", active=1, owner="chris", frequency="interval", repeat_every=6,
                    repeat_unit="months", repeat_start="2026-10-31", hours_month=None, **values)


def test_interval_advances_from_actual_work_clamps_month_end_and_undo_restores(app):
    with app.app_context():
        task = repeating(minutes_each=20)
        first = periods.task_key(task)
        assert periods.repeat_due(task, periods.today()) == date(2026, 10, 31)
        logic.toggle(member(), task, desired_done=True)
        assert periods.repeat_due(task, periods.today()) == date(2027, 5, 11)
        assert logic.points_for(task, "chris") == 4
        assert logic.monthly_hours(task) == pytest.approx(20 / 360)
        logic.toggle(member(), task, desired_done=False, period_override=first)
        assert periods.task_key(task) == first
        assert periods.add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
        assert periods.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_interval_early_completions_have_distinct_cycles_and_receipts(app):
    with app.app_context():
        isolated()
        task = repeating()
        keys = []
        for _ in range(2):
            keys.append(periods.task_key(task))
            logic.toggle(member(), task, desired_done=True)
        assert keys[0] != keys[1]
        rows = logic.task_views(logic.dashboard("chris"))["today"]["completed"]
        assert len(rows) == 2 and len({r["row_key"] for r in rows}) == 2
        logic.toggle(member(), task, desired_done=False, period_override=keys[1])
        assert periods.task_key(task) == keys[1]


def test_yesterday_interval_form_retry_records_one_completion(app):
    client = app.test_client(); token = login(client, "chris")
    with app.app_context():
        task = repeating()
        key = periods.task_key(task)
    data = {"csrf":token,"action":"yesterday","period":key}
    for _ in range(2):
        assert client.post(f"/tasks/{task['id']}/plan",data=data).status_code == 302
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
        assert periods.repeat_due(task, periods.today()) == date(2027, 5, 10)


def test_yearly_fixed_date_with_leap_day_and_no_missed_stack(app):
    with app.app_context():
        task = set_task("H01", frequency="yearly", owner="chris", year_month=2, year_day=29,
                        repeat_start="2024-02-29", hours_month=None)
        assert periods.repeat_due(task, periods.today()) == date(2024, 2, 29)
        logic.toggle(member(), task, desired_done=True)
        assert periods.repeat_due(task, periods.today()) == date(2027, 2, 28)
        parsed = cms.parse_repeat("yearly", {"repeat_start":"2026-11-11", "year_month":"2", "year_day":"29"})
        assert parsed["repeat_start"] == "2027-02-28"
        with pytest.raises(ValueError):
            cms.parse_repeat("yearly", {"repeat_start":"2026-11-11", "year_month":"4", "year_day":"31"})


def test_today_sorting_and_daily_metadata_are_quiet(app):
    with app.app_context():
        isolated()
        untimed = set_task("H02", name="A daily job", active=1, owner="chris", frequency="daily", due_time=None)
        timed = set_task("H03", name="Z lunch", active=1, owner="chris", frequency="daily", due_time="08:00")
        overdue = set_task("H04", name="Old bill", active=1, owner="chris", frequency="once", due_date="2026-11-10")
        rows = logic.task_views(logic.dashboard("chris"))["today"]["open"]
        assert [r["task"]["id"] for r in rows] == [overdue["id"], timed["id"], untimed["id"]]
        assert rows[-1]["due"] is None


def test_backlog_collapses_clear_keeps_history_and_skips_can_restore(app):
    clock(app)
    with app.app_context():
        isolated()
        task = selected("0,1,2", "2026-11-02")
        scheduling.ensure_selected()
        first = scheduling.candidates(task["id"])[0]
        scheduling.toggle(member(), scheduling.get_occurrence(first["id"]), True)
        before = db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0]
        rows = logic.task_views(logic.dashboard("chris"))["today"]["open"]
        assert len(rows) == 1 and rows[0]["backlog_count"] == 4
        assert planning.clear_older(member(), task) == 4
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == before
        view = logic.task_views(logic.dashboard("chris"))["today"]
        assert len(view["open"]) == 1 and len(view["skipped"]) == 4
        skip = view["skipped"][0]
        planning.skip_task(member(), task, skip["period_key"], restore=True)
        assert len(scheduling.candidates(task["id"])) == 2
        with pytest.raises(PermissionError):
            planning.clear_older(member("andrea"), task)


def test_daily_skip_restore_and_yesterday_repair_points_and_streak(app):
    with app.app_context():
        isolated()
        task = set_task("H02", active=1, owner="chris", frequency="daily")
        key = periods.task_key(task)
        planning.skip_task(member(), task, key)
        row = logic.task_views(logic.dashboard("chris"))["today"]["skipped"][0]
        assert row["skip_user_id"] == uid("chris")
        assert logic.streak("chris") == 0
        with pytest.raises(ValueError, match="Restore"):
            logic.toggle(member(), task, desired_done=True)
        planning.skip_task(member(), task, key, restore=True)
        planning.yesterday(member(), task)
        planning.yesterday(member(), task)  # repeat submission never adds more points
        assert logic.streak("chris") == 1
        assert len(logic.task_views(logic.dashboard("chris"))["today"]["open"]) == 1
        assert db.get_db().execute("SELECT COUNT(*) FROM completions").fetchone()[0] == 1
        assert db.get_db().execute("SELECT done_on FROM completions").fetchone()[0] == "2026-11-10"


def test_selected_routines_and_away_days_protect_without_awarding_streak(app):
    with app.app_context():
        isolated()
        task = selected("0,2", "2026-11-09")
        scheduling.ensure_selected()
        monday = scheduling.candidates(task["id"])[0]
        scheduling.toggle(member(), scheduling.get_occurrence(monday["id"]), True)
        db.get_db().execute("INSERT INTO away_dates(user_id,starts_on,ends_on) VALUES (?,?,?)", (uid("chris"), "2026-11-11", "2026-11-12"))
        db.get_db().commit()
        assert logic.streak("chris") == 1
        assert len(logic.task_views(logic.dashboard("chris"))["today"]["open"]) == 1
        assert planning.is_away(uid("chris")) and not planning.is_away(uid("andrea"))


def test_quick_add_is_personal_idempotent_and_reachable_without_js(app):
    client = app.test_client()
    token = login(client, "chris")
    data = {"csrf":token, "request_key":"a" * 32, "name":"Book a boiler service", "due_date":"2026-11-15"}
    for _ in range(2):
        response = client.post("/quick-add", data=data)
        assert response.status_code == 302 and "view=once" in response.location
    with app.app_context():
        rows = db.get_db().execute("SELECT * FROM tasks WHERE name=?", (data["name"],)).fetchall()
        assert len(rows) == 1 and rows[0]["owner"] == "chris" and rows[0]["frequency"] == "once"
        with pytest.raises(PermissionError):
            planning.quick_add(member("admin"), "No admin chores")
        set_task("H03", active=1, owner="chris", frequency="other")
    page = client.get("/?view=other").get_data(as_text=True)
    panel = page.split('data-task-panel', 1)[1]
    assert 'role="progressbar"' not in panel and "Last done" in panel


def test_cover_credits_doer_notifies_once_and_keeps_partner_board(app):
    client = app.test_client()
    token = login(client, "chris")
    with app.app_context():
        isolated()
        task = set_task("H01", active=1, name="Andrea laundry", owner="andrea", frequency="daily")
    data = {"csrf":token, "done":"1", "cover":"1", "board_user":"andrea", "period":"D2026-11-11"}
    with patch("chores.voice._notify", return_value=True) as notify:
        for _ in range(2):
            response = client.post(f"/tasks/{task['id']}/toggle", data=data, headers={"Accept":"application/json"})
            assert response.status_code == 200 and response.json["done"]
            assert "Andrea laundry" in response.json["tasks_html"]
        assert notify.call_count == 1
    cid = response.json["completion_id"]
    with app.app_context():
        assert db.get_db().execute("SELECT user_id FROM completions WHERE id=?", (cid,)).fetchone()[0] == uid("chris")
    other = app.test_client(); other_token = login(other, "andrea")
    undo = {"done":"0", "period":"D2026-11-11", "completion_id":cid}
    assert other.post(f"/tasks/{task['id']}/toggle", data={**undo,"csrf":other_token}, headers={"Accept":"application/json"}).status_code == 403
    response = client.post(f"/tasks/{task['id']}/toggle", data={**undo,"csrf":token}, headers={"Accept":"application/json"})
    assert response.status_code == 200 and not response.json["done"]


def test_interval_receipt_undo_and_stale_completion_request(app):
    client = app.test_client(); token = login(client, "chris")
    with app.app_context():
        task = repeating()
        key = periods.task_key(task)
    payload = {"csrf":token, "done":"1", "period":key}
    url = f"/tasks/{task['id']}/toggle"
    first = client.post(url, data=payload, headers={"Accept":"application/json"})
    assert first.json["done"]
    assert client.post(url, data=payload, headers={"Accept":"application/json"}).status_code == 409
    undo = {**payload, "done":"0", "completion_id":first.json["completion_id"]}
    assert not client.post(url, data=undo, headers={"Accept":"application/json"}).json["done"]
    with app.app_context():
        assert periods.task_key(task) == key


def test_evening_summary_opt_in_deduplication_retry_and_away(app):
    clock(app, "2026-11-11T18:00:00-05:00")
    with app.app_context(), patch("chores.push.send_one", return_value="ok") as sent:
        subscribe("chris")
        assert reminders.run_due() == 0
        db.get_db().execute("INSERT INTO evening_preferences VALUES (?, '18:00')", (uid("chris"),))
        db.get_db().commit()
        assert reminders.run_due() == 1
        assert reminders.run_due() == 0 and sent.call_count == 1
        clock(app, "2026-11-12T18:00:00-05:00")
        db.get_db().execute("INSERT INTO away_dates(user_id,starts_on,ends_on) VALUES (?, '2026-11-12','2026-11-12')", (uid("chris"),))
        db.get_db().commit()
        assert reminders.run_due() == 0


def test_away_suppresses_only_personal_reminders_and_sequence_notices(app):
    clock(app)
    with app.app_context(), patch("chores.push.send_one", return_value="ok") as sent:
        isolated()
        task = set_task("H02", active=1, owner="shared", frequency="daily", due_time="11:00")
        for name in ("chris", "andrea"):
            subscribe(name, f"https://web.push.apple.com/{name}")
            reminders.add(uid(name), task["id"], 0, "days", "11:00")
        root = laundry(owner="shared")
        start(root)
        for name in ("chris", "andrea"):
            db.get_db().execute("INSERT INTO sequence_subscriptions VALUES (?,?)", (root["id"],uid(name)))
        db.get_db().execute("INSERT INTO away_dates(user_id,starts_on,ends_on) VALUES (?, '2026-11-11','2026-11-11')", (uid("chris"),))
        db.get_db().commit()
        clock(app, "2026-11-11T11:00:00-05:00")
        assert reminders.run_due() == 2
        assert sent.call_count == 2
        assert all(call.args[0]["user_id"] == uid("andrea") for call in sent.call_args_list)


def test_aliases_irregular_verbs_put_away_and_siri_quick_add(app):
    with app.app_context():
        for code in ("H22", "H27", "H11"):
            set_task(code, owner="chris")
    key = make_key(app, "chris"); client = app.test_client()
    for text, expected in (("walked the dog","Dog Daily Care"), ("changed the filters","Air & Furnace Filters"), ("tidied up","Daily Tidying")):
        _, reply = say(client, key, text)
        assert reply["ok"] and reply["task"] == expected, reply
    with app.app_context():
        set_task("H01", active=1, owner="chris", name="Laundry", frequency="other")
        set_task("H03", active=1, owner="chris", name="Put Away the Laundry", frequency="other")
        set_task("H04", active=1, owner="chris", name="Dry the Laundry", frequency="other")
    for phrase, expected in (("put away laundry","Put Away the Laundry"),("dried the laundry","Dry the Laundry")):
        _, reply = say(client, key, phrase)
        assert reply["ok"] and reply["task"] == expected
    _, reply = say(client, key, "add a chore book the plumber")
    assert reply["ok"] and reply["action"] == "added"


def test_csv_round_trip_keeps_repeat_nicknames_and_minutes(app):
    with app.app_context():
        task = repeating(aliases="change the filters\nfilter change", minutes_each=17.5)
    client = app.test_client(); login(client,"chris")
    rows = list(csv.DictReader(io.StringIO(client.get("/manage/export.csv").get_data(as_text=True))))
    with app.app_context():
        _, errors = cms.apply_import(rows)
        assert errors == []
        saved = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        for field in ("repeat_start","repeat_every","repeat_unit","aliases","minutes_each"):
            assert saved[field] == task[field]


def test_admin_operational_status_and_add_missed_phrase_as_alias(app):
    with app.app_context():
        operations.record(backup_at="2026-11-10T12:00:00+00:00", notify_sent=3)
        path = Path(app.config["DATABASE"]).parent / "update-status.json"
        path.write_text(json.dumps({"checked_at":"2026-11-11T14:00:00Z","status":"Up to date.","deployed":"abc1234"}), encoding="utf-8-sig")
        db.get_db().execute("INSERT INTO voice_misses(user_id,phrase,heard_at) VALUES (?, 'sweep the den', '2026-11-11T12:00:00+00:00')", (uid("chris"),))
        mid = db.get_db().execute("SELECT id FROM voice_misses").fetchone()[0]
        task = set_task("H02", active=1)
    client = app.test_client(); token = login(client,"admin")
    page = client.get("/admin/").get_data(as_text=True)
    assert "Up to date." in page and "Registered notification devices" in page
    response = client.post(f"/admin/voice-misses/{mid}/alias", data={"csrf":token,"task_id":task["id"]})
    assert response.status_code == 302
    with app.app_context():
        assert "sweep the den" in db.get_db().execute("SELECT aliases FROM tasks WHERE id=?",(task["id"],)).fetchone()[0]
        path.write_text("corrupted",encoding="utf-8")
        assert "update_error" in operations.status()


def test_two_phones_cannot_advance_same_interval_twice(app):
    with app.app_context():
        task = repeating()
        key = periods.task_key(task)
        tid = task["id"]
    barrier = Barrier(2)
    def finish(_):
        with app.app_context():
            task = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            barrier.wait(timeout=10)
            try:
                logic.toggle(member(), task, desired_done=True, expected_period=key)
                return "saved"
            except ValueError:
                return "refresh"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, range(2))) == ["refresh", "saved"]
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM completions WHERE task_id=?", (tid,)).fetchone()[0] == 1


def test_schema_three_upgrade_keeps_runs_reminders_and_completions(app):
    with app.app_context():
        root = laundry()
        wash, dry, fold = start(root)
        reminders.add(uid("chris"), root["id"], 1, "days", "09:00")
        conn = db.get_db()
        # Recreate the previous release's task shape, preserving its child tables.
        table = db.TASKS_TABLE[:db.TASKS_TABLE.index("    aliases")].rstrip().rstrip(",") + "\n);"
        table = table.replace(", 'interval', 'yearly'", "")
        columns = ",".join(r[1] for r in conn.execute("PRAGMA table_info(tasks)") if r[1] not in ("aliases","minutes_each","repeat_every","repeat_unit","repeat_start","year_month","year_day"))
        conn.commit(); conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("BEGIN;" + table.format(name="old_tasks") + f"INSERT INTO old_tasks({columns}) SELECT {columns} FROM tasks;"
                           "DROP TABLE tasks; ALTER TABLE old_tasks RENAME TO tasks; UPDATE meta SET value='3' WHERE key='schema_version'; COMMIT;")
        conn.execute("PRAGMA foreign_keys=ON")
        for _ in range(2):
            db.init_schema(conn)
            assert scheduling.completion(wash)
            assert scheduling.get_occurrence(dry["id"])["ready_at"] == dry["ready_at"]
            assert len(scheduling.recipe(root["id"])) == 2
            assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_grid_owner_change_keeps_minutes_and_schedule_change_recalculates(app):
    client = app.test_client(); token = login(client, "chris")
    with app.app_context():
        task = set_task("H02", minutes_each=10, hours_month=5.07, frequency="daily")
        tid = task["id"]
    data = {"csrf":token,"id":tid,f"owner_{tid}":"andrea",f"frequency_{tid}":"weekly",f"hours_{tid}":"5.07",f"active_{tid}":"1"}
    assert client.post("/manage/tasks/bulk",data=data).status_code == 302
    with app.app_context():
        saved = db.get_db().execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        assert saved["minutes_each"] == 10 and saved["owner"] == "andrea"
        assert saved["hours_month"] == .72
        assert logic.monthly_hours(saved) == pytest.approx(.725)


def test_operational_records_follow_real_cli_jobs(app, tmp_path, capsys):
    import manage
    manage.cmd_backup(app, SimpleNamespace(dir=str(tmp_path / "backups"),keep=1))
    backup_path = Path(capsys.readouterr().out.strip())
    assert backup_path.is_file()
    with patch("chores.reminders.run_due",return_value=2):
        manage.cmd_notify(app,SimpleNamespace())
    with app.app_context():
        status = operations.status()
        assert status["backup_file"] == str(backup_path.resolve()) and status["notify_sent"] == "2"
        assert status["notify_started_at"] and status["notify_finished_at"]
    with patch("chores.reminders.run_due",side_effect=RuntimeError("simulated offline job")):
        with pytest.raises(RuntimeError):
            manage.cmd_notify(app,SimpleNamespace())
    with app.app_context():
        assert operations.status()["notify_error"] == "RuntimeError"


def test_new_repeat_schedules_remain_available_as_volunteer_work(app):
    client = app.test_client(); token = login(client,"chris")
    with app.app_context():
        isolated()
        interval = repeating()
        interval = set_task("H01", owner="volunteer", name="Quarterly filter change")
        yearly = set_task("H02", active=1, frequency="yearly", owner="volunteer", name="Annual holiday cards",
                          repeat_start="2026-11-11",year_month=11,year_day=11,hours_month=None)
        key = periods.task_key(yearly)
    page = client.get("/compare").get_data(as_text=True)
    assert "Quarterly filter change" in page and "Annual holiday cards" in page
    response = client.post(f"/tasks/{yearly['id']}/toggle",data={"csrf":token,"done":"1","period":key,"surface":"together"},headers={"Accept":"application/json"})
    assert response.json["done"] and response.json["points"] == 15
    assert "Undo completion" in response.json["volunteer_html"]
    with app.app_context():
        planning.skip_task(member(), interval, periods.task_key(interval))
    page = client.get("/compare").get_data(as_text=True)
    assert "Restore task" in page and "Not needed this time" in page


def test_sequence_editor_converts_days_and_active_minutes_to_frozen_points(app):
    from werkzeug.datastructures import MultiDict
    with app.app_context():
        root = set_task("H01", active=1, owner="chris", frequency="weekly")
        form = MultiDict({"sequence_editor":"1","step_task":"","step_name":"Put away laundry","step_delay":"2",
                          "step_delay_unit":"days","step_hours":"","step_minutes":"15"})
        steps = scheduling.parse_recipe(form,root["id"])
        scheduling.save_recipe(root,steps);db.get_db().commit()
        wash, put_away = start(root)
        task = scheduling.occurrence_task(put_away)
        assert put_away["delay_minutes"] == 2880 and task["minutes_each"] == 15
        assert task["completion_points"] == 3
        assert periods.duration(2880) == "2 days"
