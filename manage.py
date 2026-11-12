#!/usr/bin/env python3
"""Command-line admin for the chores app.

    python manage.py init                 create the database, seed the 109 cards, create users
    python manage.py set-password chris   change a password
    python manage.py import FILE.xlsx     apply Owner / Hours / Notes from the spreadsheet
    python manage.py backup [DIR]         copy the database safely while the app runs
    python manage.py migrate              bring an existing database up to the current schema
    python manage.py notify               send any task reminders that are due (run every minute)
    python manage.py serve                run with waitress (what systemd calls)
"""
import argparse
import getpass
import os
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from chores import create_app, db, push, reminders, operations, __version__
from chores.auth import hash_password

USERS = [  # username, display name, role, colour
    ("chris", "Chris", "member", "#2f6f9f"),
    ("andrea", "Andrea", "member", "#b5485d"),
    ("admin", "Admin", "admin", "#5b6b7a"),
]


def ask_password(username):
    while True:
        pw = getpass.getpass(f"Password for {username} (blank = generate one): ")
        if not pw:
            pw = secrets.token_urlsafe(9)
            print(f"  -> {username}: {pw}")
            return pw
        if len(pw) < 8:
            print("  At least 8 characters, please.")
            continue
        if getpass.getpass("  Again: ") == pw:
            return pw
        print("  Didn't match, try again.")


def cmd_init(app, args):
    with app.app_context():
        conn = db.get_db()
        db.init_schema(conn)
        added = db.seed_tasks(conn)
        print(f"Database: {app.config['DATABASE']}")
        print(f"Seeded {added} new task(s).")
        for username, name, role, color in USERS:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                print(f"User {username} already exists; leaving it alone.")
                continue
            pw = args.password or ask_password(username)
            conn.execute("INSERT INTO users(username, display_name, password_hash, role, color) VALUES (?,?,?,?,?)",
                         (username, name, hash_password(pw), role, color))
        conn.commit()
        push.public_key()
    print("Done.")


def cmd_migrate(app, args):
    with app.app_context():
        conn = db.get_db()
        db.init_schema(conn)
        push.public_key()
        operations.record(migration_at=operations.timestamp(), migration_version=__version__)
        print("Schema version", conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0])


def cmd_notify(app, args):
    with app.app_context():
        operations.record(notify_started_at=operations.timestamp())
        try:
            sent = reminders.run_due()
        except Exception as e:
            operations.record(notify_error=type(e).__name__, notify_finished_at=operations.timestamp())
            raise
        operations.record(notify_finished_at=operations.timestamp(), notify_error="", notify_sent=sent)
    if sent:     # this runs every minute, so stay quiet when there's nothing to report
        print(f"Sent {sent} reminder(s).")


def cmd_set_password(app, args):
    with app.app_context():
        conn = db.get_db()
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
            sys.exit(f"No user called {args.username}.")
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                     (hash_password(ask_password(args.username)), args.username))
        conn.commit()
    print("Password updated.")


def cmd_import(app, args):
    from chores.cms import apply_import, read_upload

    class _File:
        def __init__(self, p):
            self.filename, self._p = p.name, p

        def read(self):
            return self._p.read_bytes()

    with app.app_context():
        updated, problems = apply_import(read_upload(_File(Path(args.file))))
    print(f"Updated {updated} task(s).")
    for p in problems:
        print("  !", p)


def cmd_backup(app, args):
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"chores-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    src = sqlite3.connect(app.config["DATABASE"])
    with sqlite3.connect(dest) as d:
        src.backup(d)          # consistent snapshot even while the app is writing
    src.close()
    with app.app_context():
        operations.record(backup_at=operations.timestamp(), backup_file=str(dest.resolve()))
    keep = sorted(out.glob("chores-*.sqlite3"))[:-args.keep] if args.keep else []
    for old in keep:
        old.unlink()
    print(dest)


def cmd_serve(app, args):
    from waitress import serve
    print(f"Serving on http://{args.host}:{args.port}")
    serve(app, host=args.host, port=args.port, threads=4, ident="chores")


def load_env_file(path):
    """Load KEY=VALUE settings. Windows has no systemd EnvironmentFile to do this for us."""
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", ";")) and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()


def main():
    # Handled first, because the defaults for `serve` below read the environment.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", help="read settings from this KEY=VALUE file")
    common.add_argument("--log-file", help="append output to this file (scheduled tasks have no console)")
    early, _ = common.parse_known_args()
    if early.env_file:
        load_env_file(early.env_file)
        os.environ["CHORES_ENV_FILE"] = str(Path(early.env_file).resolve())
    if early.log_file:
        sys.stdout = sys.stderr = open(early.log_file, "a", encoding="utf-8", buffering=1)

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--password", help="use one password for every new user (testing)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("set-password"); s.add_argument("username"); s.set_defaults(fn=cmd_set_password)
    s = sub.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("backup"); s.add_argument("dir", nargs="?", default="backups")
    s.add_argument("--keep", type=int, default=30, help="how many backups to keep (0 = all)")
    s.set_defaults(fn=cmd_backup)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("notify").set_defaults(fn=cmd_notify)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("CHORES_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("CHORES_PORT", "8087")))
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    args.fn(create_app(), args)


if __name__ == "__main__":
    main()
