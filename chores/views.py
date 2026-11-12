"""Member-facing pages: dashboard, a person's detail view, scoreboard, reminders and push."""
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
import re

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from . import logic, periods, push, reminders, voice, scheduling, planning
from .auth import login_required, safe_next
from .db import get_db

bp = Blueprint("main", __name__)


def _back():
    target = request.form.get("return_to") or request.form.get("back")
    if not target and request.referrer:
        ref = urlsplit(request.referrer)
        if ref.netloc == request.host:
            target = ref.path + ("?" + ref.query if ref.query else "")
    return safe_next(target or url_for("main.home"))


@bp.get("/")
@login_required
def home():
    if g.user["role"] == "admin":
        return redirect(url_for("admin.system"))
    return person(g.user["username"])


@bp.get("/people/<username>")
@login_required
def person(username):
    who = logic.user_by_name(username)
    if not who or who["role"] != "member":
        abort(404)
    d = periods.today()
    dash = logic.dashboard(who["username"], d)
    own = g.user["id"] == who["id"]
    views = logic.task_views(dash, d)
    selected = request.args.get("view", "today")
    if selected not in views:
        selected = "today"
    return render_template(
        "dashboard.html",
        nags=reminders.task_ids_for(g.user["id"]),
        who=who, own=own, dash=dash, today=d,
        away=planning.is_away(who["id"], d),
        task_views=views, selected=selected, current_view=views[selected],
    )


@bp.post("/tasks/<int:task_id>/toggle")
@login_required
def toggle(task_id):
    task = _task_or_404(task_id)
    wants_json = request.headers.get("Accept", "").startswith("application/json")
    state = request.form.get("done")
    if state not in (None, "0", "1"):
        abort(400, "Choose whether this task is done.")
    occurrence_id = request.form.get("occurrence_id", type=int)
    occurrence = scheduling.get_occurrence(occurrence_id) if occurrence_id else None
    if occurrence_id and (not occurrence or occurrence["task_id"] != task_id):
        abort(404)
    expected_period = request.form.get("period")
    undo_id = request.form.get("completion_id", type=int) if state == "0" else None
    undo = get_db().execute("SELECT * FROM completions WHERE id=? AND task_id=?", (undo_id, task_id)).fetchone() if undo_id else None
    if undo_id and undo and undo["user_id"] != g.user["id"]:
        return (jsonify(ok=False, error="Only the person who crossed this off can undo it."), 403) if wants_json else abort(403)
    override = undo["period_key"] if undo else expected_period if undo_id and expected_period else None
    if override and state == "0":
        linked = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?", (task_id, override)).fetchone()
        if linked:
            occurrence_id = linked["id"]
            occurrence = scheduling.get_occurrence(occurrence_id)
    actual_period = override or (occurrence["occurrence_key"] if occurrence else periods.task_key(task))
    if expected_period and expected_period != actual_period:
        message = "This task's schedule has moved on. Refresh your list before changing it."
        if wants_json:
            return jsonify(ok=False, error=message), 409
        flash(message, "error")
        return redirect(_back())
    cover = request.form.get("cover") == "1"
    effective = scheduling.occurrence_task(occurrence) if occurrence else task
    if cover and not logic.can_steal(g.user, effective):
        abort(403)
    before = get_db().execute("SELECT id FROM completions WHERE task_id=? AND period_key=?", (task_id, actual_period)).fetchone()
    try:
        if not occurrence_id and task["frequency"] in ("selected", "sequence"):
            occurrence = scheduling.resolve(task)
            occurrence_id = occurrence["id"]
        logic.toggle(g.user, task, desired_done=None if state is None else state == "1", occurrence_id=occurrence_id,
                     early=request.form.get("early") == "1", steal=cover, period_override=override,
                     expected_period=actual_period if expected_period else None)
    except (PermissionError, ValueError) as e:
        if wants_json:
            return jsonify(ok=False, error=str(e)), 403 if isinstance(e, PermissionError) else 409
        flash(str(e), "error")
        return redirect(_back())
    if cover and not before:
        completed = get_db().execute("SELECT id,user_id FROM completions WHERE task_id=? AND period_key=?", (task_id, actual_period)).fetchone()
        voice.notify_cover(g.user, effective, completed)
    if wants_json:
        d = periods.today()
        board_user = logic.user_by_name(request.form.get("board_user", g.user["username"]))
        if not board_user or board_user["role"] != "member":
            board_user = g.user
        own = board_user["id"] == g.user["id"]
        dash = logic.dashboard(board_user["username"], d)
        views = logic.task_views(dash, d)
        selected = request.form.get("view", "today")
        if selected not in views:
            selected = "today"
        linked = get_db().execute("SELECT id FROM task_occurrences WHERE task_id=? AND occurrence_key=?", (task_id, actual_period)).fetchone()
        used_occurrence = scheduling.get_occurrence(occurrence_id or linked["id"]) if occurrence_id or linked else None
        used_key = actual_period
        completion = get_db().execute(
            "SELECT c.id,c.points, c.user_id, u.display_name AS doer FROM completions c JOIN users u ON u.id=c.user_id"
            " WHERE c.task_id=? AND c.period_key=?", (task_id, used_key)).fetchone()
        next_step = None
        if used_occurrence and used_occurrence["run_id"] and completion:
            following = get_db().execute("SELECT * FROM task_occurrences WHERE run_id=? AND position=?",
                                         (used_occurrence["run_id"], used_occurrence["position"] + 1)).fetchone()
            if following and following["ready_at"]:
                ready = scheduling.local(following["ready_at"])
                next_step = {"name": scheduling.occurrence_task(following)["name"], "at": ready.strftime('%a ') + periods.format_time(ready.time())}
        today = views["today"]["progress"]
        return jsonify(ok=True, done=bool(completion), points=completion["points"] if completion else 0,
                       by=completion["doer"] if completion else g.user["display_name"],
                       can_undo=bool(completion and completion["user_id"] == g.user["id"]),
                       today=today,
                       counts={key: view["remaining"] for key, view in views.items()},
                       occurrence_id=used_occurrence["id"] if used_occurrence else None, next_step=next_step,
                       completion_id=completion["id"] if completion else None,
                       tasks_html=render_template("_task_list.html", current_view=views[selected], selected=selected,
                                                  own=own, nags=reminders.task_ids_for(g.user["id"]),
                                                  task_back=request.form.get("return_to", "/")),
                       volunteer_html=render_template("_volunteers.html", dash=dash,
                                                      nags=reminders.task_ids_for(g.user["id"]),
                                                      task_back=request.form.get("return_to", "/compare"))
                       if request.form.get("surface") == "together" else None,
                       name=task["name"])
    return redirect(_back())


@bp.post("/quick-add")
@login_required
def quick_add():
    try:
        nonce = request.form.get("request_key", "")
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise ValueError("Refresh the page and try adding your task again.")
        tid = planning.quick_add(g.user, request.form.get("name", ""), request.form.get("due_date", ""), nonce)
    except (ValueError, PermissionError) as e:
        flash(str(e), "error")
        return redirect(_back())
    flash("Added to your One-off list.", "ok")
    return redirect(url_for("main.home", view="once", _anchor=f"task-{tid}"))


@bp.post("/tasks/<int:task_id>/plan")
@login_required
def task_plan(task_id):
    task = _task_or_404(task_id)
    occurrence_id = request.form.get("occurrence_id", type=int)
    occurrence = scheduling.get_occurrence(occurrence_id) if occurrence_id else None
    if occurrence_id and (not occurrence or occurrence["task_id"] != task_id):
        abort(404)
    action = request.form.get("action")
    try:
        if action == "clear-older":
            count = planning.clear_older(g.user, task)
            flash(f"Cleared {count} older date{'s' if count != 1 else ''}. No points added.", "ok")
        elif action == "yesterday":
            planning.yesterday(g.user, task, request.form.get("period"))
            flash("Recorded for yesterday. Today's task stays separate.", "ok")
        elif action in ("skip", "restore"):
            planning.skip_task(g.user, task, request.form.get("period", ""), occurrence, restore=action == "restore")
            flash("Task restored." if action == "restore" else "Not needed this time. No points added.", "ok")
        else:
            abort(400)
    except (ValueError, PermissionError) as e:
        flash(str(e), "error")
    return redirect(_back())


@bp.post("/away")
@login_required
def away():
    if g.user["role"] != "member":
        abort(403)
    try:
        if request.form.get("delete"):
            get_db().execute("DELETE FROM away_dates WHERE id=? AND user_id=?", (request.form.get("delete", type=int), g.user["id"]))
        else:
            starts, ends = date.fromisoformat(request.form.get("starts_on", "")), date.fromisoformat(request.form.get("ends_on", ""))
            if ends < starts or (ends - starts).days > 366:
                raise ValueError("Choose an away range of up to one year.")
            get_db().execute("INSERT INTO away_dates(user_id,starts_on,ends_on) VALUES (?,?,?)", (g.user["id"], starts.isoformat(), ends.isoformat()))
        get_db().commit()
        flash("Away dates saved. Your tasks stay available for coverage.", "ok")
    except ValueError:
        flash("Choose valid start and end dates, up to one year apart.", "error")
    return redirect(url_for("main.more", _anchor="away"))


@bp.post("/evening-summary")
@login_required
def evening_summary():
    if g.user["role"] != "member":
        abort(403)
    try:
        if request.form.get("enabled") == "1":
            at = datetime.strptime(request.form.get("at_time", ""), "%H:%M").strftime("%H:%M")
            get_db().execute("INSERT INTO evening_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET at_time=excluded.at_time", (g.user["id"], at))
        else:
            get_db().execute("DELETE FROM evening_preferences WHERE user_id=?", (g.user["id"],))
        get_db().commit()
        flash("Evening summary preference saved.", "ok")
    except ValueError:
        flash("Choose a valid time for your evening summary.", "error")
    return redirect(url_for("main.more", _anchor="evening-summary"))


@bp.get("/occurrences/<int:occurrence_id>")
@login_required
def occurrence_link(occurrence_id):
    occurrence = scheduling.get_occurrence(occurrence_id)
    if not occurrence:
        abort(404)
    if occurrence["run_id"]:
        return redirect(url_for("main.sequence_run", run_id=occurrence["run_id"], _anchor=f"occurrence-{occurrence_id}"))
    return render_template("occurrence.html", row=scheduling._display_row(occurrence),
                           nags=reminders.task_ids_for(g.user["id"]))


@bp.post("/occurrences/<int:occurrence_id>/skip")
@login_required
def skip_occurrence(occurrence_id):
    occurrence = scheduling.get_occurrence(occurrence_id)
    if not occurrence:
        abort(404)
    try:
        planning.skip_task(g.user, scheduling.occurrence_task(occurrence), occurrence["occurrence_key"], occurrence)
        flash("Not needed this time. No points were added.", "ok")
    except (ValueError, PermissionError) as e:
        flash(str(e), "error")
    return redirect(url_for("main.home"))


@bp.post("/tasks/<int:task_id>/start-run")
@login_required
def start_sequence(task_id):
    import re
    task = _task_or_404(task_id)
    if not logic.can_complete(g.user, task):
        abort(403)
    if not scheduling.recipe(task_id):
        abort(404)
    token = request.form.get("run_token", "")
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        abort(400, "Refresh the page before starting a run.")
    try:
        with get_db():
            run_id = scheduling.start_run(task, "R" + token)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("manage.task_edit", task_id=task_id))
    return redirect(url_for("main.sequence_run", run_id=run_id))


@bp.get("/sequences/<run_id>")
@login_required
def sequence_run(run_id):
    run = get_db().execute("SELECT * FROM sequence_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        abort(404)
    rows = scheduling.run_rows(run_id)
    root = _task_or_404(run["root_task_id"])
    return render_template("sequence_run.html", run=run, rows=rows,
                           can_control=logic.can_complete(g.user, rows[0]["task"]) if rows else False,
                           can_start=logic.can_complete(g.user, root) and bool(scheduling.recipe(root["id"])),
                           subscribed=bool(get_db().execute("SELECT 1 FROM sequence_subscriptions WHERE root_task_id=? AND user_id=?",
                                                           (run["root_task_id"], g.user["id"])).fetchone()),
                           nags=reminders.task_ids_for(g.user["id"]))


@bp.post("/occurrences/<int:occurrence_id>/reschedule")
@login_required
def reschedule_occurrence(occurrence_id):
    occurrence = scheduling.get_occurrence(occurrence_id)
    if not occurrence:
        abort(404)
    try:
        unit = request.form.get("unit", "minutes")
        if unit not in ("minutes", "hours", "days"):
            raise ValueError("Choose minutes, hours or days.")
        minutes = int(request.form.get("minutes", "")) * {"minutes": 1, "hours": 60, "days": 1440}[unit]
        scheduling.reschedule(g.user, occurrence, minutes)
        flash("Ready time updated for this run.", "ok")
    except (ValueError, PermissionError) as e:
        flash(str(e) or "Choose a whole number of minutes.", "error")
    return redirect(url_for("main.occurrence_link", occurrence_id=occurrence_id))


@bp.post("/sequences/<run_id>/cancel")
@login_required
def cancel_sequence(run_id):
    try:
        scheduling.cancel_run(g.user, run_id)
        flash("Remaining steps cancelled. Completed work has been kept.", "ok")
    except (ValueError, PermissionError) as e:
        flash(str(e), "error")
    return redirect(_back() if request.form.get("back") else url_for("main.sequence_run", run_id=run_id))


@bp.post("/tasks/<int:task_id>/sequence-reminders")
@login_required
def sequence_reminders(task_id):
    _task_or_404(task_id)
    if g.user["role"] != "member":
        abort(403)
    if request.form.get("enabled") == "1":
        get_db().execute("INSERT OR IGNORE INTO sequence_subscriptions VALUES (?,?)", (task_id, g.user["id"]))
    else:
        get_db().execute("DELETE FROM sequence_subscriptions WHERE root_task_id=? AND user_id=?", (task_id, g.user["id"]))
    get_db().commit()
    flash("Your sequence reminder preference was saved.", "ok")
    return redirect(request.referrer or url_for("main.more"))


def _task_or_404(task_id):
    task = get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        abort(404)
    return task


@bp.get("/tasks/<int:task_id>")
@login_required
def task_link(task_id):
    """A reminder deep link must find the task even when it isn't in Today."""
    task = _task_or_404(task_id)
    if g.user["role"] == "admin":
        return redirect(url_for("manage.task_edit", task_id=task_id))
    if not task["active"]:
        return redirect(url_for("manage.task_edit", task_id=task_id))
    if task["frequency"] not in ("selected", "sequence"):
        occurrence = scheduling.resolve(task)
        if occurrence:
            return redirect(url_for("main.occurrence_link", occurrence_id=occurrence["id"]))
    if task["frequency"] in ("selected", "sequence"):
        choices = scheduling.candidates(task_id) or scheduling.candidates(task_id, include_done=True)
        if choices:
            return redirect(url_for("main.occurrence_link", occurrence_id=choices[0]["id"]))
        return redirect(url_for("manage.task_edit", task_id=task_id))
    if task["owner"] == logic.VOLUNTEER:
        return redirect(url_for("main.compare", _anchor=f"task-{task_id}"))
    username = g.user["username"] if task["owner"] == logic.SHARED else task["owner"]
    if task["frequency"] in ("interval", "yearly"):
        row = next(r for r in logic.board([task["owner"]])[task["frequency"]] if r["task"]["id"] == task_id and not r["done"] and not r.get("skipped"))
        return render_template("occurrence.html", row=dict(row, scheduled_date=periods.due_date(task, periods.today())), nags=reminders.task_ids_for(g.user["id"]))
    selected = {"daily": "today", "weekly": "week", "monthly": "month", "once": "once", "other": "other"}[task["frequency"]]
    rows = logic.task_views(logic.dashboard(username))[selected]["rows"]
    if not any(r["task"]["id"] == task_id for r in rows):
        return redirect(url_for("manage.task_edit", task_id=task_id))
    return redirect(url_for("main.person", username=username, view=selected, _anchor=f"task-{task_id}"))


@bp.route("/tasks/<int:task_id>/remind", methods=["GET", "POST"])
@login_required
def remind(task_id):
    if g.user["role"] != "member":
        abort(403)
    task = _task_or_404(task_id)
    allowed = reminders.can_remind(task)
    if request.method == "POST" and allowed:
        try:
            presets = {"morning": (0, "days", "09:00"), "evening": (1, "days", "18:00"), "hour": (1, "hours", "")}
            preset = request.form.get("preset")
            if preset:
                if preset not in presets or (preset == "evening" and task["frequency"] == "daily"):
                    raise ValueError("Choose a reminder that fits this task's schedule.")
                amount, unit, at_time = presets[preset]
            else:
                amount, unit, at_time = reminders.parse(request.form, task)
        except ValueError as e:
            flash(str(e), "error")
        else:
            added = reminders.add(g.user["id"], task_id, amount, unit, at_time)
            flash("Reminder added." if added else "You already have that reminder.", "ok" if added else "error")
            return redirect(url_for("main.remind", task_id=task_id))
    return render_template("reminders.html", task=task, allowed=allowed,
                           items=reminders.for_task(g.user["id"], task_id),
                           due=periods.due_info(task, periods.now(), explicit_only=False),
                           has_device=bool(push.subscriptions_for(g.user["id"])),
                           vapid_public=push.public_key())


@bp.get("/progress")
@login_required
def progress():
    if g.user["role"] != "member":
        return redirect(url_for("main.compare"))
    d = periods.today()
    dash = logic.dashboard(g.user["username"], d)
    streak = logic.streak(g.user["username"], d)
    return render_template("progress.html", today=d, dash=dash, streak=streak,
                           badges=logic.badges(g.user["username"], g.user["id"], dash, streak, d),
                           week=logic.points_in(g.user["id"], *periods.range_for("week", d)),
                           history=logic.history_grid(g.user["username"], g.user["id"], 14, d),
                           activity=logic.recent_activity(g.user["id"], 15))


@bp.get("/more")
@login_required
def more():
    return render_template("more.html", vapid_public=push.public_key() if g.user["role"] == "member" else None,
                           reminder_items=get_db().execute(
                               "SELECT r.task_id, t.name, COUNT(*) AS n FROM reminders r JOIN tasks t ON t.id=r.task_id"
                               " WHERE r.user_id=? GROUP BY r.task_id ORDER BY t.name", (g.user["id"],)).fetchall(),
                           today=periods.today(),
                           away_items=get_db().execute("SELECT * FROM away_dates WHERE user_id=? ORDER BY starts_on DESC", (g.user["id"],)).fetchall(),
                           summary_pref=get_db().execute("SELECT * FROM evening_preferences WHERE user_id=?", (g.user["id"],)).fetchone(),
                           sequence_reminder_items=get_db().execute(
                               "SELECT t.id,t.name FROM sequence_subscriptions s JOIN tasks t ON t.id=s.root_task_id"
                               " WHERE s.user_id=? ORDER BY t.name", (g.user["id"],)).fetchall())


@bp.route("/voice", methods=["GET", "POST"])
@login_required
def voice_setup():
    """Siri setup: personal keys for the Shortcut. A new key is shown once, on this response only."""
    if g.user["role"] != "member":
        abort(403)
    new_key = voice.create_token(g.user["id"], request.form.get("label", "")) if request.method == "POST" else None
    return render_template("voice.html", new_key=new_key, keys=voice.tokens_for(g.user["id"]),
                           api_url=url_for("api.voice_command", _external=True))   # not "endpoint": base.html uses that


@bp.post("/voice/keys/<int:token_id>/delete")
@login_required
def voice_key_delete(token_id):
    if not voice.revoke(g.user["id"], token_id):
        abort(404)
    flash("Voice key removed. Any shortcut using it stops working now.", "ok")
    return redirect(url_for("main.voice_setup"))


@bp.post("/reminders/<int:reminder_id>/delete")
@login_required
def reminder_delete(reminder_id):
    task_id = reminders.delete(g.user["id"], reminder_id)
    if task_id is None:
        abort(404)
    flash("Reminder removed.", "ok")
    return redirect(url_for("main.remind", task_id=task_id))


# ------------------------------------------------------------------- push ---

@bp.get("/sw.js")
def service_worker():
    """Served from the root, not /static/, because a worker only controls pages under its own path."""
    resp = current_app.send_static_file("sw.js")
    resp.mimetype = "text/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.post("/push/subscribe")
@login_required
def push_subscribe():
    try:
        push.save_subscription(g.user["id"], request.get_json(silent=True) or {},
                               request.headers.get("User-Agent", ""))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True)


@bp.post("/push/unsubscribe")
@login_required
def push_unsubscribe():
    push.delete_subscription(g.user["id"], (request.get_json(silent=True) or {}).get("endpoint", ""))
    return jsonify(ok=True)


@bp.post("/push/test")
@login_required
def push_test():
    results = push.deliver(push.subscriptions_for(g.user["id"]), {
        "title": "✓ Notifications are on", "body": "This device will get your task reminders.",
        "url": "/", "tag": "test"})
    return jsonify(ok=results["ok"] > 0, sent=results["ok"])


@bp.get("/compare")
@login_required
def compare():
    rng = request.args.get("range", "week")
    if rng not in ("week", "month"):
        rng = "week"
    d = periods.today()
    start, end = periods.range_for(rng, d)
    players = []
    for m in logic.members():
        dash = logic.dashboard(m["username"], d)
        stats = logic.points_in(m["id"], start, end)
        players.append({
            "user": m, "stats": stats, "dash": dash,
            "load": logic.load_hours(m["username"]),
        })
    return render_template("compare.html", players=players, rng=rng, start=start,
                           last=end - timedelta(days=1),
                           activity=logic.recent_activity(None, 20),
                           volunteer_open=players[0]["dash"]["grabs_open"] if players else 0,
                           household={"count": sum(p["stats"]["count"] for p in players),
                                      "points": sum(p["stats"]["points"] for p in players),
                                      "volunteer": sum(p["stats"]["volunteer"] for p in players)},
                           dash=logic.dashboard(g.user["username"], d),
                           nags=reminders.task_ids_for(g.user["id"]))
