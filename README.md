# MoleChores

[![tests](../../actions/workflows/tests.yml/badge.svg)](../../actions/workflows/tests.yml)

Household task tracker for the Moll household, with Poopoo Magoo the ChoreMole. It runs on the Raspberry Pi and will be live at **https://chores.moll.casa**.

The 109 task cards are dealt to Chris, Andrea, Shared, or Needs Volunteer. Each of you sees what's left today, this week and this month, crosses tasks off, and can check the scoreboard. Tasks can have due dates, and you can ask for a push notification on your iPhone if one isn't done in time.

```bash
sudo ./deploy/install.sh        # on the Pi: installs, asks for the three passwords, starts the service
```

On a Pi running Windows 11, use `deploy\windows\install.ps1` instead: see [MANUAL § Installing on Windows 11](docs/MANUAL.md#installing-on-windows-11).

Then set up HTTPS by following [docs/MANUAL.md § Going live](docs/MANUAL.md#8-going-live-at-choresmollcasa).

- **Manual** (goals, how to use it, architecture, design choices, operations): [docs/MANUAL.md](docs/MANUAL.md)
- **Changelog:** [docs/CHANGELOG.md](docs/CHANGELOG.md)

Local development (VS Code run configs are included, see [MANUAL § Developing](docs/MANUAL.md#11-developing)):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py init && python manage.py serve     # http://127.0.0.1:8087
pytest
```
