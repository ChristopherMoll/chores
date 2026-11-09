"""SQLite access. One file, no ORM: the data model is four small tables."""
import json
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('member', 'admin')),
    color         TEXT    NOT NULL DEFAULT '#5b6b7a'
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,
    grp         TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    frequency   TEXT    NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly', 'other')),
    owner       TEXT    NOT NULL DEFAULT 'volunteer',
    hours_month REAL,
    notes       TEXT    NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per task per period. period_key is D2026-11-08 / W2026-11-02 / M2026-11,
-- or O2026-11-08 for "other" tasks (at most one log per day).
-- points and owner_at are snapshots taken at completion time so that later
-- re-dealing or re-estimating never rewrites history.
CREATE TABLE IF NOT EXISTS completions (
    id         INTEGER PRIMARY KEY,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_key TEXT    NOT NULL,
    done_on    TEXT    NOT NULL,           -- local date, YYYY-MM-DD
    done_at    TEXT    NOT NULL,           -- UTC timestamp
    owner_at   TEXT    NOT NULL,
    points     INTEGER NOT NULL,
    UNIQUE (task_id, period_key)
);
CREATE INDEX IF NOT EXISTS ix_completions_user_day ON completions(user_id, done_on);
CREATE INDEX IF NOT EXISTS ix_completions_period ON completions(period_key);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

SCHEMA_VERSION = "1"


def connect(path):
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # friendlier to SD cards + concurrent reads
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.commit()


def seed_tasks(conn):
    """Load the 109 cards from the bundled seed file. Existing codes are left alone."""
    seed = json.loads((Path(__file__).parent / "seed" / "tasks.json").read_text())
    added = 0
    for t in seed:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tasks(code, grp, name, description, frequency, owner, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t["code"], t["group"], t["name"], t["description"], t["frequency"], t["owner"], int(t["active"])),
        )
        added += cur.rowcount
    conn.commit()
    return added
