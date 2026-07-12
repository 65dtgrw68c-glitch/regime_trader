#!/usr/bin/env bash
#
# setup.sh — provision regime_trader as a 24/7 systemd service.
#
# Target: a fresh Oracle Cloud Free Tier "Ampere" ARM instance running
# Ubuntu 22.04/24.04 (also works on any Debian/Ubuntu aarch64 or x86_64 VM).
# Idempotent: safe to re-run (e.g. after `git pull`).
#
# Run as root (or via sudo) FROM the cloned repo directory:
#   sudo bash deploy/setup.sh
#
# It does NOT touch your credentials — you place .env yourself (see README).
set -euo pipefail

APP_USER="regime"
APP_DIR="/opt/regime_trader"
SERVICE="regime-trader"
PYTHON="${PYTHON:-python3}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/setup.sh" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 1) System packages ──────────────────────────────────────────────────────
# build-essential/python3-dev are a safety net: numpy/scipy/pandas/hmmlearn
# ship aarch64 wheels, but if pip ever falls back to a source build it needs
# a compiler. tzdata so the market-clock logic sees correct UTC time.
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-dev build-essential \
    git tzdata ca-certificates
timedatectl set-timezone UTC || true

# ── 2) Service user (no login, no home clutter) ─────────────────────────────
log "Creating service user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# ── 3) Code into $APP_DIR ───────────────────────────────────────────────────
log "Syncing code to $APP_DIR"
mkdir -p "$APP_DIR"
# Copy the repo (excluding the venv and local secrets) into place.
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.git' --exclude 'logs/*' \
    "$REPO_DIR"/ "$APP_DIR"/
mkdir -p "$APP_DIR/logs" "$APP_DIR/data_cache"

# ── 4) Python venv + deps ───────────────────────────────────────────────────
log "Building virtualenv"
if [[ ! -d "$APP_DIR/.venv" ]]; then
    "$PYTHON" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
# Runtime deps only (no streamlit/pytest/pyfolio dashboards on the server).
"$APP_DIR/.venv/bin/pip" install -q \
    "numpy>=1.26" "pandas>=2.2" "scipy>=1.13" "hmmlearn>=0.3" \
    "alpaca-py>=0.20" "python-dotenv>=1.0" "requests>=2.32"

# ── 5) Ownership + .env permissions ─────────────────────────────────────────
log "Setting ownership"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
if [[ -f "$APP_DIR/.env" ]]; then
    chmod 600 "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
else
    echo "  !! No $APP_DIR/.env yet — copy your Alpaca credentials there"
    echo "     BEFORE starting the service (see deploy/README.md)."
fi

# ── 6) systemd unit ─────────────────────────────────────────────────────────
log "Installing systemd unit"
install -m 644 "$APP_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"

log "Done."
cat <<EOF

Next steps:
  1. Put your Alpaca paper credentials in $APP_DIR/.env  (chmod 600), e.g.:
         ALPACA_API_KEY=...
         ALPACA_SECRET_KEY=...
         PAPER=true
  2. Start it:      sudo systemctl start $SERVICE
  3. Watch it:      journalctl -u $SERVICE -f
  4. Health check:  bash $APP_DIR/deploy/healthcheck.sh
EOF
