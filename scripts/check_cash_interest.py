"""Verify whether the live Alpaca account actually pays interest on idle cash.

Every published backtest number in this repo credits idle cash at the real
^IRX T-bill series (or the flat `config.BACKTEST["cash_yield_annual"]`
fallback) — worth 37bp/yr over the 2007-2026 sample (mean ^IRX 1.52%) but
~106bp/yr at today's ~4.3% short rates. That credit is only real money if the
live Alpaca account actually pays it. If it doesn't (e.g. because this is a
PAPER account, or a live account with no cash-sweep program enrolled), every
published CAGR/Sharpe number is inflated by exactly that much and the gap
grows every year rates stay elevated.

This queries the account's "INT" (interest) activity history — the only
place Alpaca records actual interest credits, since the account object
itself carries no interest-rate or accrual field. An empty result does NOT
by itself prove the account never pays interest (it may simply not have
accrued/posted yet); read the printed context and, if still ambiguous,
confirm directly with Alpaca (docs or support) whether cash interest applies
to this account type.

Must be run somewhere with real ALPACA_API_KEY/ALPACA_SECRET_KEY in .env —
this repo's dev Codespace has none (see README/deploy docs); run this on the
deployed host instead:

    python scripts/check_cash_interest.py
    python scripts/check_cash_interest.py --days 365
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker.alpaca_client import AlpacaClient  # noqa: E402
from settings import config  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=None,
                     help="only count INT activity from the last N days "
                          "(default: everything the API returns)")
    a = ap.parse_args(argv)

    client = AlpacaClient()
    try:
        client.connect()
    except Exception as exc:
        print(f"Could not connect to Alpaca: {exc}")
        print("Set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env and re-run "
              "this on the host that actually holds the account.")
        return 1

    account = client.get_account()
    is_paper = "paper-api" in (client.base_url or "") or client.paper
    print(f"Account: mode={'PAPER' if is_paper else 'LIVE'}  "
          f"status={account['status']}  cash=${account['cash']:,.2f}  "
          f"equity=${account['equity']:,.2f}")

    activities = client.get_account_activities("INT")
    if a.days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=a.days)
        activities = [
            act for act in activities
            if _activity_date(act) is None or _activity_date(act) >= cutoff
        ]

    assumed_annual = float(config.BACKTEST.get("cash_yield_annual", 0.0))
    print(f"\nBacktest assumption: {assumed_annual * 100:.2f}%/yr flat "
          f"(or the real ^IRX series where the harness supplies one) "
          f"credited on every idle-cash dollar.\n")

    if not activities:
        print("No INT (interest) activity found on this account.")
        if is_paper:
            print(
                "This is a PAPER account. Alpaca's cash-interest / sweep "
                "programs are documented as applying to LIVE brokerage "
                "accounts — paper trading is a simulation and, as far as "
                "this account's activity history shows, is NOT crediting "
                "the interest the backtest assumes. Treat every published "
                "return number as overstated by the backtest's cash-yield "
                "credit until this account is live and INT activity "
                "actually appears, or Alpaca support confirms otherwise."
            )
        else:
            print(
                "This is a LIVE account with no recorded interest activity. "
                "Either interest hasn't accrued/posted yet, or this account "
                "isn't enrolled in a cash-interest program — confirm with "
                "Alpaca directly. Until INT activity appears, treat the "
                "backtest's cash-yield credit as unverified."
            )
        return 0

    total = sum(float(act.get("net_amount", 0.0) or 0.0) for act in activities)
    dates = sorted(d for d in (_activity_date(act) for act in activities) if d)
    print(f"{len(activities)} INT entries"
          + (f", {dates[0].date()} .. {dates[-1].date()}" if dates else "")
          + f", total credited: ${total:,.2f}")

    if dates and len(dates) > 1:
        span_days = (dates[-1] - dates[0]).days or 1
        mean_cash = account["cash"]  # best available proxy; no historical series here
        if mean_cash > 0:
            annualized = total / mean_cash * (365.0 / span_days)
            print(f"Rough realized yield vs CURRENT cash balance: "
                  f"{annualized * 100:.2f}%/yr "
                  f"(uses today's cash as a stand-in for the historical "
                  f"average — a rough check, not a precise measurement).")
    print(
        "\nCompare the total above against what the backtest would have "
        "credited over the same window at the assumed rate; a large gap "
        "means the live account's cash treatment differs from the "
        "backtest assumption and the published CAGR/Sharpe numbers should "
        "be adjusted or re-run with cash_yield_annual set to the measured "
        "value."
    )
    return 0


def _activity_date(act: dict):
    raw = act.get("date") or act.get("transaction_time") or act.get("activity_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
