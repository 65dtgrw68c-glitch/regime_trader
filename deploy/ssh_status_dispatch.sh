#!/usr/bin/env bash
#
# ssh_status_dispatch.sh — the ONLY thing the Codespace's restricted SSH key
# is allowed to run on the server (enforced via a forced `command=` in
# authorized_keys + a matching sudoers NOPASSWD rule; see deploy/README.md
# "Read-only status access from Codespace"). Whitelists a small set of
# read-only status subcommands — no trading, no writes, no arbitrary shell.
#
# Usage: ssh_status_dispatch.sh <subcommand>
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/regime_trader}"
cmd="${1:-}"

case "$cmd" in
    healthcheck)
        bash "$APP_DIR/deploy/healthcheck.sh"
        ;;
    reconcile)
        cd "$APP_DIR" && python3 scripts/reconcile.py
        ;;
    gitlog)
        cd "$APP_DIR" && git log -1 --oneline
        ;;
    taillog)
        tail -n 100 "$APP_DIR/logs/app.log" 2>/dev/null || echo "no log file yet"
        ;;
    *)
        echo "ssh_status_dispatch: command not allowed: '$cmd'" >&2
        echo "allowed: healthcheck | reconcile | gitlog | taillog" >&2
        exit 1
        ;;
esac
