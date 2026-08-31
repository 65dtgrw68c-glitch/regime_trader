# Phase 4 — paper-trading validation: what is ready, what needs the server

2026-08-25. Phase 4 of the 2026-08-24 audit is the last gate before live capital. Unlike
Phases 0-3, most of it cannot be completed from a dev machine: it requires the deployed
host, real credentials, and calendar time. This file records exactly which parts are
now ready to run, which are blocked on the server, and what each one has to show.

## Ready — built and tested here

### 1. Crash mid-rebalance, with documented recovery ✅

The audit's Go/No-Go list requires "ein simulierter Absturz mitten im Rebalance, mit
dokumentierter Wiederherstellung". Done as an automated test rather than a one-off
manual drill, so it re-runs on every commit:
`tests/test_main.py::TestCrashDuringRebalanceRecovery` (4 cases), driven against a fake
broker that enforces `client_order_id` uniqueness the way Alpaca does.

| Scenario | Required behaviour | Verified |
|---|---|---|
| Process dies after submitting leg 1; restart recomputes the same decision against a stale snapshot | The `client_order_id` collides, the broker refuses the duplicate, the position is **not** doubled | ✅ |
| Restart with leg 1 filled and leg 2 never sent | Leg 1 is not re-ordered; leg 2 **is** submitted — recovery finishes the job | ✅ |
| Partial fill, same trading day, new target | The COID carries the target quantity, so "the rest of this decision" and "a new decision" are distinguishable and the book is never stuck untradeable for the day | ✅ |
| Crash between submit and response; retry hits a duplicate rejection | `submit_order` returns the **existing** order id (via `get_order_by_client_id`), not `""` — a live order is never left untracked | ✅ |

This is the recovery documentation the gate asks for. A manual drill on the server is
still worth doing once (below), but the mechanism itself is now covered by tests.

### 2. Daily reconciliation report ✅ (built, tested against a real paper account)

`scripts/reconcile.py`. Read-only by construction — it never trades, cancels, or
repairs, so it is safe to run at any time including mid-rebalance.

It rebuilds the target book by calling `TradingSystem.startup()` and
`_compute_live_target_book()` — the **real** weight-construction path — rather than
reimplementing it, so the report cannot drift from what the bot actually decides. That
was the exact failure mode found on 2026-08-01 (the live loop had silently diverged from
the validated path), so it is worth stating explicitly.

Every deviation is classified rather than just printed, because a report that only shows
deltas trains its reader to ignore it. The classifier is unit-tested
(`tests/test_reconcile.py`, 9 cases) including the case that is easy to get wrong: an
open order pointing *away* from target must not read as benign "in flight".

**Smoke-tested against this Codespace's `.env` paper credentials while building it**
(a real account, though not one this environment actively trades — see the note below).
The first version compared raw share counts against a `0.01`-share tolerance and, run
mid-day, flagged three ordinary positions (0.15-1.45% of position size — pure intraday
price drift since the morning's fill) as needing a human. That is exactly the failure
mode the script exists to avoid: a share-count tolerance cannot distinguish "the price
moved since this morning" from "something is wrong", because the bot rebalances once a
day, not continuously, while the account's market value keeps moving throughout the
session. Fixed by moving materiality to **portfolio weight** — the quantity the strategy
actually manages — with a default 1-percentage-point band; the `market_open` branch only
fires once a deviation clears that band. Re-run after the fix produced a mix of `ok` and
correctly-flagged rows against real (if stale) account state.

*This Codespace is not the deployed host* — no `systemd` timer, no bot log files here,
only the paper API keys in `.env`. The account `reconcile.py` reached is therefore not
under continuous live management from this environment, so the deviations it found here
reflect an account nobody is actively rebalancing, not a live operational fault. That
distinction matters for reading its output on the real deployed host, where a flagged
row means the bot that ran a few hours ago should have already matched.

Exit codes are cron-friendly: `0` reconciled, `1` unexplained deviations, `2` could not
reconcile. Suggested deployment: run after the close, alert on non-zero.

## Blocked on the deployed server

These cannot be faked and are not attempted here.

### 3. ≥60 trading days of paper operation ⏳

Calendar-bound. Nothing to build; it needs to run. Note that the Phase 1/2 fixes changed
live behaviour (all-or-nothing startup, buying-power gate, price sanity band, cache-tail
fetch, cancel confirmation, new COID format), so **the 60-day count should start from
the deploy of those commits**, not from earlier paper history — the earlier days
exercised different code.

### 4. ≥30 measured fills, then `scripts/slippage_check.py` ⏳

The audit found `logs/trades.csv` holds 127 ORDER rows and **zero** FILL rows — there is
still no measured execution anywhere in this project's history, so the 2 bp slippage
assumption underlying every published number remains unverified. The tooling exists and
works; it needs fills to read.

Acceptance per the audit: realised slippage median ≤ 5 bp, p95 ≤ 20 bp. Note the
break-even against SPY is ~26 bp per turnover unit, so the strategy is not
cost-fragile — this measurement is about replacing an assumption with a number, not
about a knife-edge.

### 5. `scripts/check_cash_interest.py` against the bot's account ⏳

The 2026-08-13 addendum found 17 FILL and **0 INT** activities over two months — the
paper account demonstrably does not pay interest on idle cash. Every published figure
credits ^IRX on idle cash (worth ~37 bp/yr historically, ~119 bp at today's rates, on a
24% mean cash weight), so if the live account behaves the same way, the published CAGR
is overstated by roughly that much.

Run it on the deployed host, against the account number the bot actually uses — the
Codespace account is not necessarily the same one.

### 6. One manual crash drill on the server ⏳

Item 1 covers the mechanism in tests. Doing it once for real — `kill -9` mid-rebalance,
then restart and reconcile — also validates the parts tests cannot: systemd restart
behaviour, the instance lock, and that `reconcile.py` correctly reports the resulting
state. Cheap to do, and it exercises the operational path rather than the code path.

## Status summary

| # | Gate | State |
|---|---|---|
| 1 | Crash-recovery mechanism | ✅ tested |
| 2 | Reconciliation report | ✅ built, needs server to run |
| 3 | ≥60 paper trading days | ⏳ calendar |
| 4 | ≥30 fills + slippage measured | ⏳ server |
| 5 | Cash-interest verified | ⏳ server |
| 6 | Manual crash drill | ⏳ server |

**Phase 4 is not complete and cannot be completed from here.** Two of six gates are
closed; the remaining four need the deployed host and roughly three months of calendar
time.

## And a reminder about what Phase 4 does not settle

Passing every gate above establishes that the bot *operates* correctly. It says nothing
about finding K1 — the sleeve's tail risk — which remains open and is a separate,
unresolved owner decision (`analysis_report_2026-08-25_k1_options.md`). The audit's
Go/No-Go list requires both: K1 addressed **and** Phase 4 passed. Neither is done.
