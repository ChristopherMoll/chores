"""Member-facing pages: dashboard, a person's detail view, scoreboard, reminders and push."""
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from . import logic, periods, push, reminders
from .auth import login_required
from .db import get_db

bp = Blueprint("main", __name__)


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
    expected_period = request.form.get("period")
    if expected_period and expected_period != periods.period_key(task["frequency"]):
        message = "This task's schedule has moved on. Refresh your list before changing it."
        if wants_json:
            return jsonify(ok=False, error=message), 409
        flash(message, "error")
        return redirect(request.referrer or url_for("main.home"))
    try:
        logic.toggle(g.user, task, desired_done=None if state is None else state == "1")
    except PermissionError as e:
        if wants_json:
            return jsonify(ok=False, error=str(e)), 403
        flash(str(e), "error")
        return redirect(request.referrer or url_for("main.home"))
    if wants_json:
        d = periods.today()
        dash = logic.dashboard(g.user["username"], d)
        views = logic.task_views(dash, d)
        selected = request.form.get("view", "today")
        if selected not in views:
            selected = "today"
        completion = get_db().execute(
            "SELECT c.points, c.user_id, u.display_name AS doer FROM completions c JOIN users u ON u.id=c.user_id"
            " WHERE c.task_id=? AND c.period_key=?", (task_id, periods.period_key(task["frequency"], d))).fetchone()
        today = views["today"]["progress"]
        return jsonify(ok=True, done=bool(completion), points=completion["points"] if completion else 0,
                       by=completion["doer"] if completion else g.user["display_name"],
                       can_undo=bool(completion and completion["user_id"] == g.user["id"]),
                       today=today,
                       counts={key: len(view["open"]) for key, view in views.items()},
                       tasks_html=render_template("_task_list.html", current_view=views[selected], selected=selected,
                                                  own=True, nags=reminders.task_ids_for(g.user["id"]),
                                                  task_back=request.form.get("return_to", "/")),
                       volunteer_html=render_template("_volunteers.html", dash=dash,
                                                      nags=reminders.task_ids_for(g.user["id"]),
                                                      task_back=request.form.get("return_to", "/compare"))
                       if request.form.get("surface") == "together" else None,
                       name=task["name"])
    return redirect(request.referrer or url_for("main.home"))


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
    if task["owner"] == logic.VOLUNTEER:
        return redirect(url_for("main.compare", _anchor=f"task-{task_id}"))
    username = g.user["username"] if task["owner"] == logic.SHARED else task["owner"]
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
                               " WHERE r.user_id=? GROUP BY r.task_id ORDER BY t.name", (g.user["id"],)).fetchall())


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
