#!/usr/bin/env bash
#
# healthcheck.sh — one-shot liveness/sanity check for the running service.
# Exit 0 = healthy, 1 = attention needed.  Safe to wire into cron + an alert.
#
#   bash deploy/healthcheck.sh
#   */15 * * * * root bash /opt/regime_trader/deploy/healthcheck.sh || \
#       curl -fsS -X POST "$WEBHOOK" -d 'regime-trader unhealthy'
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/regime_trader}"
SERVICE="regime-trader"
LOG="$APP_DIR/logs/app.log"
STALE_MINUTES="${STALE_MINUTES:-20}"   # log must have advanced within this window
rc=0

say() { printf '%-22s %s\n' "$1" "$2"; }

# 1) Risk HALT lock — the single most important thing to surface.
if [[ -f "$APP_DIR/logs/RISK_HALT.lock" ]]; then
    say "risk_halt" "PRESENT — bot stopped, needs human review!"
    rc=1
else
    say "risk_halt" "clear"
fi

# 2) systemd unit state.
active="$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
say "service" "$active"
[[ "$active" == "active" ]] || rc=1

# 3) Log freshness — is the poll loop actually advancing?  (Weekends/holidays
#    still poll, so the file mtime should move even when the market is shut.)
if [[ -f "$LOG" ]]; then
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$LOG") ) / 60 ))
    if (( age_min <= STALE_MINUTES )); then
        say "log_freshness" "ok (${age_min}m ago)"
    else
        say "log_freshness" "STALE (${age_min}m > ${STALE_MINUTES}m)"
        rc=1
    fi
    # 4) Paused / repeated-failure markers in the recent tail.
    if tail -n 50 "$LOG" | grep -qE "Pausing trading|reconnect failed|unreachable"; then
        say "recent_errors" "found pause/API-failure markers in last 50 lines"
        rc=1
    else
        say "recent_errors" "none"
    fi
else
    say "log_freshness" "NO LOG FILE at $LOG"
    rc=1
fi

exit "$rc"
