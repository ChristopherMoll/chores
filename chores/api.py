"""JSON API for the Siri Shortcut.

Authenticated only by a personal key in the Authorization header. The session cookie is
ignored here, which is why these routes can skip the CSRF check: a browser can't attach
the key to a forged request.
"""
from flask import Blueprint, g, jsonify, request

from . import voice

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.before_request
def _authenticate():
    g.user = None
    scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
    user = voice.user_for_token(raw.strip()) if scheme.lower() == "bearer" else None
    if user is None:
        return jsonify(ok=False, action="unauthorized",
                       speech="This shortcut isn't connected. Create a new voice key in MoleChores, under More."), 401
    g.user = user
    return None


@bp.post("/voice")
def voice_command():
    data = request.get_json(silent=True)
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str):
        return jsonify(ok=False, action="bad_request", speech="The shortcut didn't send what you said."), 400
    return jsonify(voice.handle(g.user, text))
