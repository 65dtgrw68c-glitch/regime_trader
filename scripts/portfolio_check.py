"""
Portfolio check — evaluate the LIVE two-ticker book (SPY+QQQ, each capped at
RISK.max_position_size of shared equity) as a JOINT portfolio instead of two
isolated single-name backtests.

Why this exists
---------------
The experiment grid validates each name in isolation at the 0.50 cap.  Live,
both names trade against ONE equity: their drawdowns coincide (SPY/QQQ daily
correlation ~0.9), so the joint max drawdown is deeper than either single-name
run suggests.  This script quantifies that.

Method (and its stated approximations)
--------------------------------------
Each name is backtested with the pinned ORCHESTRATOR profile on date-aligned
data; the joint per-bar return is composed as

    r_joint = r_A + r_B - y_daily

where y_daily is the cash yield.  The subtraction corrects the double-counted
idle-cash credit: each single-name run credits y*(1 - w_i), so the sum
credits y*(2 - w_A - w_B) = y + y*(1 - w_A - w_B), one y too many.

Valid because position sizing is equity-proportional (weights, not dollar
amounts), so per-name returns compose linearly.  Ignored, and in which
direction they bias the result:
  * joint circuit breakers (weekly / -10% HALT act on joint equity live) —
    composition shows the UN-protected joint path, i.e. conservative on DD;
  * daily compounding cross-terms — O(r_A * r_B) per bar, negligible.

Usage
-----
    python scripts/portfolio_check.py                # SPY+QQQ, 30y via Yahoo
    python scripts/portfolio_check.py --bars 2000    # recent span only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

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


def _row(name: str, rets: pd.Series) -> dict:
    lo, hi = sharpe_block_bootstrap_ci(rets)
    return {
        "name": name,
        "total_return": total_return(rets),
        "cagr": annualised_return(rets),
        "sharpe": sharpe_ratio(rets),
        "sharpe_ci": f"[{lo:.2f}, {hi:.2f}]",
        "max_dd": max_drawdown(rets),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Joint-book check for the live ticker set")
    ap.add_argument("--tickers", nargs=2, default=["SPY", "QQQ"])
    ap.add_argument("--bars", type=int, default=7000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="experiments_report_portfolio.md")
    args = ap.parse_args(argv)

    t_a, t_b = args.tickers
    data = {t: fetch_yahoo(t, args.bars) for t in (t_a, t_b)}
    common = data[t_a].index.intersection(data[t_b].index)[-args.bars:]
    data = {t: df.loc[common] for t, df in data.items()}
    print(f"Aligned span: {common[0].date()} … {common[-1].date()} ({len(common)} bars)")

    try:
        tbill = fetch_tbill_yields()
    except Exception as exc:
        print(f"^IRX fetch failed ({exc}) — flat fallback.")
        tbill = None

    profile = dict(getattr(config, "ORCHESTRATOR", {}))
    results = {}
    for t in (t_a, t_b):
        print(f"Running pinned profile on {t} ...")
        bt = Backtester(ticker=t, train_window=252, test_window=126,
                        random_seed=args.seed, strategy_overrides=profile,
                        cash_yield_series=tbill)
        results[t] = bt.run(data[t])

    r_a, r_b = results[t_a].returns, results[t_b].returns
    assert (r_a.index == r_b.index).all(), "OOS indices diverged — alignment bug"

    # Per-bar cash yield on the OOS index (same construction the backtester
    # uses), for the double-count correction and the 50/50 benchmark.
    yld = Backtester(cash_yield_series=tbill)._build_daily_yield(r_a.index)

    r_joint = r_a + r_b - yld

    # Benchmarks over the same OOS bars.
    bench_a = results[t_a].benchmark_returns
    bench_b = results[t_b].benchmark_returns
    bh_5050 = 0.5 * bench_a["buy_and_hold"] + 0.5 * bench_b["buy_and_hold"]
    sma_5050 = 0.5 * bench_a["sma_200"] + 0.5 * bench_b["sma_200"]

    rows = [
        _row(f"JOINT BOOK {t_a}+{t_b} (live profile)", r_joint),
        _row(f"{t_a} alone @cap {config.RISK['max_position_size']:.2f}", r_a),
        _row(f"{t_b} alone @cap {config.RISK['max_position_size']:.2f}", r_b),
        _row("bench: 50/50 buy&hold (daily rebal.)", bh_5050),
        _row("bench: 50/50 sma_200 (costless)", sma_5050),
    ]

    header = ("| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD |\n"
              "|---|---:|---:|---:|---:|---:|\n")
    lines = [
        f"| {r['name']} | {r['total_return']:+.1%} | {r['cagr']:+.1%} "
        f"| {r['sharpe']:.2f} | {r['sharpe_ci']} | {r['max_dd']:.1%} |"
        for r in rows
    ]
    meta = (
        f"Joint-book composition of the live ticker set under the pinned "
        f"profile `{profile}`, cap {config.RISK['max_position_size']:.2f} per "
        f"name.  \nData: Yahoo adjusted, {len(common)} aligned bars "
        f"({common[0].date()} … {common[-1].date()}); cash yield: "
        f"{'^IRX series' if tbill is not None else 'flat fallback'}.  \n"
        f"Method: r_joint = r_A + r_B − y (cash-credit double-count "
        f"correction); joint breakers not modelled (conservative on DD) — "
        f"see scripts/portfolio_check.py docstring.\n"
    )
    report = f"# Joint-book portfolio check\n\n{meta}\n{header}" + "\n".join(lines) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print("\n" + report + f"Report written to {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
