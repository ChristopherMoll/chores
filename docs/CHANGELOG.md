# Changelog

All notable changes to this project are recorded here. Versions follow [semantic versioning](https://semver.org): the major number goes up for changes that need a manual step (a data migration or config change), the minor number for new features, and the patch number for fixes.

## [Unreleased]

Nothing yet.

## [1.2.0] - 2026-11-08

Run `sudo ./deploy/install.sh` to update. It backs up the database and applies the schema change itself.

### Added
- **One-off tasks** with an optional due date. They get their own section on the dashboard, and drop off the day after they're crossed off.
- **Due settings:** a due day for weekly tasks, a due day of the month for monthly tasks, and a due-by time for daily tasks. Tasks show "Due Wed", "By 8:00 pm" or an overdue label, and sort soonest first.
- **"Nag me about this":** per-person reminders on any task with a due date, such as "if this isn't done 1 day before it's due, remind me at 9:00 am". A task can have several, and they're skipped once it's crossed off.
- **Push notifications** for the app saved to an iPhone home screen (iOS 16.4+): a service worker, VAPID keys generated next to the database, per-device subscriptions, and an Enable notifications card on your dashboard with a test button. Expired subscriptions are removed automatically.
- `manage.py notify` and a `chores-notify.timer` that runs it every minute. `manage.py migrate` applies schema changes.
- PNG app icons, because iOS ignores SVG touch icons.
- CSV export and import now include the due settings.
- New setting `CHORES_VAPID_SUBJECT`.

### Fixed
- The service failed to start from the example config: systemd treats end-of-line comments as part of the value. The example no longer has any, and `install.sh` strips them from existing env files.
- `install.sh` wasn't executable in git, and now installs `rsync`, which it depends on.
- A POST with no CSRF token passed the check if the session had none either. A non-ASCII token caused a 500.
- A crafted `next=` value with a backslash or control character could redirect off-site after login.
- Changing a task's frequency mid-period could make a completed task show as open.
- Two people crossing off the same task at the same moment gave one of them a 500.
- Pages crashed on Windows because `%-d` date formats don't exist there. Added `tzdata` for Windows too.

## [1.1.0] - 2026-11-08

### Changed
- **Everyone can manage tasks.** The Manage area (deal grid, task editor, new tasks, import and export) is now open to every logged-in user, and its URLs moved from `/admin/…` to `/manage/…`.
- **Admin is now for technical settings only.** `/admin/` is a separate area, linked only for the admin account, holding System info, Users and feed corrections. The whole blueprint is admin-only, enforced on the server for every route, including ones added later.
- The admin account lands on the Admin page after login instead of the Scoreboard.
- Code: `chores/admin.py` was split into `chores/cms.py` (task management) and `chores/admin.py` (admin area). Templates moved from `templates/admin/` to `templates/manage/`.

### Added
- Admin **System** page: app and schema versions, Python, Flask and SQLite versions, database path and size, row counts, and current configuration.
- Repository setup for GitHub and VS Code: a full `.gitignore` (keeps the database, secret key, `.env` files and filled-in spreadsheets out of git), `.gitattributes` (LF for files the Pi executes), `.editorconfig`, `pyproject.toml`, `.vscode/` run and debug configs plus recommended extensions, and a GitHub Actions workflow that runs the tests on Python 3.11 and 3.13.

### Removed
- The `CHORES_MEMBERS_CAN_EDIT` setting. If your `/etc/chores/chores.env` contains it, the line is now ignored and can be deleted.

## [1.0.0] - 2026-11-08

First release. Replaces the printed card deck and the `Household_Tasks.xlsx` spreadsheet.

### Added
- **Accounts:** three fixed users (`chris`, `andrea`, `admin`), with scrypt-hashed passwords set at install, password change, admin password reset, and display names and colours.
- **Tasks:** all 109 cards from the deck seeded with code, group, name and definition. Each task has a frequency (daily / weekly / monthly / other), an owner (Chris / Andrea / Shared / Needs Volunteer), hours per month, notes ("our standard") and an active flag. Wild cards start inactive.
- **Dashboard:** what's left today, this week and this month, with progress bars and one-tap cross-off and undo. Also an "Up for grabs" list of volunteer tasks, a log for as-needed tasks, a 14-day history strip, badges and recent activity.
- **Person view:** read-only view of the other person's full dashboard.
- **Scoreboard:** weekly or monthly points, head-to-head bar, leader crown, daily points chart, current-list progress, assigned load in hours (own + half of shared), badges and the household feed.
- **Game rules:** about 1 point per 5 minutes of estimated effort, a 50% bonus for volunteer tasks, daily-list streaks, and 8 badges. Points are snapshotted when a task is crossed off.
- **CMS (admin):** a deal grid for bulk changes to owner, frequency, hours and active; a task editor; new tasks with auto-suggested codes; delete; CSV export; and import of the `.xlsx` spreadsheet or a CSV, matched by code.
- **CLI:** `manage.py init | set-password | import | backup | serve`.
- **Deployment:** systemd service with hardening, nightly backup timer (keeps 30), Caddyfile for `chores.moll.casa`, an nginx alternative, and a one-shot `install.sh` for Raspberry Pi OS.
- **Security:** CSRF on every POST, per-IP login lockout, secure session cookies behind HTTPS, strict CSP and security headers, and the app bound to localhost behind the reverse proxy.
- **Front end:** mobile first, dark mode, installable to the home screen, works without JavaScript.
- **Docs:** `docs/MANUAL.md` (goals, usage, architecture, design choices, operations) and this changelog.
- **Tests:** pytest suite covering periods, points, auth and CSRF, cross-off permissions, shared tasks, daily reset and streaks, CMS access, bulk dealing, import, and that every page renders.
