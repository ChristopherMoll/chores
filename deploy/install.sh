#!/usr/bin/env bash
# Install or update chores on a Raspberry Pi (Raspberry Pi OS / Debian).
# Run from the unpacked project folder:   sudo ./deploy/install.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/chores DATA=/var/lib/chores ETC=/etc/chores

manage() { sudo -u chores bash -c "set -a; source $ETC/chores.env; cd $APP && .venv/bin/python manage.py $*"; }

apt-get install -y -qq python3-venv rsync >/dev/null
id chores &>/dev/null || useradd --system --home "$DATA" --shell /usr/sbin/nologin chores
mkdir -p "$APP" "$DATA" "$ETC"

# Copy code (never touches the database in $DATA).
rsync -a --delete --exclude .git --exclude .venv --exclude instance --exclude backups --exclude '__pycache__' \
      --exclude .pytest_cache "$SRC"/ "$APP"/
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

[[ -f "$ETC/chores.env" ]] || cp "$APP/deploy/chores.env.example" "$ETC/chores.env"
# systemd keeps end-of-line comments as part of the value; earlier env files had them.
sed -i -E '/^[[:space:]]*#/! s/[[:space:]]+#.*$//' "$ETC/chores.env"
chown -R chores:chores "$DATA"
chmod 750 "$DATA"

cp "$APP/deploy/chores.service" "$APP/deploy/chores-backup.service" "$APP/deploy/chores-backup.timer" \
   "$APP/deploy/chores-notify.service" "$APP/deploy/chores-notify.timer" /etc/systemd/system/
systemctl daemon-reload

if [[ ! -f "$DATA/chores.sqlite3" ]]; then
  echo
  echo "First install: creating the database and the three accounts."
  manage init
else
  echo "Existing database: backing it up, then applying any schema changes."
  systemctl stop chores chores-notify.timer 2>/dev/null || true
  manage backup "$DATA/backups"
  manage migrate
fi

systemctl enable --now chores chores-backup.timer chores-notify.timer
systemctl restart chores
sleep 1
systemctl --no-pager --lines=3 status chores || true
echo
echo "Running on 127.0.0.1:8087. Next: set up Caddy (see docs/MANUAL.md, 'Going live')."
