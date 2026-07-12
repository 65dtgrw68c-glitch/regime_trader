# Deploying regime_trader (Oracle Cloud Free Tier)

Runs the bot on an **Always-Free** Oracle Cloud Ampere (ARM) VM as a
**scheduled once-a-day job**: a `systemd` timer fires shortly after the US
open on trading days, the bot makes one decision per ticker, and exits. The
host idles the rest of the time.

> **Why scheduled, not always-on.** The strategy trades **daily bars** — it
> makes at most one decision per ticker per day. `startup()` rebuilds all
> state from scratch every run (history, HMM, position sync, stale-order
> cancel), so a fresh ~15-second process each day is fully self-contained and
> has no long-lived process to hang, leak, or babysit. (An always-on variant
> is documented at the bottom if you ever want it.)
>
> It stays a **paper** account until you deliberately flip three switches
> (see "Going live"). Don't, until you've watched it trade for a while.

---

## 1. Create the Free-Tier instance

1. Oracle Cloud console → **Compute → Instances → Create Instance**.
2. **Shape:** **Ampere / VM.Standard.A1.Flex**, 1 OCPU / 6 GB RAM
   (well within Always-Free).
3. **Image:** Canonical **Ubuntu 22.04** or 24.04, aarch64.
4. **SSH keys:** upload your public key (or save the generated private key).
5. Create; note the **public IP**.

No inbound ports needed — the bot only makes **outbound** HTTPS calls to
Alpaca. Leave the default security list (SSH in, everything out).

## 2. First login & get the code

```bash
ssh ubuntu@<PUBLIC_IP>
sudo apt-get update -qq && sudo apt-get install -y git
git clone <YOUR_REPO_URL> regime_trader
cd regime_trader
```

## 3. Provision

```bash
sudo bash deploy/setup.sh
```

Creates the `regime` service user, copies code to `/opt/regime_trader`,
builds a venv with **runtime** deps only, installs the service + timer, and
enables the **timer**. Re-runnable any time.

## 4. Add your Alpaca credentials

```bash
sudo -u regime tee /opt/regime_trader/.env >/dev/null <<'EOF'
ALPACA_API_KEY=YOUR_PAPER_KEY
ALPACA_SECRET_KEY=YOUR_PAPER_SECRET
PAPER=true
EOF
sudo chmod 600 /opt/regime_trader/.env
```

Use your **paper** keys. `PAPER=true` pins the paper endpoint.

## 5. Arm the schedule & test once

```bash
# Start the daily schedule
sudo systemctl start regime-trader.timer
systemctl list-timers regime-trader.timer      # confirm the next fire time

# Run one cycle right now to prove it works end-to-end
sudo systemctl start regime-trader.service
journalctl -u regime-trader.service -f         # watch the 10-step startup + decision
```

The manual run does a full startup, one decision per ticker, then exits. On a
weekend/holiday/closed market it is a clean no-op (`stale_bar` /
`skipped_market_closed`) — that is expected.

## 6. Verify health

```bash
bash /opt/regime_trader/deploy/healthcheck.sh
```

Wire it into cron as a daily heartbeat + alert (run it midday, after the
morning fire):

```bash
# /etc/cron.d/regime-trader-health
0 16 * * 1-5 root bash /opt/regime_trader/deploy/healthcheck.sh >/dev/null || \
  curl -fsS -X POST "$YOUR_WEBHOOK_URL" -d 'regime-trader UNHEALTHY'
```

---

## Day-to-day operations

| Task | Command |
|---|---|
| Next scheduled run | `systemctl list-timers regime-trader.timer` |
| Last run's logs | `journalctl -u regime-trader.service -e` |
| Follow a live run | `journalctl -u regime-trader.service -f` |
| App log | `tail -f /opt/regime_trader/logs/app.log` |
| Run once now | `sudo systemctl start regime-trader.service` |
| Pause the bot | `sudo systemctl stop regime-trader.timer` (+ `disable` to survive reboot) |
| Resume | `sudo systemctl start regime-trader.timer` |
| Update to latest code | `sudo bash deploy/update.sh` (from your checkout) |
| Trades audit trail | `/opt/regime_trader/logs/trades.csv` |

### When the risk HALT fires (−20% drawdown)

The bot writes `/opt/regime_trader/logs/RISK_HALT.lock` and every daily run
becomes a no-op exit **3** (it checks the lock before doing anything). Nothing
trades until you clear it:

```bash
cat /opt/regime_trader/logs/RISK_HALT.lock     # read the incident report
# ... review ...
sudo rm /opt/regime_trader/logs/RISK_HALT.lock # only after you've decided
```

### Going live (real money) — later, deliberately

1. You have watched it paper-trade across at least one full bear/rebound.
2. Swap `.env` to live keys and set `PAPER=false`.
3. Set `BROKER["mode"] = "live"` in `settings/config.py`.
4. `sudo bash deploy/update.sh`.

Until all three are done it stays paper — no accidental path to real orders.

---

## Notes & caveats

- **Firing time.** 09:35 ET, Mon-Fri, via `OnCalendar=Mon-Fri 09:35
  America/New_York` — the explicit timezone means systemd tracks EST/EDT so
  it stays 5 min after the open all year. Holidays fire but no-op.
- **Decision vs fill timing.** The bot decides on today's fresh bar and
  submits an immediate market order. The backtest models "decide on the
  completed close, fill next open"; firing near the *close* and submitting
  market-on-open orders for the next day would map that even more exactly —
  a possible refinement, not required for a slow SMA-200 system.
- **Missed runs.** `Persistent=true`: if the host was off at fire time it
  runs on next boot rather than skipping the day.
- **Reboots / data feed / clock:** free Alpaca IEX daily bars (no SIP sub);
  `startup()` cancels stale broker orders first; the bot asks Alpaca for the
  market clock rather than trusting local time.

### Alternative: always-on daemon

If you ever want a warm 24/7 process instead (e.g. moving to intraday bars),
replace the oneshot `ExecStart` with `main.py` (no `--once`), set
`Type=simple`, `Restart=always`, `RestartSec=30`,
`RestartPreventExitStatus=3`, drop the timer, and `systemctl enable
regime-trader.service` directly. The scheduled model above is the right
default for daily bars.
