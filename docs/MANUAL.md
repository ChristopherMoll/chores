# Chores: Manual

A small web app that runs on our Raspberry Pi and keeps track of who does what around the house. It started as a deck of 109 printed task cards, then became a spreadsheet, and this is the third version.

Contents

1. [Goals](#1-goals)
2. [How it works (for Chris and Andrea)](#2-how-it-works-for-chris-and-andrea)
3. [Managing tasks (for everyone) and the admin area](#3-managing-tasks-for-everyone-and-the-admin-area)
4. [Points, streaks and badges](#4-points-streaks-and-badges)
5. [Architecture](#5-architecture)
6. [Design choices](#6-design-choices)
7. [Installing on the Pi](#7-installing-on-the-pi)
8. [Going live at chores.moll.casa](#8-going-live-at-choresmollcasa)
9. [Day-to-day operations](#9-day-to-day-operations)
10. [Configuration reference](#10-configuration-reference)
11. [Developing](#11-developing)
12. [Known limits and ideas](#12-known-limits-and-ideas)

---

## 1. Goals

The card deck had one rule that matters more than the rest: **whoever holds a card holds all of it**. That means noticing the job needs doing, planning it, and doing it, without being reminded. The app exists to support that rule, not to replace it.

What it should do:

- **Make ownership visible.** Every task has exactly one owner: Chris, Andrea, Shared, or Needs Volunteer. Nobody has to wonder whose job something is.
- **Show what's left, not everything.** When you open the app you see what's still open today, this week and this month, and nothing else.
- **Make it satisfying to finish things.** Crossing something off should feel good. A light layer of points, streaks and badges adds that, without turning the household into a contest.
- **Keep it fair, not identical.** The spreadsheet's hours-per-month column carries over. The scoreboard shows each person's assigned load next to what they've actually done, so imbalances show up early.
- **Be transparent.** Either of you can look at the other's dashboard in full detail. There's no private view.
- **Be boring to run.** One Python process, one SQLite file, no build step. It sits quietly next to Plex and SABnzbd and backs itself up every night.

Out of scope on purpose: native mobile apps, multiple households and chat. Reminders are strictly opt-in: nothing nags you unless you asked it to, for that specific task, because reminders handed out by default would work against the "without being reminded" rule.

---

## 2. How it works (for Chris and Andrea)

### Your dashboard

Log in and you land on **My tasks**:

- **Header:** today's date, how many daily tasks you have left, your current streak, this week's points and how many badges you've earned.
- **Today / This week / This month:** one column each, with a progress bar. Open tasks are listed first. Tap the circle to cross one off. Tap the circle again to undo it if you did it by mistake.
- **Tap a task's name** to see the full card: what it covers, plus your agreed standard if one was written in Notes.
- **Up for grabs:** tasks marked *Needs Volunteer*. Either of you can take one and get **50% bonus points** for it.
- **As needed:** tasks with no schedule (taxes, travel planning, birthdays and so on). Log one when you do it. The list shows when each was last done.
- **Last 14 days:** a heat strip showing how much of your daily list you got through each day.
- **Badges** and **recent activity** are at the bottom.

### What "done" means for each kind of task

| Frequency | Resets | Example |
|---|---|---|
| Daily | Every midnight (household time zone) | Dishes |
| Weekly | Monday morning (configurable) | Laundry |
| Monthly | On the 1st | Budget & Bills |
| One-off | Never. Done once. | Renew passport |
| Other / as needed | Never. Each log is a dated entry. | Taxes |

A crossed-off task stays crossed off until its period ends, then it comes back as open. A one-off stays on your list until the end of the day you cross it off, so you can undo a mistake, then drops off. Its history is kept.

### Due days and times

Any task can say when it's due: a date for a one-off, a day of the week for a weekly task, a day of the month for a monthly task (months without that day use their last day), or a time for a daily task. Set these on the task's edit page under **Manage**. Open tasks show a label like *Due Wed* or *By 8:00 pm*, turn red once overdue, and sort soonest first. A due day doesn't change when the task resets: a weekly task due Wednesday can still be crossed off until the week ends.

### Nag me about this

Tap a task's name, then **🔔 Nag me about this**, and choose when you'd like a push notification if it isn't done yet, such as "1 day before at 9:00 am" or "30 minutes before". You can add several to one task. They're yours alone: your partner doesn't get them, and you can set one on any task. A 🔔 marks tasks you have a reminder on.

- A task needs a due date to count back from. Daily tasks are due at their due-by time, or the end of the day if none is set. Weekly and monthly tasks with no due day are due on the last day of their period. As-needed tasks and one-offs with no date can't have reminders.
- If the task is crossed off by then, including by your partner on a Shared task, the reminder is skipped.
- A reminder can't fire before the task's period opens, since you couldn't cross it off yet. "1 day before" on a weekly task due on the first day of the week arrives that first day instead.
- If the Pi was off when a reminder came due, it's still sent if it's less than 2 hours late.

### Turning on notifications on an iPhone

iOS only allows push for web apps saved to the home screen, and needs iOS 16.4 or later.

1. Open `https://chores.moll.casa` in Safari, tap **Share**, then **Add to Home Screen**.
2. Open chores **from the new icon** and log in again. The home-screen app has its own login, separate from Safari.
3. On your dashboard, tap **Enable notifications** and allow them. **Send a test** confirms it works.

Do this on each device you want reminders on. Push needs the real HTTPS site, so it won't work over plain-HTTP LAN testing.

### Shared tasks

A *Shared* task shows up on both dashboards, and whichever of you does it crosses it off for both of you. The ✓ shows who did it. Only the person who crossed it off can undo it, so nobody can accidentally erase the other person's work.

### Looking at each other's progress

Use **How's Andrea doing? →** on your dashboard, or **See every task →** on the Scoreboard. You'll see the other person's full dashboard in read-only form: the same lists, history, badges and activity.

### Scoreboard

Points for this week or this month, with a head-to-head bar, a 👑 for whoever's ahead, a daily points chart, progress on current lists, each person's assigned load in hours, earned badges, and a household feed of every cross-off.

### Changing your password

Tap your initial in the top-right corner, then **Change password**.

---

## 3. Managing tasks (for everyone) and the admin area

**Manage** in the top bar is open to everyone who's logged in. Chris and Andrea re-deal, re-estimate and edit cards from their own accounts. Re-dealing is a household conversation, not an admin job.

The **admin** account is kept separate for technical settings only (see [The admin area](#the-admin-area) below). It can still use Manage, but you shouldn't need it for that.

### The deal grid

All 109 cards (plus any you add) are in one table. For each row you can change:

- **Owner:** Chris, Andrea, Shared, or Needs Volunteer.
- **Frequency:** Daily, Weekly, Monthly, Once, or Other. Changing it clears the old due setting, since it no longer applies.
- **Hours / mo:** your estimate of total monthly effort, including the noticing and planning, not just the doing.
- **On:** untick to retire a task without losing its history. Retired tasks disappear from dashboards.

Change as many rows as you like, then press **Save changes**. The page warns you if you try to leave with unsaved edits. The chips above the grid show each owner's task count and hours, and clicking a chip filters the table to that owner. Search and filter by group, frequency, owner or active state.

### Editing a card

Click a task's name to edit its code, name, group, due setting, "What it covers" text and **Our standard** notes. Write down what you've agreed counts as done there (how often, how well, by when), because it shows on the dashboard when someone opens the task.

**Delete** removes a task *and* its history for good. To keep the history, untick Active instead.

### Adding tasks

**+ New task** suggests the next free code in the group you pick (H28, O26, …). This replaces the blank cards at the end of the deck.

### Importing the spreadsheet

If you filled in `Household_Tasks.xlsx`, go to **Manage → Import spreadsheet** and upload it. Rows are matched by **Code**, and the importer reads Owner, Hours / Month, Notes, and also Frequency, Active, Due Date, Due Weekday, Due Day and Due Time if those columns are present. Blank cells leave existing values alone. The same thing works from the command line: `python manage.py import Household_Tasks.xlsx`.

**Export CSV** downloads the current state of every task. It's useful as a human-readable backup, and you can edit it in a spreadsheet and import it again.

### The admin area

Log in as **admin** and you land on **Admin** (`/admin/`). Neither member account sees it in the navigation, and every route under `/admin/` refuses non-admin users, including any routes added in future. It holds:

- **System:** app and schema version, Python, Flask and SQLite versions, database location and size, row counts, and the current configuration (read-only; settings live in the env file).
- **Users:** change display names and colours, and set a new password for anyone who's locked out. The three accounts are fixed: there's no sign-up page.
- **Feed corrections:** a *remove* link on each household-feed entry on the Scoreboard, for cross-offs made by mistake.

Any future technical or admin-level configuration goes here, so members never have to see it.

### Wild cards

The ten Wild cards (Moving House, New Baby, …) start **turned off**. When one of those events actually happens, switch the card on in the grid, give it an owner, and expect to re-deal some other cards for a while.

### Starting frequencies

The cards didn't specify frequencies, so the seed data uses a best guess: 17 daily, 27 weekly, 21 monthly, and 44 other. All tasks start as *Needs Volunteer* except the four "each of you" cards (Self-Care, Your Own Clothes & Grooming, Your Own Friendships, Small Gestures for Your Partner), which start as *Shared*. Expect to change a lot of these in your first dealing session.

---

## 4. Points, streaks and badges

The game layer is deliberately light. It's there to make finishing things feel good, not to settle arguments.

**Points per cross-off.** Roughly one point per five minutes of estimated effort:

```
minutes per occurrence = hours_month × 60 ÷ occurrences per month
                         (daily 30.44, weekly 4.35, monthly 1, once 1, other 1)
points = max(1, round(minutes ÷ 5))
```

So a daily task estimated at 15 h/month is about 30 minutes a day, which earns 6 points. Tasks without an hours estimate get a flat 2 (daily), 5 (weekly), 10 (monthly) or 8 (once or other). A **Needs Volunteer** task earns 1.5× (rounded up).

Points are **saved at the moment you cross something off**. If you later change the hours or re-deal a task, past scores don't move.

**Streak.** The number of consecutive days on which *every* daily task you hold (solo or shared) got done. Today's unfinished list doesn't break your streak; only a finished day adds to it. If you hold no daily tasks your streak stays at 0.

**Badges** (recalculated live):

| Badge | How |
|---|---|
| 🧹 Clean sweep | Every daily task done today |
| 📅 Week, handled | Every weekly task done this week |
| 🗓️ Month in the bag | Every monthly task done this month |
| 🔥 On a roll / ⚡ Unstoppable / 🏆 Legend | 3 / 7 / 30-day streak |
| 🙋 Volunteer | Picked up a Needs Volunteer task this week |
| 🦸 Household hero | 5+ volunteer pickups this month |

**Assigned load** on the scoreboard is each person's own hours plus half of all Shared hours, not counting one-offs. This is the spreadsheet's "fair, not identical" check, kept in view.

---

## 5. Architecture

```
 phone / laptop
      │  HTTPS  chores.moll.casa
      ▼
 router (ports 80/443 forwarded) ──► Raspberry Pi
                                       │
                                       ├─ Caddy  :443   TLS (Let's Encrypt), reverse proxy
                                       │     │
                                       │     ▼  http://127.0.0.1:8087
                                       ├─ waitress ─ Flask app "chores"  (systemd: chores.service)
                                       │                    │
                                       │                    ▼
                                       ├─ /var/lib/chores/chores.sqlite3   (+ nightly backups)
                                       │                    ▲
                                       │                    │
                                       └─ manage.py notify  (chores-notify.timer, every minute)
                                                  │  encrypted, signed with the VAPID key
                                                  ▼
                                            Apple push service ──► iPhone service worker (sw.js)
```

### Code layout

```
chores/
├── manage.py              CLI: init, migrate, set-password, import, backup, notify, serve
├── chores/
│   ├── __init__.py        app factory, config from env, security headers
│   ├── db.py              schema, migrations, connection, seeding
│   ├── periods.py         time zone, period keys, week/month ranges, due dates
│   ├── logic.py           ownership, toggling, points, streaks, badges (no request objects)
│   ├── reminders.py       "nag me": when each reminder fires, sending the ones that are due
│   ├── push.py            VAPID keys, device subscriptions, sending through pywebpush
│   ├── auth.py            login, sessions, CSRF, lockout, password change
│   ├── views.py           dashboard, person view, scoreboard, toggle, reminders, push endpoints
│   ├── cms.py             Manage: deal grid, task editor, import/export (all logged-in users)
│   ├── admin.py           Admin: system info, users, feed corrections (admin role only)
│   ├── seed/tasks.json    the 109 cards
│   ├── templates/         Jinja templates (server-rendered)
│   └── static/            style.css, app.js, sw.js (service worker), icons, web manifest
├── deploy/                systemd units, Caddyfile, nginx alternative, install.sh, env example
├── docs/                  this manual + CHANGELOG
└── tests/                 pytest suite
```

### Data model

Seven tables (see `chores/db.py`):

- **users**: `username`, `display_name`, `password_hash` (scrypt), `role` (`member` or `admin`), `color`.
- **tasks**: `code` (H01…), `grp`, `name`, `description`, `frequency`, `owner` (a member's username, `shared` or `volunteer`), `hours_month`, `notes`, `active`, and one of `due_date`, `due_weekday`, `due_monthday` or `due_time` depending on the frequency.
- **push_subscriptions**: one row per person per device: the push service `endpoint` and the device's encryption keys.
- **reminders**: one person's "nag me" on one task: `amount`, `unit` (`minutes`, `hours` or `days`), and `at_time` for reminders counted in days.
- **reminder_log**: which occurrence of each reminder has already been sent.
- **completions**: one row per task per period. `period_key` encodes the period (`D2026-11-08`, `W2026-11-02`, `M2026-11`, `ONCE` for one-offs, or `O2026-11-08` for as-needed tasks). A `UNIQUE(task_id, period_key)` constraint means a task can't be crossed off twice in the same period, even by both of you tapping at once. Each row also records `user_id`, `done_on` (local date), `done_at` (UTC), and snapshots of `owner_at` and `points`.
- **meta**: schema version, for future migrations.

"Is this task done?" is just: *does a completion exist for this task with the current period's key?* Nothing is ever reset. A new period simply has no rows yet. History is kept for free.

### Request flow for crossing something off

1. You tap the circle. `app.js` POSTs to `/tasks/<id>/toggle` with the CSRF token in a header and `Accept: application/json`.
2. `logic.toggle` checks permissions, then inserts or deletes the completion for the current period.
3. The JSON reply carries the new state and points. The page updates the row, the counter, the progress bar and the toast without reloading.
4. Without JavaScript, the same button is an ordinary form post that redirects back.

### How a reminder reaches a phone

1. `chores-notify.timer` runs `manage.py notify` every minute. `reminders.run_due` works out when each reminder fires for the task's current occurrence.
2. If that moment has passed within the last 2 hours and the task still isn't done, it records the reminder in `reminder_log` so it can't go out twice, then hands it to `push.deliver`.
3. pywebpush encrypts the message to each of that person's devices and signs the request with the VAPID key. Apple delivers it to the phone, where `sw.js` shows the notification. Tapping it opens the dashboard at that task.
4. A device Apple reports as gone (404 or 410) is deleted. If no device could be reached because of an error, the log entry is removed so the next run retries.

---

## 6. Design choices

**Flask + SQLite + server-rendered HTML.** Two people, a few hundred page views a week, and a Pi that also runs Plex. A framework with an SPA front end, a database server, or a container stack would all be more to maintain than the app is worth. SQLite in WAL mode handles this load easily and makes backup a single file copy. There's no build step, so updating means copying files and restarting.

**No ORM.** The schema is four tables and the queries are short. Plain SQL in `logic.py` is easy to read and has no extra dependency.

**Periods as keys, not resets.** There's no cron job that "un-ticks" tasks at midnight. Done-ness is derived from the current date, so the app is correct even if the Pi was off at midnight, and full history accumulates with no extra work.

**Snapshotting points and owner.** Re-dealing and re-estimating are expected (the cards say to check in weekly and re-deal). Snapshots keep last month's scoreboard honest after this month's changes.

**One owner value per task.** It's a direct translation of the card rule. *Shared* covers both the "hold it together" and "each of you holds it for yourself" cards, with the trade-off noted in [section 12](#12-known-limits-and-ideas).

**Everyone manages tasks; admin is for technical settings.** Dealing and re-dealing cards is the core household activity, so both members can do it directly. The admin role is a separate access level for technical and account settings that members shouldn't need to see. That boundary is enforced on the server: the whole `/admin/` blueprint checks the role before any route runs, so nothing in it can be reached by a member account, whether through the UI or a direct request. Keep new technical settings, and any future machine-facing endpoints that expose them, inside `admin.py`. The admin account doesn't cross off tasks and doesn't appear on the scoreboard.

**Undo is limited to whoever did it.** Prevents silent erasure of the other person's work. Admin can remove any entry from the household feed if something needs correcting.

**Gamification kept light.** Points scale with real effort, so many tiny tasks can't beat one big one. Volunteering is rewarded, because getting unowned work picked up is the most useful thing the game can encourage. There are no penalties, no lost points and no public shaming of an unfinished day.

**A timer for reminders, not a thread in the web app.** A systemd oneshot works the same way as the nightly backup, restarts cleanly, and can't take the website down. Heavy imports are deferred, so a run with nothing to send is cheap.

**Reminders can't fire before a period opens.** Crossing a task off only applies to its current period, so a nag about next week's occurrence couldn't be acted on, and might arrive right after you'd finished this week's.

**Security for an internet-facing Pi.**
- The app binds to `127.0.0.1`. Only Caddy is reachable from outside, and only over HTTPS.
- Passwords are hashed with scrypt (Werkzeug default). There are no default passwords: `init` asks for them, or generates random ones.
- CSRF tokens on every POST. Session cookies are HttpOnly, SameSite=Lax, and Secure in production. The session is cleared at login.
- Each IP is locked out for 15 minutes after 8 failed logins.
- Strict security headers: CSP with no inline script or style, `X-Frame-Options: DENY`, nosniff, same-origin referrer. This is also why colours and bar widths are set from `data-` attributes by `app.js` rather than inline styles.
- The systemd unit runs as an unprivileged `chores` user with a read-only filesystem except `/var/lib/chores`.
- Push subscriptions are only accepted for known push services, so a logged-in user can't make the server call an arbitrary URL. The VAPID private key is readable only by the `chores` user.

**Mobile first.** It'll mostly be used on phones, standing in the kitchen. The layout is single-column on small screens with large tap targets, and it can be added to the home screen (web manifest and icon included). Dark mode follows the phone's setting.

**Progressive enhancement.** Every action is a real form, and JavaScript only makes it smoother. If `app.js` fails to load, everything still works.

---

## 7. Installing on the Pi

Requirements: Raspberry Pi OS (Bookworm or later) with Python 3.11+, already standard. The app uses about 40 MB of RAM.

```bash
# copy the project to the Pi, e.g.
scp chores.zip pi@raspberrypi.local:~
ssh pi@raspberrypi.local
unzip chores.zip && cd chores
sudo ./deploy/install.sh
```

The installer:

1. creates a system user `chores`,
2. copies the code to `/opt/chores` and builds a virtualenv there,
3. puts config in `/etc/chores/chores.env` (on first install only),
4. on first install, creates `/var/lib/chores/chores.sqlite3`, seeds the 109 cards, and **asks for passwords** for chris, andrea and admin (press Enter to generate one; it's printed once),
5. on an update, backs up the database and applies any schema changes,
6. enables `chores.service`, the nightly `chores-backup.timer` and the every-minute `chores-notify.timer`.

Check that it's running:

```bash
systemctl status chores
curl -I http://127.0.0.1:8087/login      # expect 200
```

**Trying it on the LAN before going live:** edit `/etc/chores/chores.env` and set `CHORES_HOST=0.0.0.0` and `CHORES_SECURE_COOKIES=0`, then `sudo systemctl restart chores` and browse to `http://<pi-ip>:8087`. Put both settings back before exposing it to the internet.

**Updating** to a new version: unpack the new code and run `sudo ./deploy/install.sh` again. It replaces the code, backs up the database, applies any schema changes, and leaves your settings in the env file alone. Read the [CHANGELOG](CHANGELOG.md) first.

---

## 8. Going live at chores.moll.casa

1. **DNS.** Add a record for `chores.moll.casa` pointing at your home IP. The simplest option is a `CNAME` to `moll.casa`, so it follows your dynamic DNS automatically. If your DDNS provider supports it, a wildcard `*.moll.casa` also works.
2. **Ports.** Forward TCP **80** and **443** on the router to the Pi. Port 80 is needed so Let's Encrypt can issue and renew the certificate. If 80/443 already go to another reverse proxy, add chores to that proxy instead (see the nginx example).
3. **Caddy** (recommended, because it handles certificates itself):
   ```bash
   sudo apt install caddy
   sudo cp /opt/chores/deploy/Caddyfile /etc/caddy/Caddyfile   # or append the block to your existing one
   sudo systemctl reload caddy
   ```
   Already running nginx? Use `deploy/nginx.conf` with `certbot --nginx -d chores.moll.casa`.
4. Make sure `/etc/chores/chores.env` has `CHORES_HOST=127.0.0.1`, `CHORES_BEHIND_PROXY=1` and `CHORES_SECURE_COOKIES=1`. Then restart with `sudo systemctl restart chores`.
5. Open `https://chores.moll.casa` on your phone, log in, and use *Add to Home Screen*.

---

## 9. Day-to-day operations

| Task | Command |
|---|---|
| Logs | `journalctl -u chores -f` |
| Restart | `sudo systemctl restart chores` |
| Reset a password | `cd /opt/chores && sudo -u chores bash -c 'set -a; . /etc/chores/chores.env; .venv/bin/python manage.py set-password chris'` (or use admin → Users) |
| Backup now | same pattern with `manage.py backup /var/lib/chores/backups` |
| Reminder logs | `journalctl -u chores-notify -n 50` (silent unless something was sent or failed) |
| List backups | `ls /var/lib/chores/backups` (the last 30 nightly copies are kept) |
| Restore | `sudo systemctl stop chores && sudo -u chores cp /var/lib/chores/backups/chores-YYYYMMDD-HHMMSS.sqlite3 /var/lib/chores/chores.sqlite3 && sudo systemctl start chores` |

Backups use SQLite's online-backup API, so they're consistent even while the app is in use. For off-Pi safety, have something you already run (rsync to a NAS, etc.) copy `/var/lib/chores/backups` somewhere else.

The push keys, `vapid_private.pem` and `vapid_public.txt`, sit next to the database and aren't in those backups. If they're lost, the app makes a new pair and every device has to tap **Enable notifications** again.

---

## 10. Configuration reference

All settings are environment variables, set in `/etc/chores/chores.env`. Keep comments on their own lines, because systemd treats text after a value as part of the value.

| Variable | Default | Meaning |
|---|---|---|
| `CHORES_INSTANCE` | `./instance` | Folder for the database and secret key |
| `CHORES_DATABASE` | `$CHORES_INSTANCE/chores.sqlite3` | Override the database path |
| `CHORES_TIMEZONE` | `America/New_York` | When "today" starts and ends |
| `CHORES_WEEK_START` | `0` | First day of the week: 0 = Monday … 6 = Sunday |
| `CHORES_HOST` / `CHORES_PORT` | `127.0.0.1` / `8087` | Where waitress listens |
| `CHORES_BEHIND_PROXY` | off | Trust `X-Forwarded-*` from Caddy/nginx (needed for correct client IPs in the login lockout) |
| `CHORES_SECURE_COOKIES` | off | Send the session cookie only over HTTPS. **Turn on in production.** |
| `CHORES_SECRET_KEY` | generated | Signs session cookies. Generated once into the instance folder if not set. Changing it logs everyone out. |
| `CHORES_VAPID_SUBJECT` | `https://chores.moll.casa` | Identifies this server to push services. Must be an `https://` URL or a `mailto:` address. |

---

## 11. Developing

The project is set up as a normal git repository, ready for GitHub and VS Code.

```bash
git clone https://github.com/ChristopherMoll/chores.git && cd chores
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py init            # creates ./instance/chores.sqlite3 (git-ignored)
python manage.py serve           # http://127.0.0.1:8087
pytest
```

After pulling a version with schema changes, run `python manage.py migrate`.

**In VS Code:** open the folder and accept the recommended extensions. Pick the `.venv` interpreter, which `.vscode/settings.json` already points at. Then:
- **Run and Debug → "Chores: Flask (debug, auto-reload)"** gives breakpoints in Python and Jinja templates, and reloads on save.
- **"Chores: init local database"** creates the local database the first time.
- **Testing** in the sidebar picks up the pytest suite automatically.

**Repository conventions**
- `.gitignore` keeps live data and secrets out of git: `instance/`, `backups/`, any `*.sqlite3`, `secret_key`, `.env` files, and filled-in `*.xlsx` spreadsheets. It also ignores Python, VS Code, Visual Studio, JetBrains and OS clutter, while keeping the shared `.vscode` config.
- `.gitattributes` forces LF line endings on the files the Pi executes (`*.sh`, systemd units, Caddyfile), so editing on Windows can't break `install.sh`.
- `.editorconfig` sets indentation and line endings for any editor.
- **GitHub Actions** (`.github/workflows/tests.yml`) runs the tests on every push to `main` and on every pull request, on Python 3.11 (what Raspberry Pi OS Bookworm ships) and 3.13.
- Versions follow semver. Bump `__version__` in `chores/__init__.py` and `pyproject.toml`, add a CHANGELOG entry, and tag the release (`git tag v1.2.0`).

**Code conventions**
- Logic that doesn't need a request goes in `logic.py` / `periods.py`, which keeps it testable.
- Tests use `TODAY_OVERRIDE` in the app config to pin "today".
- Keep the CSP intact: no inline `<script>` or `style=""`. Use a class, or a `data-` attribute that `app.js` applies.
- New technical or admin-level settings go in `admin.py`, never in `cms.py` or `views.py`.
- Schema changes: bump `SCHEMA_VERSION` in `db.py`, add a migration step to `init_schema`, and note it in the CHANGELOG. `install.sh` runs `manage.py migrate` on every update.

**Deploying from git** (optional, instead of copying a zip): clone the repo on the Pi, then `git pull && sudo ./deploy/install.sh` to update. The installer copies the code into `/opt/chores` and never touches the database.

---

## 12. Known limits and ideas

- **"Each of you" cards are one shared tick.** Self-Care and similar cards are meant to be held by each person for themselves, but as *Shared* tasks one tick covers both. If that matters, add a personal copy for each person (for example C22-C for Chris and C22-A for Andrea) and delete the shared one.
- **Streaks use today's assignments.** If a daily task is re-dealt, past days are judged against the new assignment.
- **The login lockout is in memory** and resets when the service restarts. That's fine for a two-person household.
- **Reminders are per task and opt-in** (see Goals). There's no "you have 3 left at 8 pm" summary. It would be a small addition to `reminders.py`.
- **Each reminder fires once per occurrence.** It doesn't repeat until the task is done. Add several reminders if you want more than one nudge.
- **Finished one-offs stay in the Manage grid** so their history is kept. Delete or turn them off to tidy up.
- **Logging out doesn't turn off notifications on that device.** Use **Turn off** first. If someone else logs in and enables them, the device moves to that person.
- Ideas: an end-of-week check-in page that walks through the cards (step 6 in the deck), a "pass this card" handoff with notice, and printing the current deal back onto cards.
