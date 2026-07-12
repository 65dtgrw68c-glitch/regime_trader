# Deploying regime_trader as a 24/7 service (Oracle Cloud Free Tier)

This runs the bot as a hardened `systemd` service on an **Always-Free**
Oracle Cloud Ampere (ARM) VM. It auto-restarts on crash, refuses to
restart-loop into a risk-HALT, and survives host reboots.

> **Reality check.** The bot trades **daily bars** — it makes at most one
> decision per ticker per day, shortly after the US open (~13:30 UTC). A 24/7
> service is more than it strictly needs (a `systemd` timer firing once a day
> would do), but you chose the always-on model, so this keeps a warm process
> polling every 60s. It stays a **paper** account until you deliberately set
> `PAPER=false` — don't do that until you've watched it trade for a while.

---

## 1. Create the Free-Tier instance

1. Oracle Cloud console → **Compute → Instances → Create Instance**.
2. **Shape:** change to **Ampere / VM.Standard.A1.Flex**, 1 OCPU / 6 GB RAM
   (well within Always-Free; the bot needs far less).
3. **Image:** Canonical **Ubuntu 22.04** (or 24.04), aarch64.
4. **SSH keys:** upload your public key (or let it generate one — save the
   private key).
5. Create, then note the instance's **public IP**.

No inbound ports need opening — the bot only makes **outbound** HTTPS calls to
Alpaca. Leave the default security list as-is (SSH in, everything out).

## 2. First login & get the code

```bash
ssh ubuntu@<PUBLIC_IP>

# Clone your repo (or scp the directory up).
sudo apt-get update -qq && sudo apt-get install -y git
git clone <YOUR_REPO_URL> regime_trader
cd regime_trader
```

## 3. Provision

```bash
sudo bash deploy/setup.sh
```

This creates the `regime` service user, copies the code to
`/opt/regime_trader`, builds a venv with the **runtime** dependencies only,
and installs + enables the `systemd` unit. Re-runnable any time.

## 4. Add your Alpaca credentials

The setup script never touches secrets — you place them:

```bash
sudo -u regime tee /opt/regime_trader/.env >/dev/null <<'EOF'
ALPACA_API_KEY=YOUR_PAPER_KEY
ALPACA_SECRET_KEY=YOUR_PAPER_SECRET
PAPER=true
EOF
sudo chmod 600 /opt/regime_trader/.env
```

Use your **paper** keys from the Alpaca dashboard. `PAPER=true` keeps the bot
on the paper endpoint regardless of anything else.

## 5. Start it

```bash
sudo systemctl start regime-trader
journalctl -u regime-trader -f          # watch the 10-step startup live
```

You should see `[startup 10/10] Startup complete.` and a `STATUS | ...` line.
On a weekend/closed market it then goes quiet by design — the latest daily bar
is already known, so each 60s poll is a no-op (`stale_bar`).

## 6. Verify health

```bash
bash /opt/regime_trader/deploy/healthcheck.sh
```

All green = healthy. Wire it into cron for a heartbeat + alert:

```bash
# /etc/cron.d/regime-trader-health
*/15 * * * * root bash /opt/regime_trader/deploy/healthcheck.sh >/dev/null || \
  curl -fsS -X POST "$YOUR_WEBHOOK_URL" -d 'regime-trader UNHEALTHY'
```

---

## Day-to-day operations

| Task | Command |
|---|---|
| Status | `systemctl status regime-trader` |
| Live logs | `journalctl -u regime-trader -f` |
| App log | `tail -f /opt/regime_trader/logs/app.log` |
| Stop (graceful) | `sudo systemctl stop regime-trader` |
| Restart | `sudo systemctl restart regime-trader` |
| Update to latest code | `sudo bash deploy/update.sh` (from your checkout) |
| Trades audit trail | `/opt/regime_trader/logs/trades.csv` |

### When the risk HALT fires (−20% drawdown)

The bot writes `/opt/regime_trader/logs/RISK_HALT.lock`, stops, and exits with
code **3**. `systemd` is configured (`RestartPreventExitStatus=3`) to **not**
restart into it — the unit shows as failed/exited, which is correct: this
state needs *you*.

```bash
cat /opt/regime_trader/logs/RISK_HALT.lock     # read the incident report
# ... review what happened ...
sudo rm /opt/regime_trader/logs/RISK_HALT.lock # only after you've decided
sudo systemctl reset-failed regime-trader
sudo systemctl start regime-trader
```

### Going live (real money) — later, deliberately

1. You have watched it paper-trade across at least one full bear/rebound.
2. Swap `.env` to live keys and set `PAPER=false`.
3. Set `BROKER["mode"] = "live"` in `settings/config.py`.
4. `sudo bash deploy/update.sh`.

Until all three are done it stays paper — there is no accidental path to real
orders.

---

## Notes & caveats

- **Data feed:** free Alpaca IEX daily bars are used (no SIP subscription
  needed). If you ever see `subscription does not permit...` it means
  something requested intraday SIP data — the daily path avoids it.
- **Reboots:** the unit is `enabled`, so the bot comes back automatically
  after an Oracle host reboot; `startup()` rebuilds all state (history, HMM,
  positions) from scratch, and cancels any stale broker orders first.
- **Clock:** the VM is pinned to UTC; the bot asks Alpaca for the market clock
  rather than trusting local time, so DST is handled broker-side.
- **This is not HA.** One VM, one process. For a paper daily-bar bot that is
  entirely adequate. Don't add complexity you don't need.
