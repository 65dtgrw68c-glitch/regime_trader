"""reproduce_headline.py — the one canonical way to reproduce the book's
headline metrics (CAGR, Sharpe, maxDD, turnover).

Three different CAGR numbers for the SAME book (2007-04 .. today, same
data source) circulated across reports before this existed — 12.95%,
13.66%, 13.67% — with nothing pinning which cost assumption or data
snapshot produced which (2026-08-24 audit, finding H3). This script prints
every input that affects the output alongside the result: git commit,
a hash of the exact data files read, the span, the cost line and the cash
model. A headline number quoted anywhere without this preamble next to it
should not be trusted — regenerate it with this script instead.

    python scripts/reproduce_headline.py
    python scripts/reproduce_headline.py --refresh    # re-fetch Yahoo data first

Data: Yahoo adjusted parquet under data_cache/yahoo/ (see scripts/sleeve_check.py
for the fetch helper — same source, so the two stay comparable). Cost and cash
assumptions are read from settings.config.BACKTEST, not hardcoded here, so this
script and the deployed backtest path can never silently diverge.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.portfolio_backtester import PortfolioBacktester   # noqa: E402
from settings import config                                 # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE = REPO_ROOT / "data_cache" / "yahoo"
TICKERS = ["SPY", "QQQ", "GLD", "IEF", "QLD"]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        commit = out.stdout.strip()
    except Exception:
        return "unknown (not a git checkout or git unavailable)"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT,
        capture_output=True, text=True,
    ).stdout.strip()
    return commit + ("+dirty" if dirty else "")


def _data_hash(paths: list[Path]) -> str:
    """SHA-256 over the exact bytes of every parquet file this run reads.

    data_cache/ is gitignored, so nothing else fingerprints the historical
    data snapshot a given number was computed on. Two runs claiming the
    same headline number can now be checked against the same data instead
    of taken on faith.
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def load(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        p = CACHE / f"{s}.parquet"
        if not p.exists():
            raise SystemExit(f"missing {p} — run with --refresh first")
        out[s] = pd.read_parquet(p)
    return out


def refresh(symbols: list[str]) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_experiments import fetch_yahoo, fetch_tbill_yields
    CACHE.mkdir(parents=True, exist_ok=True)
    for s in symbols:
        fetch_yahoo(s, 9999).to_parquet(CACHE / f"{s}.parquet")
    fetch_tbill_yields().to_frame("y").to_parquet(CACHE / "TBILL.parquet")


def stats(r: pd.Series) -> dict[str, float]:
    n = len(r)
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    return {
        "cagr": (1 + r).prod() ** (252 / n) - 1 if n else 0.0,
        "vol": r.std() * np.sqrt(252) if n else 0.0,
        "sharpe": (r.mean() / r.std() * np.sqrt(252)) if n and r.std() else 0.0,
        "max_dd": dd.min() if n else 0.0,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                     help="re-fetch Yahoo data before running")
    ap.add_argument(
        "--book-vol-target", type=float, default=None, metavar="X",
        help="override settings.config.BOOK_VOL_TARGET for this run. "
             "Default: the DEPLOYED value, i.e. the book that actually "
             "trades. Pass 0 to reproduce the pre-2026-08-31 headline "
             "(the same book without the vol target) — the only comparison "
             "this flag exists for. It is not a sweep knob: scanning "
             "targets here and keeping the best is exactly the in-sample "
             "selection the frozen holdout exists to prevent.",
    )
    args = ap.parse_args(argv)

    if args.refresh:
        refresh(sorted(set(TICKERS)))

    px = load(TICKERS)
    tbill_path = CACHE / "TBILL.parquet"
    if not tbill_path.exists():
        raise SystemExit(f"missing {tbill_path} — run with --refresh first")
    tbill = pd.read_parquet(tbill_path)["y"]

    slippage_bps = float(config.BACKTEST["slippage"]) * 10_000.0
    commission_bps = float(config.BACKTEST["commission"]) * 10_000.0

    bt = PortfolioBacktester(
        histories=px,
        initial_capital=float(config.BACKTEST["initial_capital"]),
        transaction_cost_bps=commission_bps,
        slippage_bps=slippage_bps,
        cash_yield_series=tbill,
        book_vol_target=args.book_vol_target,   # None = the deployed value
    )
    result = bt.run()
    r = result.returns
    if r.empty:
        raise SystemExit(f"empty result: {result.metadata}")

    m = stats(r)
    data_files = [CACHE / f"{t}.parquet" for t in TICKERS] + [tbill_path]

    print("=" * 72)
    print("reproduce_headline.py — pinned inputs")
    print("=" * 72)
    print(f"commit         {_git_commit()}")
    print(f"data hash      {_data_hash(data_files)}  "
          f"(sha256[:16] of {', '.join(p.name for p in data_files)})")
    print(f"span           {r.index[0].date()} .. {r.index[-1].date()}  "
          f"({len(r)} bars)")
    print(f"costs          {slippage_bps:.1f} bp slippage + {commission_bps:.1f} bp "
          f"commission per turnover unit  (settings.config.BACKTEST)")
    print(f"cash model     ^IRX 13-week T-bill yield series "
          f"({tbill_path.name}), forward-filled, credited on idle cash only "
          f"(source={result.metadata.get('cash_yield_source')})")
    print(f"book           core_scale={result.metadata.get('core_scale')}  "
          f"sleeves={result.metadata.get('sleeves')}")
    _vt = float(result.metadata.get("book_vol_target", 0.0) or 0.0)
    print(f"vol target     {_vt:.0%} annualised over "
          f"{result.metadata.get('vol_target_lookback')} bars"
          if _vt > 0 else "vol target     off")
    print("-" * 72)
    print(f"CAGR           {m['cagr']:.2%}")
    print(f"Vol            {m['vol']:.2%}")
    print(f"Sharpe         {m['sharpe']:.2f}")
    print(f"maxDD          {m['max_dd']:.2%}")
    n_years = len(r) / 252.0
    annual_turnover = result.metadata.get("total_turnover", 0.0) / n_years if n_years else 0.0
    print(f"Turnover       {annual_turnover:.1f}  (sum|delta w| p.a., "
          f"{result.metadata.get('total_turnover', 0.0):.1f} total over {n_years:.1f}y)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
