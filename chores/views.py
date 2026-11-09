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
    streak = logic.streak(who["username"], d)
    own = g.user["id"] == who["id"]
    return render_template(
        "dashboard.html",
        nags=reminders.task_ids_for(g.user["id"]),
        vapid_public=push.public_key() if own else None,
        who=who, own=own, dash=dash, today=d,
        streak=streak,
        badges=logic.badges(who["username"], who["id"], dash, streak, d),
        week=logic.points_in(who["id"], *periods.range_for("week", d)),
        history=logic.history_grid(who["username"], who["id"], 14, d),
        activity=logic.recent_activity(who["id"], 12),
        days_left={f: periods.days_left(f, d) for f in ("weekly", "monthly")},
        members=logic.members(),
    )


@bp.post("/tasks/<int:task_id>/toggle")
@login_required
def toggle(task_id):
    task = _task_or_404(task_id)
    wants_json = request.headers.get("Accept", "").startswith("application/json")
    try:
        done = logic.toggle(g.user, task)
    except PermissionError as e:
        if wants_json:
            return jsonify(ok=False, error=str(e)), 403
        flash(str(e), "error")
        return redirect(request.referrer or url_for("main.home"))
    if wants_json:
        return jsonify(ok=True, done=done, points=logic.points_for(task, task["owner"]),
                       by=g.user["display_name"])
    return redirect(request.referrer or url_for("main.home"))


def _task_or_404(task_id):
    task = get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        abort(404)
    return task


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
                           has_device=bool(push.subscriptions_for(g.user["id"])))


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
    days = (end - start).days
    players = []
    for m in logic.members():
        dash = logic.dashboard(m["username"], d)
        streak = logic.streak(m["username"], d)
        stats = logic.points_in(m["id"], start, end)
        players.append({
            "user": m, "stats": stats, "streak": streak, "dash": dash,
            "load": logic.load_hours(m["username"]),
            "badges": logic.badges(m["username"], m["id"], dash, streak, d),
            "spark": logic.daily_points(m["id"], start, min(days, (d - start).days + 1)),
        })
    top = max((p["stats"]["points"] for p in players), default=0)
    leader = [p for p in players if p["stats"]["points"] == top and top > 0]
    spark_max = max([1] + [v for p in players for v in p["spark"]])
    return render_template("compare.html", players=players, rng=rng, start=start,
                           last=end - timedelta(days=1),
                           top=top, leader=leader[0] if len(leader) == 1 else None,
                           tie=len(leader) > 1, spark_max=spark_max,
                           activity=logic.recent_activity(None, 20),
                           volunteer_open=players[0]["dash"]["grabs_open"] if players else 0)
