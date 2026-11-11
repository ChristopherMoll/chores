# Changelog

All notable changes to this project are recorded here. Versions follow [semantic versioning](https://semver.org): the major number goes up for changes that need a manual step (a data migration or config change), the minor number for new features, and the patch number for fixes.

## [1.8.0] - 2026-11-10

### Added
- **Visible Undo completion:** each completed task now has a labeled button beneath its name, without opening its details. The Completed disclosure says Review & undo. Several displayed completions can be corrected independently, without Siri's one-hour limit. Shared tasks retain the actual-doer rule, and undoing a sequence still protects later completed steps. Early sequence completions can be undone while their original ready time is still in the future.
- **Animated vector mascots:** inline SVGs replace the static illustrations, with occasional blinks, gentle breathing, completion nods, a celebration bounce, and moving question-mark, lightbulb, trophy and confetti details. Original SVG exports are retained; a small Python preparation tool maps their parts and creates presentation-only artwork without conflicting styles or IDs.
- **Responsive feedback:** native touch controls gain press feedback that cancels during scrolling; checkmarks animate only after a confirmed save. List movement and progress changes animate while retaining scroll position and disclosures. Ready sequence steps receive a brief highlight. Desktop editors, task details, navigation selection and save confirmations have short transitions.
- **Phone comfort:** smaller feedback controls now have 44px touch targets, hover styling is limited to devices with a precise hovering pointer, and form/notification positioning accounts for the visual viewport when a phone keyboard is open. Day selectors adapt to enlarged text and narrow editor panels. Pinch zoom and native scrolling remain available.

### Changed
- Quiet celebrations and the device's Reduce Motion preference disable both CSS and JavaScript motion. Mascot animation stops offscreen and pauses when the app is hidden. Native forms and static illustrations continue to work without JavaScript. No animation or touch library is required.

## [1.7.0] - 2026-11-10

No manual migration: the normal update upgrades SQLite to schema 3, preserving existing tasks, completions, reminders and Siri keys.

### Added
- **Selected days:** choose any combination of Monday through Sunday, including weekends, with an optional common due time. Every scheduled date has its own completion, points and reminder. Missed dates remain available; skip an individual occurrence without changing the schedule. Schedule edits preserve completed work and existing sequence runs.
- **Timed sequences:** add ordered follow-ups in the task editor, creating new steps or reusing tasks set to Sequence step. Each delay starts from the previous step's actual completion. Runs survive restarts, can overlap, and retain their original task details, ownership, effort and delays when templates change.
- **One step at a time:** ready work appears in Today; waiting and blocked steps appear under Coming up without counting against today's progress. A run page groups every step and offers Done early, a per-run ready-time adjustment, cancellation of unfinished work, and Start another run. Undo retracts waiting follow-ups and protects later completed work.
- **Personal ready reminders:** opt in per sequence; the existing minute notification job sends the next-step nudge and suppresses completed/cancelled steps and duplicate sends. Preferences can be removed in More.
- CSV export/import includes selected days and ordered sequence references. Siri can complete ready occurrences and activate their follow-ups; it asks the user to choose in the app when more than one date or load could match.

## [1.6.1] - 2026-11-10

### Fixed
- Siri kept asking "Wash Laundry or Fold the Laundry?" when dictation misheard one word, such as "watch laundry" for "wash laundry". A misheard word scored nothing, so the two tasks tied on "laundry". Near misses now get partial credit, enough to tell similar tasks apart but never enough to pick a task on their own. When every word of a longer name is heard, that task beats a shorter one ("wash laundry" means Wash Laundry, not Laundry). Past tenses like "mopped" and "folded" are understood too.
- When Siri does need to ask, it now says what it heard first ("I heard 'watch laundry'. Did you mean…"), and the Admin page lists those phrases alongside the ones that matched nothing.

## [1.6.0] - 2026-11-09

### Added
- **"Hey Siri, what chores do I have left today?"** Siri now reads the whole Today list in groups: remaining daily chores, then chores due today, then overdue ones. Long lists stop after 10 names and say how many more are in the app. The More → Siri page explains how to add this shortcut.

### Changed
- Voice understands more ways of asking what's left, such as "what chores do I have left today".

### Fixed
- The More → Siri page showed `main.voice_setup` instead of the address to put in the shortcut. It now shows `https://chores.moll.casa/api/v1/voice`, and the More tab stays highlighted on that page.

## [1.5.0] - 2026-11-09

No manual step: the two new tables are created by the normal update.

### Added
- **Siri:** "Hey Siri, I did a chore" crosses off a task by voice through an Apple Shortcut. More → Siri creates and removes personal voice keys and explains how to build the shortcut. Voice can also say what's left today, your weekly score, and undo your last cross-off from the past hour.
- Naming your partner's task by voice crosses it off for them, gives you the points, and sends them a "Task stolen!" notification. Undoing it sends a "Never mind". Voice never claims a Needs Volunteer task, and asks instead of guessing when two tasks sound alike.
- The Admin page lists voice phrases that matched no task over the last 30 days.

## [1.4.2] - 2026-11-09

### Fixed
- Installing on Windows ARM64 no longer tries to compile `cryptography` from source, which failed without OpenSSL and a build toolchain. The installer and CI now accept only a prebuilt `cryptography`, so on ARM64 pip selects `cryptography` 46.0.3 and `pywebpush` 2.3.0. Other platforms still get the newest releases.

## [1.4.1] - 2026-11-09

### Fixed
- Windows updates repair permissions before stopping the app. The previous recursive ACL command applied directory-only inheritance flags to files, leaving existing configuration, database, or log files unreadable even from Administrator PowerShell. The installer now grants effective file access first, then adds directory inheritance without replacing those grants. Existing inaccessible files are recovered when necessary; SYSTEM and Administrators retain full access, and LOCAL SERVICE retains Modify access.
- A standalone `deploy/windows/repair-update.ps1` repairs an older installer already extracted to TEMP and retries the update without requiring manual permission edits.

## [1.4.0] - 2026-11-09

No database migration or new service is required. Existing task ownership, recurrence, points, streaks, and badge rules are unchanged.

### Added
- A focused iPhone layout with bottom navigation, and a persistent desktop sidebar.
- Today brings daily routines and current weekly, monthly, and one-off deadlines together. Week, month, one-off, and as-needed lists remain available; completed tasks stay accessible in a collapsible section.
- Together shows household contributions, assigned effort, a shared feed, optional friendly competition, and volunteer opportunities. Progress holds personal milestones, history, and activity.
- Desktop task editing beside the table, selected-row bulk assignment, accurate unsaved-change counts, discard, and workload previews that include tasks outside the active filters. Phones use task cards and the full editor.
- More contains account links, reminders, iPhone installation help, and device-local appearance and quiet-celebration preferences.
- Explicit done/undo requests make retries safe after an interrupted connection, and stale requests cannot change a new recurrence period. Feedback and counts come from the saved server state, including the actual doer of a shared task.
- Task-specific notification links open the correct list and reveal the task, including future deadlines and completed tasks. Older dashboard-anchor links are handled too.

### Changed
- Warmer, more readable light and dark themes, larger touch targets, keyboard focus handling, reduced-motion support, and contextual mascot illustrations.
- Reminder setup says “Remind me” and offers notification permissions at the point of use. The main task screen no longer carries a notification setup card.
- Management uses “What done looks like” and “Include in task lists.” One-off tasks are still excluded from recurring workload estimates.
- Returning to the app refreshes stale task state while protecting unsaved management changes. Offline actions show recoverable feedback; offline writes are not queued.

## [1.3.0] - 2026-11-08

On an iPhone, delete the old home-screen icon and add the app again to get the new name and icon. You'll need to log in and tap Enable notifications again on that phone.

### Added
- **MoleChores:** the app has a new name and a mascot, Poopoo Magoo the ChoreMole, who appears on the login, dashboard, scoreboard, reminder, manage, import and error pages. On the dashboard the pose follows your day: working, overdue, or celebrating once today's list is done. The internal `chores` names (package, folders, settings, domain) are unchanged, so nothing needs reconfiguring.
- New app icons featuring the mascot.
- **Windows install:** `deploy/windows/install.ps1` installs or updates the app on Windows 11, including on a Raspberry Pi, using Task Scheduler instead of systemd. The app runs as the low-privilege LOCAL SERVICE account.
- `manage.py --env-file` and `--log-file`, for running without systemd.
- GitHub Actions now also runs the tests on Windows x64 and Windows on ARM64.
- **Automatic updates on Windows:** `deploy/windows/update.ps1` checks GitHub every 15 minutes and installs the newest commit on `main` once its tests have passed. If the install fails, it restores the previous code and database.

### Changed
- The Windows ARM64 CI job is advisory, so it can't block an update by itself.
- A failed pip self-upgrade no longer stops `install.ps1`.

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
