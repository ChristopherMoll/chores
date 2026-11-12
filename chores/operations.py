"""Small operational breadcrumbs for the Admin page; never credentials or logs."""
import json
from pathlib import Path

from flask import current_app

from .db import get_db
from . import scheduling


def record(**values):
    db = get_db()
    db.executemany("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   [("ops." + key, str(value)) for key, value in values.items()])
    db.commit()


def timestamp():
    return scheduling.stamp(scheduling.now_utc())


def status():
    result = {r["key"][4:]: r["value"] for r in get_db().execute("SELECT key,value FROM meta WHERE key LIKE 'ops.%'")}
    path = Path(current_app.config["DATABASE"]).parent / "update-status.json"
    try:
        if path.is_file() and path.stat().st_size < 65536:
            update = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(update, dict):
                result["update"] = {k: str(update.get(k, ""))[:300] for k in ("checked_at", "status", "deployed")}
    except (OSError, ValueError):
        result["update_error"] = "The updater status file could not be read."
    return result
