#!/usr/bin/env bash
#
# One-shot setup for the GF-assignment backend on an AWS Lightsail Ubuntu
# instance (22.04 / 24.04). Safe on a SHARED box: it never replaces an
# existing Caddyfile — it appends one site block for <domain> and reloads
# Caddy — and it does not touch the system timezone or other services.
#
#   sudo bash lightsail-setup.sh gf.gpsfms.com
#   sudo API_PORT=8010 bash lightsail-setup.sh gf.gpsfms.com   # if 8000 is taken
#
# What it does
#   • installs python3-venv (+ Caddy only if the box has none)
#   • copies ./backend to /opt/gf-assignment and creates a venv there
#   • systemd service  gf-api.service     → uvicorn on 127.0.0.1:$API_PORT
#   • systemd timer    gf-pretrip.timer   → pretrip_email.py daily at
#                                           $EMAIL_TIME in $REPORT_TZ
#   • Caddy site block: https://<domain> → 127.0.0.1:$API_PORT
#
# Before running: point the DNS A record for <domain> at the instance's
# static IP, and open ports 80 + 443 in the Lightsail "Networking" tab.
# After running: edit /opt/gf-assignment/databases.json and .env, then
#   sudo systemctl restart gf-api && curl https://<domain>/api/health
#
# Re-running is safe (idempotent); it refreshes the code and units.

set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "usage: sudo bash $0 <domain>   e.g. gf.gpsfms.com" >&2
  exit 1
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../backend"
APP=/opt/gf-assignment
API_PORT="${API_PORT:-8000}"
EMAIL_TIME="${EMAIL_TIME:-10:00}"
REPORT_TZ="${REPORT_TZ:-America/Los_Angeles}"   # timer runs in this zone regardless of the box's tz

# -- port must be free (unless it's already ours) ------------------------------
if ss -ltn "( sport = :$API_PORT )" | grep -q LISTEN; then
  if ! systemctl is-active --quiet gf-api; then
    echo "port $API_PORT is already in use by another service — re-run with API_PORT=<free port>" >&2
    ss -ltnp "( sport = :$API_PORT )" >&2
    exit 1
  fi
fi

apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync curl gnupg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

# -- app files + venv -----------------------------------------------------------
id -u gfapi >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin gfapi
mkdir -p "$APP"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude 'tests' --exclude 'out' \
      --exclude '.env' --exclude 'databases.json' --exclude 'recipients*.json' "$SRC/" "$APP/"
cp -n "$SRC/recipients.example.json" "$APP/recipients.example.json" || true
cp -n "$SRC/databases.example.json" "$APP/databases.example.json" || true
[[ -f "$APP/.env" ]] || cp "$SRC/.env.example" "$APP/.env"
[[ -f "$APP/databases.json" ]] || cp "$SRC/databases.example.json" "$APP/databases.json"
[[ -d "$APP/.venv" ]] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R gfapi:gfapi "$APP"
chmod 600 "$APP/.env" "$APP/databases.json"

# -- systemd: API ----------------------------------------------------------------
cat >/etc/systemd/system/gf-api.service <<EOF
[Unit]
Description=GPSFMS GF Assignment API
After=network-online.target
Wants=network-online.target

[Service]
User=gfapi
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn app:app --host 127.0.0.1 --port $API_PORT --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# -- systemd: daily emailer (timezone-qualified, so the box's tz doesn't matter) --
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
Description=Run the pre-trip completion email daily at $EMAIL_TIME $REPORT_TZ

[Timer]
OnCalendar=*-*-* $EMAIL_TIME:00 $REPORT_TZ
Persistent=true

[Install]
WantedBy=timers.target
EOF

# -- Caddy: add a site block, never replace the file ------------------------------
CADDYFILE=/etc/caddy/Caddyfile
mkdir -p /etc/caddy
if [[ -f "$CADDYFILE" ]] && grep -qE "^\s*$DOMAIN\s*\{" "$CADDYFILE"; then
  echo "Caddyfile already has a block for $DOMAIN — leaving it alone"
else
  [[ -f "$CADDYFILE" ]] && cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
  cat >>"$CADDYFILE" <<EOF

# GPSFMS GF Assignment API (added by gf-assignment-addin/deploy/lightsail-setup.sh)
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:$API_PORT
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
    }
}
EOF
fi
caddy validate --config "$CADDYFILE" --adapter caddyfile

systemctl daemon-reload
systemctl enable --now gf-api.service gf-pretrip.timer
systemctl restart gf-api.service
if systemctl is-active --quiet caddy; then
  systemctl reload caddy        # keeps the other sites up
else
  systemctl enable --now caddy
fi

echo
echo "Done. Next:"
echo "  1. sudo nano $APP/databases.json  (one entry per database: pilot, then clients)"
echo "     sudo nano $APP/.env            (SMTP + shared defaults)"
echo "  2. sudo nano $APP/recipients.<database>.json   (GF name → emails, per database)"
echo "  3. sudo systemctl restart gf-api && curl -s https://$DOMAIN/api/health"
echo "  4. sudo -u gfapi $APP/.venv/bin/python $APP/pretrip_email.py --dry-run --database <db>"
echo "  5. journalctl -u gf-api -f         (logs)   ·   systemctl list-timers gf-pretrip.timer"
