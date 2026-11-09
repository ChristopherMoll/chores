"""Member-facing pages: dashboard, a person's detail view, and the scoreboard."""
from datetime import timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import logic, periods
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
    return render_template(
        "dashboard.html",
        who=who, own=(g.user["id"] == who["id"]), dash=dash, today=d,
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
    task = get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        abort(404)
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
