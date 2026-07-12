#!/usr/bin/env bash
#
# update.sh — pull the latest code and restart the service.
# Run from your git checkout (NOT /opt/regime_trader) as root:
#   sudo bash deploy/update.sh
#
# Refuses to restart while the risk HALT lock is present — a running halt is
# a human-review state, and blindly restarting would either loop or hide it.
set -euo pipefail

APP_DIR="/opt/regime_trader"
SERVICE="regime-trader"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run as root." >&2; exit 1; fi

if [[ -f "$APP_DIR/logs/RISK_HALT.lock" ]]; then
    echo "!! Risk HALT lock present at $APP_DIR/logs/RISK_HALT.lock" >&2
    echo "   Review the incident and delete it before updating/restarting." >&2
    exit 3
fi

echo "==> git pull"
git -C "$REPO_DIR" pull --ff-only

echo "==> re-running setup (syncs code + deps, reinstalls units)"
bash "$REPO_DIR/deploy/setup.sh"

# Scheduled model: nothing long-running to restart — setup already did
# daemon-reload and re-enabled the timer, so the NEXT daily fire runs the new
# code. Just confirm the schedule is armed.
echo "==> timer status"
systemctl --no-pager list-timers "$SERVICE.timer" || true
echo
echo "Update applied. It takes effect on the next scheduled run"
echo "(run 'sudo systemctl start $SERVICE.service' to exercise it now)."
