"""Web Push: VAPID keys, device subscriptions, and sending.

The keys live next to the database and are generated once. pywebpush and
cryptography are imported only when actually needed, because `manage.py
notify` runs every minute and usually has nothing to send.
"""
import base64
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app

from .db import get_db

TTL_SECONDS = 4 * 60 * 60     # a reminder that's four hours late isn't worth delivering
# Only real push services, so a logged-in user can't point the server at an arbitrary URL.
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")


def _key_paths():
    folder = Path(current_app.config["DATABASE"]).parent
    return folder / "vapid_private.pem", folder / "vapid_public.txt"


def public_key():
    """The browser-facing VAPID key, generating the pair the first time it's needed."""
    private, public = _key_paths()
    if not private.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        public.write_text(base64.urlsafe_b64encode(raw).rstrip(b"=").decode())
    return public.read_text().strip()


def _allowed_endpoint(endpoint):
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def save_subscription(user_id, info, user_agent=""):
    """Store a browser subscription. If this device was someone else's, it moves to this user."""
    keys = info.get("keys") or {}
    endpoint, p256dh, auth = info.get("endpoint") or "", keys.get("p256dh") or "", keys.get("auth") or ""
    if not (isinstance(endpoint, str) and _allowed_endpoint(endpoint) and p256dh and auth):
        raise ValueError("That isn't a valid push subscription.")
    db = get_db()
    db.execute(
        "INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, user_agent) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh,"
        " auth = excluded.auth, user_agent = excluded.user_agent",
        (user_id, endpoint, p256dh, auth, user_agent[:200]))
    db.commit()


def delete_subscription(user_id, endpoint):
    db = get_db()
    db.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?", (user_id, endpoint))
    db.commit()


def subscriptions_for(user_id):
    return get_db().execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall()


def send_one(sub, payload):
    """Send to one device. Returns 'ok', 'gone' (subscription is dead) or 'error' (worth retrying)."""
    from pywebpush import WebPushException, webpush

    private, _ = _key_paths()
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=str(private),
            vapid_claims={"sub": current_app.config["VAPID_SUBJECT"]},   # fresh dict: pywebpush adds to it
            ttl=TTL_SECONDS,
            timeout=15,
        )
        return "ok"
    except WebPushException as e:
        status = e.response.status_code if e.response is not None else None
        if status in (404, 410):
            return "gone"
        print(f"push failed ({status}) for subscription {sub['id']}: {e}", flush=True)
        return "error"
    except OSError as e:     # includes requests' connection errors and timeouts
        print(f"push failed for subscription {sub['id']}: {e}", flush=True)
        return "error"


def deliver(subs, payload):
    """Send to several devices, dropping any the push service says are gone. Returns a Counter."""
    public_key()   # make sure the signing key exists
    results = Counter()
    db = get_db()
    for sub in subs:
        result = send_one(sub, payload)
        results[result] += 1
        if result == "gone":
            db.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
            db.commit()
    return results
