#!/usr/bin/env python3
"""Command-line admin for the chores app.

    python manage.py init                 create the database, seed the 109 cards, create users
    python manage.py set-password chris   change a password
    python manage.py import FILE.xlsx     apply Owner / Hours / Notes from the spreadsheet
    python manage.py backup [DIR]         copy the database safely while the app runs
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

from chores import create_app, db
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
    print("Done.")


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
    keep = sorted(out.glob("chores-*.sqlite3"))[:-args.keep] if args.keep else []
    for old in keep:
        old.unlink()
    print(dest)


def cmd_serve(app, args):
    from waitress import serve
    print(f"Serving on http://{args.host}:{args.port}")
    serve(app, host=args.host, port=args.port, threads=4, ident="chores")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--password", help="use one password for every new user (testing)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("set-password"); s.add_argument("username"); s.set_defaults(fn=cmd_set_password)
    s = sub.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("backup"); s.add_argument("dir", nargs="?", default="backups")
    s.add_argument("--keep", type=int, default=30, help="how many backups to keep (0 = all)")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("CHORES_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("CHORES_PORT", "8087")))
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    args.fn(create_app(), args)


if __name__ == "__main__":
    main()
