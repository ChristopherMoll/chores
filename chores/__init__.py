"""Chores: a small household task tracker for the Moll household."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, db, logic, periods

__version__ = "1.1.0"


def _env_bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _secret_key(instance):
    """SECRET_KEY from the environment, else a key generated once and kept in the instance folder."""
    if os.environ.get("CHORES_SECRET_KEY"):
        return os.environ["CHORES_SECRET_KEY"]
    f = Path(instance) / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip()


def create_app(test_config=None):
    app = Flask(__name__, instance_path=os.environ.get("CHORES_INSTANCE") or None,
                instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE=os.environ.get("CHORES_DATABASE", str(Path(app.instance_path) / "chores.sqlite3")),
        TIMEZONE=os.environ.get("CHORES_TIMEZONE", "America/New_York"),
        WEEK_START=int(os.environ.get("CHORES_WEEK_START", "0")),     # 0 = Monday … 6 = Sunday
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool("CHORES_SECURE_COOKIES", False),
        PERMANENT_SESSION_LIFETIME=timedelta(days=60),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or _secret_key(app.instance_path)

    if _env_bool("CHORES_BEHIND_PROXY", False):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(db.close_db)
    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)

    from . import admin, cms, views
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(cms.bp)
    app.register_blueprint(admin.bp)

    @app.context_processor
    def inject():
        return {
            "csrf_token": auth.csrf_token,
            "owner_label": logic.owner_label,
            "FREQ_LABELS": periods.FREQ_LABELS,
            "version": __version__,
        }

    @app.template_filter("hours")
    def fmt_hours(v):
        if v is None or v == "":
            return "–"
        return f"{v:.1f}".rstrip("0").rstrip(".")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        return resp

    for code in (400, 403, 404, 429):
        app.register_error_handler(code, lambda e, c=code: (render_template("error.html", error=e), c))

    return app
