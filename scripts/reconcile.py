"""reconcile.py — daily reconciliation: what the book SHOULD hold vs what the
broker actually holds, with every deviation classified.

Phase 4 of the 2026-08-24 audit requires, before live capital: "Täglicher
Reconciliation-Report: Zielbuch vs Brokerbestand, jede Abweichung erklärt."
This is that report. It answers one question — is the live account actually
holding the book the strategy decided on? — and, where it isn't, says which of
the known causes explains the gap rather than just printing a number.

What it does NOT do: trade, cancel, or repair anything. It is read-only by
construction, so it is safe to run at any time, including while the bot is
mid-rebalance (it will simply report the in-flight state as such).

Must run somewhere with real ALPACA_API_KEY/ALPACA_SECRET_KEY configured
(the deployed host, or a dev environment with paper keys in .env):

    python scripts/reconcile.py
    python scripts/reconcile.py --json                  # machine-readable, for cron
    python scripts/reconcile.py --weight-tolerance 0.02  # 2pp materiality band

Materiality is judged in PORTFOLIO WEIGHT, not raw share count. A held
position's price moves continuously while the market is open, so its share
count drifts a little from a freshly-recomputed target every minute even
with zero operational fault — the bot rebalances once a day, not
continuously. Flagging every nonzero share difference trains the reader to
ignore the report; only a weight-level deviation beyond `--weight-tolerance`
(default 1 percentage point of equity) is treated as material.

Exit codes (so a cron wrapper can alert on them):
    0  reconciled — every position within the weight-tolerance band, or explained
    1  deviations found that are NOT explained by a known benign cause
    2  could not reconcile (no credentials, broker unreachable, market data)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXIT_OK = 0
EXIT_UNEXPLAINED = 1
EXIT_ERROR = 2


def classify(diff_shares: float, diff_weight: "float | None",
             open_order_qty: float, halted: bool, market_open: bool,
             weight_tolerance: float) -> tuple[str, bool]:
    """Explain one deviation. Returns (explanation, is_benign).

    The point of this function is that a reconciliation report which only
    prints deltas trains the reader to ignore it. Every line either names a
    cause that accounts for the gap, or is flagged as needing a human.

    Sign convention: `diff_shares` is actual - target (negative = short of
    target), and `open_order_qty` is signed by side (positive = an open
    buy). An open order closes the gap when diff_shares + open_order_qty
    lands near zero.

    `diff_weight` is the same gap expressed as a fraction of equity
    (None if a price wasn't available to convert it). Materiality is judged
    on THIS, not on `diff_shares` — see the module docstring for why a
    share-count tolerance is the wrong tool while the market is open.
    """
    if halted:
        return ("risk HALT is active — the book is intentionally flat and no "
                "longer tracks target", True)

    if diff_weight is not None and abs(diff_weight) < weight_tolerance:
        return (f"within normal drift ({diff_weight:+.2%} of equity, "
                f"under the {weight_tolerance:.0%} materiality band)", True)

    if abs(open_order_qty) > 1e-6:
        residual = diff_shares + open_order_qty
        if abs(residual) < max(abs(diff_shares), 1.0) * 0.02:
            return (f"in flight — an open order for {open_order_qty:+.4f} "
                    f"accounts for this gap", True)
        return (f"partially in flight — open order {open_order_qty:+.4f} leaves "
                f"{residual:+.4f} of a {diff_shares:+.4f} gap unaccounted for", False)

    if market_open:
        return ("material and no order is pending — the book should "
                "already match; investigate", False)

    if diff_shares < 0 and abs(diff_shares) > 1e-6 and (
        diff_weight is None or diff_weight <= -weight_tolerance
    ):
        return ("position is short of target — check for an external close, a "
                "rejected order, or insufficient buying power", False)

    if diff_shares > 0:
        return ("holding more than the target book wants — check for a "
                "failed liquidation or a manual trade", False)

    return ("unexplained drift — no open order, no halt, no obvious cause", False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weight-tolerance", type=float, default=0.01,
                     help="portfolio-weight deviation treated as immaterial "
                          "(default 0.01 = 1 percentage point of equity)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    try:
        from broker.alpaca_client import AlpacaClient
        from broker.position_tracker import PositionTracker
        from data.market_data import MarketDataFeed
        from main import TradingSystem
    except Exception as exc:                                  # pragma: no cover
        print(f"reconcile: import failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        client = AlpacaClient()
        if not client.verify_connection():
            print("reconcile: broker connection/account not usable", file=sys.stderr)
            return EXIT_ERROR
    except Exception as exc:
        print(f"reconcile: cannot reach broker: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # Rebuild the target book the same way the live path does — through
    # TradingSystem.startup(), so this reconciles against the REAL weight
    # construction (trend -> selector -> allocator -> sleeves) rather than a
    # reimplementation that could drift from it.
    system = TradingSystem(client=client, data_feed=MarketDataFeed(client=client))
    if not system.startup():
        print("reconcile: startup failed — cannot compute a target book",
              file=sys.stderr)
        return EXIT_ERROR

    halted = bool(system._risk.is_halted()) if system._risk else False
    market_open = bool(client.is_market_open())
    equity = system._current_equity()

    target_weights = system._compute_live_target_book()
    prices = {}
    for ticker, state in system._states.items():
        if state.history is not None and not state.history.empty:
            prices[ticker] = float(state.history["close"].iloc[-1])
    target_positions = system._target_positions_from_weights(
        target_weights, prices, equity,
    )

    tracker = PositionTracker(client)
    tracker.refresh()
    live = tracker.get_positions()

    # Open orders, per ticker, so "in flight" can be distinguished from "wrong".
    open_qty: dict[str, float] = {}
    try:
        for o in client.trading.get_orders():
            sym = str(getattr(o, "symbol", ""))
            qty = float(getattr(o, "qty", 0) or 0)
            filled = float(getattr(o, "filled_qty", 0) or 0)
            side = str(getattr(o, "side", "")).lower()
            remaining = qty - filled
            open_qty[sym] = open_qty.get(sym, 0.0) + (
                remaining if "buy" in side else -remaining
            )
    except Exception as exc:
        print(f"reconcile: warning — could not read open orders: {exc}",
              file=sys.stderr)

    rows = []
    unexplained = 0
    for ticker in sorted(set(target_positions) | set(live)):
        target = float(target_positions.get(ticker, 0.0))
        pos = live.get(ticker)
        actual = float(pos.qty) if pos else 0.0
        diff = actual - target

        price = prices.get(ticker) or (pos.current_price if pos else None)
        diff_weight = (diff * price / equity) if price and equity else None

        exact_match = abs(diff) < 1e-6
        if exact_match:
            explanation, benign = "matches target exactly", True
        else:
            explanation, benign = classify(
                diff, diff_weight, open_qty.get(ticker, 0.0),
                halted, market_open, args.weight_tolerance,
            )
            if not benign:
                unexplained += 1

        rows.append({
            "ticker": ticker,
            "target_weight": round(float(target_weights.get(ticker, 0.0)), 6),
            "target_qty": round(target, 4),
            "actual_qty": round(actual, 4),
            "diff": round(diff, 4),
            "diff_weight": round(diff_weight, 6) if diff_weight is not None else None,
            "open_order_qty": round(open_qty.get(ticker, 0.0), 4),
            "matched": exact_match or benign,
            "explanation": explanation,
            "benign": benign,
        })

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account_equity": round(equity, 2),
        "market_open": market_open,
        "halted": halted,
        "weight_tolerance": args.weight_tolerance,
        "positions": rows,
        "unexplained_deviations": unexplained,
        "reconciled": unexplained == 0,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("=" * 84)
        print(f"Reconciliation — {report['timestamp']}")
        print("=" * 84)
        print(f"equity {equity:,.2f}   market {'OPEN' if market_open else 'closed'}"
              f"   halt {'ACTIVE' if halted else 'clear'}   "
              f"weight tolerance {args.weight_tolerance:.0%}")
        print("-" * 84)
        print(f"{'ticker':<8}{'target w':>10}{'target':>11}{'actual':>11}{'diff':>10}  status")
        for r in rows:
            flag = "ok " if r["matched"] else "!! "
            print(f"{r['ticker']:<8}{r['target_weight']:>10.4f}{r['target_qty']:>11.4f}"
                  f"{r['actual_qty']:>11.4f}{r['diff']:>+10.4f}  {flag}{r['explanation']}")
        print("-" * 84)
        if report["reconciled"]:
            print("RECONCILED — every position matches target, or is explained.")
        else:
            print(f"NOT RECONCILED — {unexplained} deviation(s) need a human.")
        print("=" * 84)

    system.shutdown("reconcile")
    return EXIT_OK if report["reconciled"] else EXIT_UNEXPLAINED


if __name__ == "__main__":
    raise SystemExit(main())
