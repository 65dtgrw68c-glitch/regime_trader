#!/usr/bin/env bash
#
# healthcheck.sh — sanity check for the SCHEDULED (timer) deployment.
# Exit 0 = healthy, 1 = attention needed.  Safe to wire into cron + an alert.
#
#   bash deploy/healthcheck.sh
#   0 12 * * * root bash /opt/regime_trader/deploy/healthcheck.sh || \
#       curl -fsS -X POST "$WEBHOOK" -d 'regime-trader unhealthy'
#
# Unlike an always-on daemon, the bot only runs ~15s once per trading day, so
# this checks the SCHEDULE is armed and the LAST run succeeded — not that a
# process is currently alive.
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/regime_trader}"
UNIT="regime-trader"
LOG="$APP_DIR/logs/app.log"
# One daily run + weekend gap: the log may legitimately be up to ~3 days old
# on a Monday morning before that day's run. Flag only well beyond that.
STALE_HOURS="${STALE_HOURS:-80}"
rc=0

say() { printf '%-20s %s\n' "$1" "$2"; }

# 1) Risk HALT lock — the single most important thing to surface.
if [[ -f "$APP_DIR/logs/RISK_HALT.lock" ]]; then
    say "risk_halt" "PRESENT — daily runs are no-ops until reviewed!"
    rc=1
else
    say "risk_halt" "clear"
fi

# 2) Timer armed?
tstate="$(systemctl is-active "$UNIT.timer" 2>/dev/null || true)"
say "timer" "$tstate"
[[ "$tstate" == "active" ]] || rc=1

# 3) Next scheduled fire.
next="$(systemctl show "$UNIT.timer" -p NextElapseUSecRealtime --value 2>/dev/null || true)"
say "next_run" "${next:-unknown}"

# 4) Result of the LAST daily run (success | "" if it has never run yet).
result="$(systemctl show "$UNIT.service" -p Result --value 2>/dev/null || true)"
if [[ -z "$result" || "$result" == "success" ]]; then
    say "last_run" "${result:-not-run-yet}"
else
    say "last_run" "FAILED ($result)"
    rc=1
fi

# 5) Log freshness — has a run written anything within STALE_HOURS?
if [[ -f "$LOG" ]]; then
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$LOG") ) / 3600 ))
    if (( age_h <= STALE_HOURS )); then
        say "log_freshness" "ok (${age_h}h ago)"
    else
        say "log_freshness" "STALE (${age_h}h > ${STALE_HOURS}h)"
        rc=1
    fi
else
    say "log_freshness" "NO LOG FILE at $LOG"
fi

exit "$rc"
