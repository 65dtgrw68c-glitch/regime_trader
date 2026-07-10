"""
Stop-loss / take-profit sweep — measure per-trade protective exits on the
pinned profile before anyone turns them on.

Why this exists
---------------
RISK.stop_loss_pct / take_profit_pct sat in the config for months without
any code implementing them (a false sense of protection).  The mechanics
are now implemented in the backtester (intraday against each bar's
low/high, gap-through fills at the open, stop-before-TP within a bar) but
OFF by default.  This sweep is the evidence for that default: it runs the
pinned live profile with a grid of stop/TP levels over ~28 years per
ticker, including the exact legacy pair (2% / 4%) that the config used to
advertise.

A-priori expectation (stated before the first run): tight stops re-create
the measured sell-low/rebuy-next-open whipsaw of the daily circuit
breakers, and take-profits cap the right-tail trades a trend follower
lives on.  If a row here ever looks BETTER than the baseline, confirm it
on a ticker/period you did not tune on before believing it.

Usage
-----
    python scripts/stop_sweep.py                     # SPY+QQQ, 30y via Yahoo
    python scripts/stop_sweep.py --tickers SPY --bars 2000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtester import Backtester                      # noqa: E402
from core.performance import (                              # noqa: E402
    annualised_return,
    max_drawdown,
    sharpe_ratio,
    total_return,
)
from run_experiments import (                               # noqa: E402
    fetch_tbill_yields,
    fetch_yahoo,
    sharpe_block_bootstrap_ci,
)
from settings import config                                 # noqa: E402

# (label, stop_loss_pct, take_profit_pct)
SWEEP: list[tuple[str, float, float]] = [
    ("baseline (off)",        0.00, 0.00),
    ("sl 2% (legacy value)",  0.02, 0.00),
    ("sl 2% + tp 4% (legacy pair)", 0.02, 0.04),
    ("sl 5%",                 0.05, 0.00),
    ("sl 10%",                0.10, 0.00),
    ("sl 15% (disaster stop)", 0.15, 0.00),
    ("tp 4% only",            0.00, 0.04),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-trade stop/TP sweep")
    ap.add_argument("--tickers", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--bars", type=int, default=7000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="experiments_report_stops.md")
    args = ap.parse_args(argv)

    try:
        tbill = fetch_tbill_yields()
    except Exception as exc:
        print(f"^IRX fetch failed ({exc}) — flat fallback.")
        tbill = None

    profile = dict(getattr(config, "ORCHESTRATOR", {}))
    sections: list[str] = []
    for ticker in args.tickers:
        data = fetch_yahoo(ticker, args.bars).iloc[-args.bars:]
        lines = [
            f"\n## {ticker} ({data.index[0].date()} … {data.index[-1].date()}, "
            f"{len(data)} bars)\n",
            "| Config | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD "
            "| Trades | Stop exits | TP exits |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for label, sl, tp in SWEEP:
            print(f"=== {ticker}: {label} ...")
            bt = Backtester(ticker=ticker, train_window=252, test_window=126,
                            random_seed=args.seed, strategy_overrides=profile,
                            cash_yield_series=tbill,
                            stop_loss_pct=sl, take_profit_pct=tp)
            res = bt.run(data)
            r, tl = res.returns, res.trade_log
            lo, hi = sharpe_block_bootstrap_ci(r)
            n_stop = int((tl["exit_reason"] == "stop_loss").sum()) if len(tl) else 0
            n_tp = int((tl["exit_reason"] == "take_profit").sum()) if len(tl) else 0
            lines.append(
                f"| {label} | {total_return(r):+.1%} | {annualised_return(r):+.1%} "
                f"| {sharpe_ratio(r):.2f} | [{lo:.2f}, {hi:.2f}] "
                f"| {max_drawdown(r):.1%} | {len(tl)} | {n_stop} | {n_tp} |"
            )
            print(f"    sharpe={sharpe_ratio(r):.2f} dd={max_drawdown(r):.1%} "
                  f"stops={n_stop} tps={n_tp}")
        sections.append("\n".join(lines))

    meta = (
        f"Per-trade stop/TP sweep on the pinned profile `{profile}`, cap "
        f"{config.RISK['max_position_size']:.2f}, walk-forward 252/126, "
        f"seed {args.seed}; cash yield "
        f"{'^IRX series' if tbill is not None else 'flat fallback'}; "
        f"next-open fills; stops simulated intraday vs bar low/high "
        f"(gap-through → open fill).\n"
    )
    report = "# Stop-loss / take-profit sweep\n\n" + meta + "\n".join(sections) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"\nReport written to {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
