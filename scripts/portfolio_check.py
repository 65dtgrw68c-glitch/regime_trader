"""
Portfolio check — evaluate a multi-asset book as a JOINT portfolio instead of
isolated single-name backtests.

Why this exists
---------------
The experiment grid validates each name in isolation at the RISK cap.  Live,
all names trade against ONE equity: their drawdowns may coincide, so the joint
max drawdown can be deeper than single-name runs suggest.  This script
quantifies the joint behavior.

Method (and its stated approximations)
--------------------------------------
Each name is backtested with the pinned ORCHESTRATOR profile on date-aligned
data; the joint per-bar return is composed as

    r_joint = Σ r_i - y_daily * (len(tickers) - 1)

where y_daily is the cash yield.  The subtraction corrects the double-counted
idle-cash credit: each single-name run credits y*(1 - w_i), so the sum
credits y*(Σ(1 - w_i)) = len(tickers) * y - Σw_i, which is one y too many
per name.

Valid because position sizing is equity-proportional (weights, not dollar
amounts), so per-name returns compose linearly.  Ignored, and in which
direction they bias the result:
  * joint circuit breakers (weekly / -10% HALT act on joint equity live) —
    composition shows the UN-protected joint path, i.e. conservative on DD;
  * daily compounding cross-terms — O(Π r_i) per bar, negligible.

Usage
-----
    python scripts/portfolio_check.py                       # SPY+QQQ, 30y via Yahoo
    python scripts/portfolio_check.py --tickers SPY GLD IEF # 3-asset portfolio
    python scripts/portfolio_check.py --bars 2000           # recent span only
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
    ap = argparse.ArgumentParser(description="Joint-book check for multiple assets")
    ap.add_argument("--tickers", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--bars", type=int, default=7000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="experiments_report_portfolio.md")
    args = ap.parse_args(argv)

    tickers = list(args.tickers)
    if len(tickers) < 1:
        print("ERROR: must specify at least one ticker")
        return 1

    data = {t: fetch_yahoo(t, args.bars) for t in tickers}
    # Align all tickers to a common index
    common = data[tickers[0]].index
    for t in tickers[1:]:
        common = common.intersection(data[t].index)
    common = common[-args.bars:]
    
    if len(common) < 100:
        print(f"ERROR: insufficient common data ({len(common)} bars)")
        return 1

    data = {t: df.loc[common] for t, df in data.items()}
    print(f"Aligned span: {common[0].date()} … {common[-1].date()} ({len(common)} bars)")
    print(f"Tickers: {', '.join(tickers)}")

    try:
        tbill = fetch_tbill_yields()
    except Exception as exc:
        print(f"^IRX fetch failed ({exc}) — flat fallback.")
        tbill = None

    profile = dict(getattr(config, "ORCHESTRATOR", {}))
    results = {}
    for t in tickers:
        print(f"Running pinned profile on {t} ...")
        bt = Backtester(ticker=t, train_window=252, test_window=126,
                        random_seed=args.seed, strategy_overrides=profile,
                        cash_yield_series=tbill)
        results[t] = bt.run(data[t])

    # Verify all returns are aligned
    base_idx = results[tickers[0]].returns.index
    for t in tickers[1:]:
        if not (results[t].returns.index == base_idx).all():
            print(f"ERROR: indices diverged for {t} — alignment bug")
            return 1

    # Per-bar cash yield on the OOS index (same construction the backtester
    # uses), for the double-count correction.
    yld = Backtester(cash_yield_series=tbill)._build_daily_yield(base_idx)

    # Compose the joint return: sum individual returns, subtract the extra
    # cash yield that would be counted len(tickers) times instead of once.
    r_joint = sum(results[t].returns for t in tickers) - yld * (len(tickers) - 1)

    # Benchmarks over the same OOS bars
    bench_5050 = None
    bench_sma = None
    if len(tickers) == 2:
        # For 2 tickers, show 50/50 benchmarks
        bench_5050 = (
            0.5 * results[tickers[0]].benchmark_returns["buy_and_hold"]
            + 0.5 * results[tickers[1]].benchmark_returns["buy_and_hold"]
        )
        bench_sma = (
            0.5 * results[tickers[0]].benchmark_returns["sma_200"]
            + 0.5 * results[tickers[1]].benchmark_returns["sma_200"]
        )
    else:
        # For N tickers, show equal-weight benchmarks
        n = len(tickers)
        bench_5050 = sum(results[t].benchmark_returns["buy_and_hold"] for t in tickers) / n
        bench_sma = sum(results[t].benchmark_returns["sma_200"] for t in tickers) / n

    rows = [
        _row(f"JOINT BOOK {'+'.join(tickers)} (live profile)", r_joint),
    ]
    for t in tickers:
        rows.append(_row(
            f"{t} alone @cap {config.RISK['max_position_size']:.2f}",
            results[t].returns,
        ))
    rows.append(_row("bench: equal-weight buy&hold (daily rebal.)", bench_5050))
    rows.append(_row("bench: equal-weight sma_200 (costless)", bench_sma))

    header = ("| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD |\n"
              "|---|---:|---:|---:|---:|---:|\n")
    lines = [
        f"| {r['name']} | {r['total_return']:+.1%} | {r['cagr']:+.1%} "
        f"| {r['sharpe']:.2f} | {r['sharpe_ci']} | {r['max_dd']:.1%} |"
        for r in rows
    ]
    meta = (
        f"Joint-book composition of {', '.join(tickers)} under the pinned "
        f"profile `{profile}`, cap {config.RISK['max_position_size']:.2f} per "
        f"name.  \nData: Yahoo adjusted, {len(common)} aligned bars "
        f"({common[0].date()} … {common[-1].date()}); cash yield: "
        f"{'^IRX series' if tbill is not None else 'flat fallback'}.  \n"
        f"Method: r_joint = Σ r_i − y*(n−1) (cash-credit corrected); "
        f"joint breakers not modelled (conservative on DD) — "
        f"see scripts/portfolio_check.py docstring.\n"
    )
    report = f"# Joint-book portfolio check\n\n{meta}\n{header}" + "\n".join(lines) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print("\n" + report + f"Report written to {Path(args.out).resolve()}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
