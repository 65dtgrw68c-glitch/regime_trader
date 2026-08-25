"""vol_target_holdout_eval.py — evaluate the book-level vol-target candidate
against the frozen holdout, per the accept/reject rule fixed BEFORE this
script ran (preregistration_2026-08-24_book_vol_target.md).

Do not change the accept/reject thresholds in this file to match a result —
that would defeat the entire point of pre-registering them first. If a
candidate is rejected, that is the answer; write it up, don't retune the
bar and rerun.

    python scripts/vol_target_holdout_eval.py               # pre-reg #1, 20%
    python scripts/vol_target_holdout_eval.py --target 0.12 # pre-reg #2, 12%

`--target` exists so a SEPARATELY pre-registered candidate can be run
against the same unchanged rule (see
preregistration_2026-08-25_tighter_vol_target.md). It is not a sweep knob:
running a range of targets here and keeping whichever one passes is exactly
the in-sample selection this whole exercise exists to prevent.

Data: Yahoo adjusted parquet under data_cache/yahoo/ (same source as
scripts/reproduce_headline.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.portfolio_backtester import PortfolioBacktester   # noqa: E402
from settings import config                                 # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data_cache" / "yahoo"
TICKERS = ["SPY", "QQQ", "GLD", "IEF", "QLD"]

# --- pre-registered candidates ---------------------------------------------
# 0.20: preregistration_2026-08-24_book_vol_target.md (default)
# 0.12: preregistration_2026-08-25_tighter_vol_target.md (--target 0.12)
DEFAULT_TARGET_VOL = 0.20
VOL_LOOKBACK = 21
# --- pre-registered accept/reject rule --------------------------------------
MAX_SHARPE_GIVEUP = 0.05
MAX_CAGR_GIVEUP_BPS = 150.0


def load(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        p = CACHE / f"{s}.parquet"
        if not p.exists():
            raise SystemExit(f"missing {p} — run scripts/reproduce_headline.py --refresh first")
        out[s] = pd.read_parquet(p)
    return out


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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--target", type=float, default=DEFAULT_TARGET_VOL,
        help="pre-registered book_vol_target to evaluate (default 0.20)",
    )
    ap.add_argument(
        "--sleeve", type=float, default=None,
        help="pre-registered sleeve weight to evaluate alongside --target "
             "(default: leave config.SLEEVES as deployed, 0.40)",
    )
    args = ap.parse_args(argv)
    target_vol = float(args.target)
    sleeve = float(args.sleeve) if args.sleeve is not None else None

    px = load(TICKERS)
    tbill_path = CACHE / "TBILL.parquet"
    tbill = pd.read_parquet(tbill_path)["y"]

    slippage_bps = float(config.BACKTEST["slippage"]) * 10_000.0
    commission_bps = float(config.BACKTEST["commission"]) * 10_000.0

    original_sleeves = {
        "core_scale": config.SLEEVES["core_scale"],
        "levered": [dict(spec) for spec in config.SLEEVES["levered"]],
    }

    def _set_sleeve(weight):
        """None = leave config.SLEEVES exactly as deployed."""
        if weight is None:
            config.SLEEVES["core_scale"] = original_sleeves["core_scale"]
            config.SLEEVES["levered"] = [dict(s) for s in original_sleeves["levered"]]
            return
        config.SLEEVES["core_scale"] = round(1.0 - weight, 6)
        config.SLEEVES["levered"] = (
            [{"ticker": "QLD", "signal": "QQQ", "weight": weight}] if weight > 0 else []
        )

    def run(book_vol_target: float, sleeve_weight=None) -> pd.Series:
        _set_sleeve(sleeve_weight)
        try:
            bt = PortfolioBacktester(
                histories=px,
                initial_capital=float(config.BACKTEST["initial_capital"]),
                transaction_cost_bps=commission_bps,
                slippage_bps=slippage_bps,
                cash_yield_series=tbill,
                book_vol_target=book_vol_target,
                vol_target_lookback=VOL_LOOKBACK,
            )
            return bt.run().returns
        finally:
            _set_sleeve(None)

    # Full history (natural start), never start_date-clipped: clipping would
    # both shift the effective simulation start (H3) and rob the vol-target's
    # own lookback of pre-holdout history to warm up on.
    # Baseline is ALWAYS the deployed configuration (sleeve 0.40, no
    # vol-target) so every pre-registration is scored against the same book.
    baseline_full = run(0.0, None)
    candidate_full = run(target_vol, sleeve)

    holdout_start = pd.Timestamp(config.HOLDOUT_START)
    baseline = baseline_full[baseline_full.index >= holdout_start]
    candidate = candidate_full[candidate_full.index >= holdout_start]

    if baseline.empty or candidate.empty:
        raise SystemExit("empty holdout slice — check HOLDOUT_START and data coverage")

    b = stats(baseline)
    c = stats(candidate)

    print("=" * 78)
    key = (round(target_vol, 4), round(sleeve, 4) if sleeve is not None else None)
    prereg = {
        (0.20, None): "preregistration_2026-08-24_book_vol_target.md",
        (0.12, None): "preregistration_2026-08-25_tighter_vol_target.md",
        (0.20, 0.30): "preregistration_2026-08-25_combination.md",
    }.get(key, "NO PRE-REGISTRATION ON FILE FOR THIS CONFIGURATION")
    print(f"vol_target_holdout_eval.py — {prereg}")
    print("=" * 78)
    sleeve_str = f"{sleeve:.0%}" if sleeve is not None else "40% (deployed, unchanged)"
    print(f"candidate      book_vol_target={target_vol:.0%}  lookback={VOL_LOOKBACK}d  "
          f"sleeve={sleeve_str}")
    print(f"holdout        {config.HOLDOUT_START} .. today "
          f"({baseline.index[0].date()} .. {baseline.index[-1].date()}, {len(baseline)} bars)")
    print(f"costs          {slippage_bps:.1f} bp slippage + {commission_bps:.1f} bp commission")
    print("-" * 78)
    print(f"{'':<14}{'CAGR':>9}{'Vol':>8}{'Sharpe':>9}{'maxDD':>9}")
    print(f"{'Baseline':<14}{b['cagr']:>9.2%}{b['vol']:>8.1%}{b['sharpe']:>9.2f}{b['max_dd']:>9.2%}")
    print(f"{'Candidate':<14}{c['cagr']:>9.2%}{c['vol']:>8.1%}{c['sharpe']:>9.2f}{c['max_dd']:>9.2%}")
    print(f"{'Delta':<14}{c['cagr']-b['cagr']:>+9.2%}{'':>8}{c['sharpe']-b['sharpe']:>+9.2f}"
          f"{abs(c['max_dd'])-abs(b['max_dd']):>+9.2%}")
    print("-" * 78)

    # --- pre-registered rule, applied mechanically ---------------------
    dd_worse = abs(c["max_dd"]) > abs(b["max_dd"])
    sharpe_giveup = b["sharpe"] - c["sharpe"]
    sharpe_too_low = sharpe_giveup > MAX_SHARPE_GIVEUP
    cagr_giveup_bps = (b["cagr"] - c["cagr"]) * 10_000.0
    cagr_too_low = cagr_giveup_bps > MAX_CAGR_GIVEUP_BPS

    reasons = []
    if dd_worse:
        reasons.append(f"maxDD magnitude worse: {abs(c['max_dd']):.2%} > {abs(b['max_dd']):.2%}")
    if sharpe_too_low:
        reasons.append(f"Sharpe {sharpe_giveup:.2f} below baseline "
                        f"(limit {MAX_SHARPE_GIVEUP:.2f})")
    if cagr_too_low:
        reasons.append(f"CAGR {cagr_giveup_bps:.0f} bp below baseline "
                        f"(limit {MAX_CAGR_GIVEUP_BPS:.0f} bp)")

    if reasons:
        print("VERDICT: REJECT")
        for r in reasons:
            print(f"  - {r}")
    else:
        print("VERDICT: ACCEPT")
        print(f"  Sharpe within {MAX_SHARPE_GIVEUP:.2f} of baseline, "
              f"maxDD not worse, CAGR give-up within {MAX_CAGR_GIVEUP_BPS:.0f} bp.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
