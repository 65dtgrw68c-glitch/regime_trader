"""
IEX vs Yahoo close divergence — H2 measurement (2026-08-17 security review).

The live decision path fetches daily bars from Alpaca's free IEX feed
(data/market_data.py, feed=DataFeed.IEX) — IEX is ~2-3% of consolidated
volume. Every validation path (scripts/sleeve_check.py,
core/portfolio_backtester.py via scripts/run_experiments.py's default
Alpaca-IEX source, or its --yahoo/--csv alternates) is checked against
Yahoo's consolidated adjusted close instead. The whole strategy reduces to
one close > SMA200 comparison, so a systematically different close series
is a signal-integrity question, not a cosmetic one.

This script MEASURES the divergence between the two sources and reports
how often it actually flips the SMA200 trend flag the strategy trades on.
It changes nothing — per the review's own recommendation, decide whether to
act (pay for the SIP feed, tolerance-band the signal, or accept it) only
after seeing real numbers, not before.

Usage:
    python scripts/iex_vs_yahoo_check.py
    python scripts/iex_vs_yahoo_check.py --tickers SPY,QQQ --bars 2500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_experiments import fetch_alpaca, fetch_yahoo   # noqa: E402
from core.regime_strategies import TREND_FILTER_WINDOW          # noqa: E402

DEFAULT_TICKERS = ["SPY", "QQQ", "GLD", "IEF", "QLD"]


def _trend_flags(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(flag, distance_bps) per bar, matching
    core.regime_strategies.is_trend_confirmed exactly (strictly causal,
    trailing window inclusive of the current bar). distance_bps is how far
    the close sits from its own SMA200, signed — near zero means a
    razor's-edge day where ~1bp of source noise is enough to flip the flag
    on its own, regardless of which feed is "more correct". NaN dropped
    (warm-up)."""
    close = close.sort_index()
    sma = close.rolling(TREND_FILTER_WINDOW).mean()
    valid = sma.notna()
    flags = (close > sma)[valid]
    dist_bps = ((close - sma) / sma * 1e4)[valid]
    return flags, dist_bps


def _normalize_to_date(df: pd.DataFrame) -> pd.DataFrame:
    """Alpaca's daily-bar timestamp carries a 04:00:00 UTC time-of-day
    component (session start); Yahoo's is already midnight-normalized. Same
    calendar date, different time-of-day — an exact-timestamp join would
    silently intersect to nothing without this."""
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index).normalize()
    return out[~out.index.duplicated(keep="last")]


def compare(ticker: str, bars: int, yahoo_range: str) -> dict:
    iex = _normalize_to_date(fetch_alpaca(ticker, bars))
    yahoo = _normalize_to_date(fetch_yahoo(ticker, bars, range_str=yahoo_range))

    common = iex.index.intersection(yahoo.index)
    if len(common) < TREND_FILTER_WINDOW + 20:
        raise ValueError(
            f"{ticker}: only {len(common)} overlapping days — too few to "
            f"measure the SMA{TREND_FILTER_WINDOW} gate."
        )

    iex_c = iex.loc[common, "close"].sort_index()
    yahoo_c = yahoo.loc[common, "close"].sort_index()
    bps = (iex_c - yahoo_c) / yahoo_c * 1e4

    # Trend flags computed from each source's OWN full causal history (not
    # just the overlap window) — matches how the live bot actually warms up
    # on TREND_FILTER_WINDOW bars of its own feed, then restricted to the
    # dates both sources can evaluate.
    iex_flags, iex_dist = _trend_flags(iex["close"])
    yahoo_flags, yahoo_dist = _trend_flags(yahoo["close"])
    flag_common = iex_flags.index.intersection(yahoo_flags.index).intersection(common)
    disagree = iex_flags.loc[flag_common] != yahoo_flags.loc[flag_common]
    disagree_dates = disagree[disagree].index

    return {
        "ticker": ticker,
        "overlap_days": len(common),
        "overlap_start": str(common.min().date()),
        "overlap_end": str(common.max().date()),
        "mean_abs_bps": float(bps.abs().mean()),
        "median_abs_bps": float(bps.abs().median()),
        "max_abs_bps": float(bps.abs().max()),
        "max_abs_date": str(bps.abs().idxmax().date()),
        "pct_days_gt_5bps": float((bps.abs() > 5).mean() * 100),
        "pct_days_gt_20bps": float((bps.abs() > 20).mean() * 100),
        "trend_flag_days_compared": int(len(flag_common)),
        "trend_flag_disagreements": int(disagree.sum()),
        # Signed distance-to-SMA200 (bps) on each disagreement day, from
        # EACH source's own perspective — near zero on both sides means a
        # razor's-edge day where ~1bp of ordinary cross-feed noise is
        # naturally enough to flip the sign, not evidence of a bad feed.
        "trend_flag_disagreement_detail": [
            {
                "date": str(d.date()),
                "iex_dist_bps": round(float(iex_dist.loc[d]), 2),
                "yahoo_dist_bps": round(float(yahoo_dist.loc[d]), 2),
            }
            for d in disagree_dates
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--bars", type=int, default=2500,
                        help="how many IEX bars to request (Alpaca free IEX "
                             "history goes back to ~2018 for liquid ETFs)")
    parser.add_argument("--yahoo-range", default="10y")
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    results = []
    for ticker in tickers:
        print(f"\n=== {ticker} ===")
        try:
            r = compare(ticker, args.bars, args.yahoo_range)
        except Exception as exc:
            print(f"  SKIPPED: {exc}")
            continue
        results.append(r)
        print(f"  overlap: {r['overlap_days']}d ({r['overlap_start']} .. {r['overlap_end']})")
        print(f"  |close diff|  mean={r['mean_abs_bps']:.2f}bp  median={r['median_abs_bps']:.2f}bp  "
              f"max={r['max_abs_bps']:.2f}bp (on {r['max_abs_date']})")
        print(f"  days >5bp: {r['pct_days_gt_5bps']:.1f}%   days >20bp: {r['pct_days_gt_20bps']:.1f}%")
        print(f"  SMA{TREND_FILTER_WINDOW} trend-flag disagreements: "
              f"{r['trend_flag_disagreements']}/{r['trend_flag_days_compared']} days")
        for d in r["trend_flag_disagreement_detail"]:
            print(f"    {d['date']}: distance-to-SMA200  IEX={d['iex_dist_bps']:+.2f}bp  "
                  f"Yahoo={d['yahoo_dist_bps']:+.2f}bp")

    if results:
        print("\n=== Summary ===")
        df = pd.DataFrame(results).set_index("ticker")
        print(df[["overlap_days", "mean_abs_bps", "max_abs_bps",
                   "trend_flag_disagreements", "trend_flag_days_compared"]].to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
