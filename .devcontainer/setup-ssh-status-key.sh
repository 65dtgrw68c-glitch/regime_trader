#!/usr/bin/env bash
#
# Materializes the Oracle server's restricted read-only status SSH key from
# the ORACLE_STATUS_SSH_KEY Codespaces secret (repo Settings > Secrets and
# variables > Codespaces) into ~/.ssh/oracle_status on every codespace
# start. That key can only run deploy/ssh_status_dispatch.sh's whitelisted
# read-only subcommands on the server (see deploy/README.md) — it is not a
# general-purpose credential.
#
# Safe to run repeatedly; a no-op if the secret hasn't been set yet (e.g. a
# fork, or before it's added in GitHub).
set -uo pipefail

if [[ -z "${ORACLE_STATUS_SSH_KEY:-}" ]]; then
    exit 0
fi

mkdir -p ~/.ssh
chmod 700 ~/.ssh
printf '%s\n' "$ORACLE_STATUS_SSH_KEY" > ~/.ssh/oracle_status
chmod 600 ~/.ssh/oracle_status
echo "oracle_status SSH key installed from ORACLE_STATUS_SSH_KEY secret."
