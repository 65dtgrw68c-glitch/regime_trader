#!/usr/bin/env bash
#
# monitor.sh — run the health check and push an alert to a webhook when the
# status CHANGES (healthy → unhealthy or back). Driven by
# regime-trader-monitor.timer; independent of the trading bot itself.
#
# Notifies only on transitions (state file), so an ongoing problem is not
# re-sent every cycle. Sends to Discord OR Slack from one payload (Discord
# reads "content", Slack reads "text"; each ignores the other key).
#
# Configure by adding to /opt/regime_trader/.env:
#     ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...   (or Slack URL)
# With no URL set it does nothing (safe default).
#
#   bash deploy/monitor.sh            # normal check
#   bash deploy/monitor.sh --test     # send a test message and exit
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/regime_trader}"
DEPLOY_DIR="$APP_DIR/deploy"
ENV_FILE="$APP_DIR/.env"
STATE_FILE="$APP_DIR/logs/.monitor_status"
HOST="$(hostname)"

# ── Load the webhook URL (only) from .env ────────────────────────────────────
WEBHOOK=""
if [[ -f "$ENV_FILE" ]]; then
    WEBHOOK="$(grep -E '^ALERT_WEBHOOK_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
fi

# ── Send a message to Discord/Slack (JSON built safely by python3) ────────────
send() {
    local msg="$1"
    if [[ -z "$WEBHOOK" ]]; then
        echo "monitor: no ALERT_WEBHOOK_URL in $ENV_FILE — nothing sent."
        return 0
    fi
    local payload
    payload="$(python3 -c 'import json,sys; m=sys.argv[1]; print(json.dumps({"content": m, "text": m}))' "$msg")"
    if curl -fsS -m 15 -X POST -H 'Content-Type: application/json' \
            -d "$payload" "$WEBHOOK" >/dev/null 2>&1; then
        echo "monitor: alert sent."
    else
        echo "monitor: webhook POST failed."
    fi
}

# ── --test: prove the webhook works, then exit ───────────────────────────────
if [[ "${1:-}" == "--test" ]]; then
    send "🔔 regime-trader test alert from ${HOST} — your webhook works."
    exit 0
fi

# ── Run the health check, capture output + verdict ───────────────────────────
output="$(bash "$DEPLOY_DIR/healthcheck.sh" 2>&1)"
rc=$?
now="ok"; (( rc != 0 )) && now="fail"

prev="ok"
[[ -f "$STATE_FILE" ]] && prev="$(cat "$STATE_FILE" 2>/dev/null || echo ok)"

# ── Notify only on a state change ────────────────────────────────────────────
if [[ "$now" == "fail" && "$prev" != "fail" ]]; then
    send "⚠️ *regime-trader UNHEALTHY* on ${HOST}
\`\`\`
${output}
\`\`\`
Check: journalctl -u regime-trader.service -e"
elif [[ "$now" == "ok" && "$prev" == "fail" ]]; then
    send "✅ *regime-trader recovered* on ${HOST} — all checks green again."
fi

echo "$now" > "$STATE_FILE" 2>/dev/null || true
echo "monitor: status=$now (was $prev)"
exit 0
