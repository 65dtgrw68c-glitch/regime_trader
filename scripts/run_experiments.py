"""
Strategy experiment harness — A/B-compare orchestrator configurations on
one dataset and produce a markdown comparison table.

Why this exists
---------------
Every profitability tweak (trend filter, vol targeting, rebalance
throttle …) MUST prove itself in a walk-forward backtest against the
untouched baseline before it goes live.  This script runs the full grid
in one go so the comparison is apples-to-apples: same data, same
walk-forward windows, same seed.

Data sources
------------
1. --csv (recommended in sandboxed/cloud environments): a local OHLCV file
   you downloaded yourself, e.g. from https://stooq.com/q/d/l/?s=spy.us&i=d
   or a Yahoo Finance "Historical Data" export. Stooq blocks requests from
   cloud-datacenter IPs (Codespaces included), so this is the reliable path
   there — download the CSV in a normal browser, then point here.
2. Stooq (default when --csv is omitted): free daily OHLCV via CSV download,
   no API key. Symbols use Stooq notation, e.g. "spy.us", "qqq.us", "^spx".
   Will fail with a 404 from most cloud IPs — use --csv instead in that case.
3. --synthetic: offline regime-switching random walk.  ONLY useful as a
   smoke test — parameter choices tuned on synthetic noise mean nothing
   for real markets.

Usage
-----
    python scripts/run_experiments.py                          # SPY via Stooq, full grid
    python scripts/run_experiments.py --csv spy_daily.csv       # local file, no network
    python scripts/run_experiments.py --ticker qqq.us --bars 1500
    python scripts/run_experiments.py --synthetic              # offline smoke run
    python scripts/run_experiments.py --out experiments_report.md

Overfitting warning
-------------------
Do NOT iterate on this table until the best row looks great and then ship
it.  Pick a candidate for a REASON, confirm it on a ticker/period you did
not tune on, and leave a final out-of-sample stretch untouched.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtester import Backtester                      # noqa: E402
from core.performance import (                              # noqa: E402
    annualised_return,
    max_drawdown,
    sharpe_ratio,
    total_return,
)

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"

# ---------------------------------------------------------------------------
# Experiment grid — name → (Backtester kwargs, description)
# ---------------------------------------------------------------------------
# "legacy_churn" reproduces the pre-fix behaviour (forced rebalance every
# bar, no trend filter) so the table shows what each fix is worth.
VARIANTS: list[tuple[str, dict, str]] = [
    (
        "legacy_churn",
        {
            "use_trend_filter": False,
            "strategy_overrides": {"rebalance_max_bars": 1, "drift_threshold": 0.0},
        },
        "pre-fix behaviour: rebalance every bar, no trend filter",
    ),
    (
        "gated_rebalance",
        {"use_trend_filter": False},
        "churn fix only: drift/regime triggers, 21-bar backstop",
    ),
    (
        "trend_filter",
        {},
        "churn fix + SMA-200 trend filter (current defaults)",
    ),
    (
        "trend_vol_10",
        {"strategy_overrides": {"vol_target": 0.10}},
        "defaults + 10% annualised vol target",
    ),
    (
        "trend_vol_12",
        {"strategy_overrides": {"vol_target": 0.12}},
        "defaults + 12% annualised vol target",
    ),
    (
        "trend_vol_15",
        {"strategy_overrides": {"vol_target": 0.15}},
        "defaults + 15% annualised vol target",
    ),
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def fetch_stooq(symbol: str) -> pd.DataFrame:
    """Download full daily history for `symbol` from Stooq."""
    url = STOOQ_URL.format(symbol=symbol.lower())
    print(f"Fetching {symbol} from {url} ...")
    df = pd.read_csv(url)
    df.columns = [c.lower() for c in df.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Stooq response for '{symbol}' is missing columns {missing} — "
            f"got {list(df.columns)}. Is the symbol correct (e.g. 'spy.us')?"
        )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    out = df[["open", "high", "low", "close", "volume"]].astype(float).dropna()
    if len(out) < 400:
        raise ValueError(f"Only {len(out)} usable bars for '{symbol}' — too few.")
    print(f"Got {len(out)} daily bars ({out.index[0].date()} … {out.index[-1].date()}).")
    return out


def load_csv(path: str) -> pd.DataFrame:
    """Load a local OHLCV file (Stooq or Yahoo Finance export)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {p.resolve()}")
    df = pd.read_csv(p)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"'{path}' is missing columns {missing} — got {list(df.columns)}. "
            f"Expected a Stooq or Yahoo Finance daily OHLCV export."
        )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    out = df[["open", "high", "low", "close", "volume"]].astype(float).dropna()
    if len(out) < 400:
        raise ValueError(f"Only {len(out)} usable bars in '{path}' — too few.")
    print(f"Loaded {len(out)} daily bars from {path} "
          f"({out.index[0].date()} … {out.index[-1].date()}).")
    return out


def synthetic_ohlcv(n: int = 2000, seed: int = 7) -> pd.DataFrame:
    """Regime-switching random walk (same shape the test suite uses)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-01", periods=n)
    vol = np.select(
        [np.arange(n) < n // 3, np.arange(n) < 2 * n // 3],
        [0.006, 0.018], default=0.012,
    )
    log_ret = rng.normal(0.0003, vol, n)
    close = 100 * np.exp(np.cumsum(log_ret))
    wig = rng.uniform(0.001, 0.004, n)
    return pd.DataFrame({
        "open":   close * (1 + rng.normal(0, 0.001, n)),
        "high":   close * (1 + wig),
        "low":    close * (1 - wig),
        "close":  close,
        "volume": rng.integers(1_000_000, 4_000_000, n).astype(float),
    }, index=dates)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def summarise(name: str, returns: pd.Series, trade_log: pd.DataFrame,
              commission: float, initial_capital: float) -> dict:
    """One comparison-table row."""
    if trade_log is not None and len(trade_log) and "qty" in trade_log:
        traded_notional = float((trade_log["qty"] * trade_log["fill_price"]).sum())
        n_trades = len(trade_log)
    else:
        traded_notional = 0.0
        n_trades = 0
    return {
        "variant":       name,
        "total_return":  total_return(returns),
        "cagr":          annualised_return(returns),
        "sharpe":        sharpe_ratio(returns),
        "max_dd":        max_drawdown(returns),
        "n_trades":      n_trades,
        "turnover_x":    traded_notional / initial_capital if initial_capital else 0.0,
        "est_commission": traded_notional * commission,
    }


def to_markdown(rows: list[dict], meta: str) -> str:
    header = (
        "| Variant | Total return | CAGR | Sharpe | Max DD | Trades | "
        "Turnover× | Est. commission |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['variant']} | ERROR: {r['error']} |||||||")
            continue
        lines.append(
            f"| {r['variant']} "
            f"| {r['total_return']:+.1%} "
            f"| {r['cagr']:+.1%} "
            f"| {r['sharpe']:.2f} "
            f"| {r['max_dd']:.1%} "
            f"| {r['n_trades']} "
            f"| {r['turnover_x']:.1f} "
            f"| ${r['est_commission']:,.0f} |"
        )
    return f"# Strategy experiment report\n\n{meta}\n\n{header}" + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ticker", default="spy.us",
                    help="Stooq symbol, e.g. spy.us / qqq.us (default: spy.us)")
    ap.add_argument("--bars", type=int, default=2000,
                    help="Use only the most recent N bars (default: 2000 ≈ 8y)")
    ap.add_argument("--csv", default=None,
                    help="Path to a local OHLCV CSV (Stooq or Yahoo export). "
                         "Use this in Codespaces/CI — Stooq blocks cloud IPs.")
    ap.add_argument("--synthetic", action="store_true",
                    help="Offline synthetic data (smoke test only)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-window", type=int, default=252)
    ap.add_argument("--test-window", type=int, default=126)
    ap.add_argument("--out", default="experiments_report.md",
                    help="Markdown report path (default: experiments_report.md)")
    args = ap.parse_args(argv)

    if args.csv:
        data = load_csv(args.csv).iloc[-args.bars:]
        source = f"{args.csv} (local file)"
    elif args.synthetic:
        data = synthetic_ohlcv(args.bars, seed=args.seed)
        source = f"synthetic (seed={args.seed})"
    else:
        data = fetch_stooq(args.ticker).iloc[-args.bars:]
        source = f"{args.ticker} via Stooq"

    rows: list[dict] = []
    benchmarks_row: list[dict] = []
    for name, kwargs, desc in VARIANTS:
        print(f"\n=== Running variant '{name}' ({desc}) ...")
        t0 = time.time()
        try:
            bt = Backtester(
                ticker=args.ticker.upper(),
                train_window=args.train_window,
                test_window=args.test_window,
                random_seed=args.seed,
                **kwargs,
            )
            res = bt.run(data)
            rows.append(summarise(
                name, res.returns, res.trade_log,
                bt.commission, res.initial_capital,
            ))
            if not benchmarks_row:   # identical across variants — record once
                for bname, brets in res.benchmark_returns.items():
                    benchmarks_row.append(summarise(
                        f"bench:{bname}", brets, None,
                        bt.commission, res.initial_capital,
                    ))
            print(f"    done in {time.time() - t0:.1f}s — "
                  f"sharpe={rows[-1]['sharpe']:.2f} trades={rows[-1]['n_trades']}")
        except Exception as exc:                       # keep the grid running
            print(f"    FAILED: {exc}")
            rows.append({"variant": name, "error": str(exc)})

    rows.sort(key=lambda r: r.get("sharpe", float("-inf")), reverse=True)
    meta = (
        f"Data: **{source}**, {len(data)} bars "
        f"({data.index[0].date()} … {data.index[-1].date()})  \n"
        f"Walk-forward: train={args.train_window} / test={args.test_window}, "
        f"seed={args.seed}  \n"
        f"⚠️ Benchmarks ignore costs. Do not tune until the best row looks "
        f"good — confirm any winner out-of-sample before going live."
    )
    report = to_markdown(rows + benchmarks_row, meta)

    out_path = Path(args.out)
    out_path.write_text(report, encoding="utf-8")
    print(f"\n{report}\nReport written to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
