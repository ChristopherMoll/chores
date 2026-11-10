"""SQLite access. One file, no ORM: the data model is a handful of small tables."""
import json
import sqlite3
from pathlib import Path

from flask import current_app, g

# Kept separate because the v1 -> v2 migration has to rebuild this table
# (SQLite can't alter a CHECK constraint in place).
TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id           INTEGER PRIMARY KEY,
    code         TEXT    NOT NULL UNIQUE,
    grp          TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    frequency    TEXT    NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly', 'once', 'other')),
    owner        TEXT    NOT NULL DEFAULT 'volunteer',
    hours_month  REAL,
    notes        TEXT    NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    due_date     TEXT,                       -- once: YYYY-MM-DD
    due_weekday  INTEGER,                    -- weekly: 0 = Monday ... 6 = Sunday
    due_monthday INTEGER,                    -- monthly: 1-31, short months use their last day
    due_time     TEXT                        -- daily: HH:MM
);
"""

SCHEMA = TASKS_TABLE.format(name="tasks") + """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('member', 'admin')),
    color         TEXT    NOT NULL DEFAULT '#5b6b7a'
);

-- One row per task per period. period_key is D2026-11-08 / W2026-11-02 / M2026-11,
-- O2026-11-08 for "other" tasks (at most one log per day), or ONCE for one-off tasks.
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

-- One row per person per device.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT    NOT NULL UNIQUE,
    p256dh     TEXT    NOT NULL,
    auth       TEXT    NOT NULL,
    user_agent TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- "Nag me": if the task isn't done `amount` `unit` before it's due, push to this person.
-- at_time is HH:MM when unit is 'days', otherwise ''. created_at is local wall-clock time.
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    unit       TEXT    NOT NULL CHECK (unit IN ('minutes', 'hours', 'days')),
    at_time    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    UNIQUE (user_id, task_id, amount, unit, at_time)
);

-- Which occurrence of each reminder has already been sent, so nobody is nagged twice.
CREATE TABLE IF NOT EXISTS reminder_log (
    reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    occurrence  TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL,
    PRIMARY KEY (reminder_id, occurrence)
);

-- Personal keys for the Siri Shortcut. Only a SHA-256 of each key is kept; deleting a row revokes it.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label        TEXT    NOT NULL,
    token_hash   TEXT    NOT NULL UNIQUE,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);

-- Voice phrases that matched no task, kept 30 days so admins can see what people actually say.
CREATE TABLE IF NOT EXISTS voice_misses (
    id       INTEGER PRIMARY KEY,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phrase   TEXT    NOT NULL,
    heard_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

SCHEMA_VERSION = "2"


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


def _migrate_to_2(conn):
    """Add one-off tasks and due fields. Rebuilds `tasks` to widen its CHECK constraint.

    Foreign keys have to be off first: with them on, DROP TABLE would cascade
    and delete every completion.
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    cols = "id, code, grp, name, description, frequency, owner, hours_month, notes, active, updated_at"
    try:
        conn.executescript(
            "BEGIN;"
            + TASKS_TABLE.format(name="tasks_new")
            + f"INSERT INTO tasks_new({cols}) SELECT {cols} FROM tasks;"
            "DROP TABLE tasks;"
            "ALTER TABLE tasks_new RENAME TO tasks;"
            "UPDATE meta SET value = '2' WHERE key = 'schema_version';"
            "COMMIT;"
        )
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_schema(conn):
    """Create anything missing and bring an older database up to date. Safe to run repeatedly."""
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.commit()
    version = int(conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0])
    if version < 2:
        _migrate_to_2(conn)


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
