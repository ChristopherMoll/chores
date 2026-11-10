"""Login, sessions, CSRF and a small brute-force guard.

The app will face the internet at chores.moll.casa, so: hashed passwords,
CSRF tokens on every POST, and a per-IP lockout after repeated failures.
"""
import hmac
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)

MAX_FAILURES, WINDOW_SECONDS = 8, 15 * 60
_failures = defaultdict(deque)   # ip -> timestamps of recent failures (in-memory; resets on restart)


def hash_password(pw):
    return generate_password_hash(pw)   # scrypt by default in Werkzeug 3


def _locked_out(ip):
    q = _failures[ip]
    cutoff = time.time() - WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= MAX_FAILURES


def load_user():
    uid = session.get("uid")
    g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone() if uid else None


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if request.method != "POST":
        return
    if request.blueprint == "api":   # authenticated by header key, never by cookie (see api.py)
        return
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        abort(400, "Your session expired. Go back, refresh the page, and try again.")


def safe_next(target):
    """Only follow same-site paths. Browsers treat backslashes as slashes and strip control characters."""
    ok = (target.startswith("/") and not target.startswith("//")
          and "\\" not in target and not any(ord(c) < 32 for c in target))
    return target if ok else url_for("main.home")


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        if g.user["role"] != "admin":
            abort(403)
        return view(*a, **kw)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _locked_out(ip):
            flash("Too many failed attempts. Try again in 15 minutes.", "error")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            _failures.pop(ip, None)
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            return redirect(safe_next(request.args.get("next", "")))
        _failures[ip].append(time.time())
        flash("That username and password don't match.", "error")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        cur, new, confirm = (request.form.get(k, "") for k in ("current", "new", "confirm"))
        if not check_password_hash(g.user["password_hash"], cur):
            flash("Current password is wrong.", "error")
        elif len(new) < 8:
            flash("New password needs at least 8 characters.", "error")
        elif new != confirm:
            flash("The new passwords don't match.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), g.user["id"]))
            db.commit()
            flash("Password changed.", "ok")
            return redirect(url_for("main.home"))
    return render_template("account.html")
