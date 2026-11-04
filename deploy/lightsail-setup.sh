#!/usr/bin/env bash
#
# One-shot setup for the GF-assignment backend on an AWS Lightsail Ubuntu
# instance (22.04 / 24.04). Run as the default `ubuntu` user via the
# browser-based SSH in the Lightsail console:
#
#   sudo bash lightsail-setup.sh gf.gpsfms.com
#
# What it does
#   • installs python3-venv + Caddy (automatic HTTPS from Let's Encrypt)
#   • copies ./backend to /opt/gf-assignment and creates a venv there
#   • systemd service  gf-api.service     → uvicorn on 127.0.0.1:8000
#   • systemd timer    gf-pretrip.timer   → pretrip_email.py daily at 10:00
#   • Caddy reverse-proxies https://<domain> → 127.0.0.1:8000
#
# Before running: point the DNS A record for <domain> at the instance's
# static IP, and open ports 80 + 443 in the Lightsail "Networking" tab.
# After running: edit /opt/gf-assignment/.env (and recipients.json), then
#   sudo systemctl restart gf-api && curl https://<domain>/api/health

set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "usage: sudo bash $0 <domain>   e.g. gf.gpsfms.com" >&2
  exit 1
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../backend"
APP=/opt/gf-assignment
EMAIL_TIME="${EMAIL_TIME:-10:00}"   # local server time; set the instance tz below

apt-get update -y
apt-get install -y python3 python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

# Run the emailer in the report timezone so "10:00" means 10:00 Pacific.
timedatectl set-timezone "${REPORT_TZ:-America/Los_Angeles}" || true

id -u gfapi >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin gfapi
mkdir -p "$APP"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude 'tests' --exclude 'out' "$SRC/" "$APP/"
[[ -f "$APP/.env" ]] || cp "$APP/.env.example" "$APP/.env"
[[ -f "$APP/recipients.json" ]] || cp "$APP/recipients.example.json" "$APP/recipients.json"
[[ -f "$APP/databases.json" ]] || cp "$APP/databases.example.json" "$APP/databases.json"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R gfapi:gfapi "$APP"
chmod 600 "$APP/.env" "$APP/databases.json"

cat >/etc/systemd/system/gf-api.service <<EOF
[Unit]
Description=GPSFMS GF Assignment API
After=network-online.target
Wants=network-online.target

[Service]
User=gfapi
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/gf-pretrip.service <<EOF
[Unit]
Description=GPSFMS daily pre-trip completion email

[Service]
Type=oneshot
User=gfapi
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python $APP/pretrip_email.py
EOF

cat >/etc/systemd/system/gf-pretrip.timer <<EOF
[Unit]
Description=Run the pre-trip completion email daily at $EMAIL_TIME

[Timer]
OnCalendar=*-*-* $EMAIL_TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat >/etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
    }
}
EOF

systemctl daemon-reload
systemctl enable --now gf-api.service gf-pretrip.timer
systemctl restart caddy

echo
echo "Done. Next:"
echo "  1. sudo nano $APP/databases.json  (one entry per database: demo + each client)"
echo "     sudo nano $APP/.env            (SMTP + shared defaults)"
echo "  2. sudo nano $APP/recipients.<database>.json   (GF name → emails, per database)"
echo "  3. sudo systemctl restart gf-api && curl -s https://$DOMAIN/api/health"
echo "  4. sudo -u gfapi $APP/.venv/bin/python $APP/pretrip_email.py --dry-run --database <db>"
echo "  5. journalctl -u gf-api -f         (logs)"
