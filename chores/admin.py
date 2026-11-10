"""Admin area: technical and account-level settings only.

Everything here requires the admin role and is deliberately kept out of the
member navigation. Day-to-day task management is in cms.py and is open to
every logged-in user. New technical configuration belongs here.
"""
import os
import sqlite3
import sys

from importlib.metadata import version as pkg_version

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from . import __version__, voice
from .auth import admin_required, hash_password
from .db import get_db

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@admin_required
def _guard():
    """Every route in this blueprint is admin-only, including ones added later."""
    return None


@bp.get("/")
def system():
    db = get_db()
    path = current_app.config["DATABASE"]
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("users", "tasks", "completions", "reminders", "push_subscriptions")}
    schema = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    cfg = current_app.config
    settings = [
        ("Time zone", cfg["TIMEZONE"], "CHORES_TIMEZONE"),
        ("Week starts", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][cfg["WEEK_START"]],
         "CHORES_WEEK_START"),
        ("Secure cookies", "on" if cfg["SESSION_COOKIE_SECURE"] else "off", "CHORES_SECURE_COOKIES"),
        ("Behind proxy", "on" if os.environ.get("CHORES_BEHIND_PROXY", "").lower() in ("1", "true", "yes", "on") else "off",
         "CHORES_BEHIND_PROXY"),
        ("Instance folder", current_app.instance_path, "CHORES_INSTANCE"),
        ("Push subject", cfg["VAPID_SUBJECT"], "CHORES_VAPID_SUBJECT"),
    ]
    info = {
        "version": __version__,
        "schema": schema["value"] if schema else "?",
        "python": sys.version.split()[0],
        "flask": pkg_version("flask"),
        "sqlite": sqlite3.sqlite_version,
        "db_path": path,
        "db_size": os.path.getsize(path) if os.path.exists(path) else 0,
    }
    return render_template("admin/system.html", info=info, counts=counts, settings=settings,
                           misses=voice.recent_misses())


@bp.route("/users", methods=["GET", "POST"])
def users():
    db = get_db()
    if request.method == "POST":
        uid = request.form.get("id", type=int)
        user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not user:
            abort(404)
        name = request.form.get("display_name", "").strip()
        color = request.form.get("color", "").strip()
        pw = request.form.get("password", "")
        if name:
            db.execute("UPDATE users SET display_name = ? WHERE id = ?", (name[:40], uid))
        if color.startswith("#") and len(color) == 7:
            db.execute("UPDATE users SET color = ? WHERE id = ?", (color, uid))
        if pw:
            if len(pw) < 8:
                flash("Passwords need at least 8 characters.", "error")
                return redirect(url_for("admin.users"))
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(pw), uid))
        db.commit()
        flash(f"Saved {user['username']}.", "ok")
        return redirect(url_for("admin.users"))
    rows = db.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM completions c WHERE c.user_id = u.id) AS n FROM users u ORDER BY u.id"
    ).fetchall()
    return render_template("admin/users.html", rows=rows)


@bp.post("/completions/<int:cid>/delete")
def completion_delete(cid):
    """Correct the record: remove a cross-off from the household feed."""
    db = get_db()
    db.execute("DELETE FROM completions WHERE id = ?", (cid,))
    db.commit()
    flash("Removed that entry.", "ok")
    return redirect(request.referrer or url_for("main.compare"))
