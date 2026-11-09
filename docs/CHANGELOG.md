# Changelog

All notable changes to this project are recorded here. Versions follow [semantic versioning](https://semver.org): the major number goes up for changes that need a manual step (a data migration or config change), the minor number for new features, and the patch number for fixes.

## [Unreleased]

Nothing yet.

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
