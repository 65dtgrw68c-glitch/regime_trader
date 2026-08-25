"""sleeve_weight_scan.py — measure the sleeve-weight lever against BOTH the
frozen holdout and the dotcom reconstruction.

This is a MEASUREMENT, not a recommendation and not a pre-registered test.

Context: preregistration_2026-08-24 and _2026-08-25 established that no
book-level vol-target level does both jobs — 20% is cheap enough to pass the
project's bar but leaves the reconstructed dotcom drawdown at -41.6% (HALT
still fires), while 12% clears the HALT but gives up 298 bp of CAGR against a
150 bp limit. Vol-targeting alone does not close finding K1.

The sleeve weight is the other lever, and it attacks K1 at its actual source:
the 40% position in a 2x-QQQ ETF whose worst regime the sample cannot contain.
config.py is explicit that the 60/40 split is an OWNER DECISION taken
deliberately on 2026-08-01 and "must not be re-optimised by grid search" — so
this script deliberately does NOT pick a winner or apply an accept/reject
rule. It puts both numbers the owner needs (what each weight costs in normal
markets, what each weight does to the reconstructed tail) side by side, so
that decision can be made on data rather than on the 2007-2026 sample alone,
which structurally cannot show the sleeve's worst case.

Any weight chosen from this table would need its own pre-registration and its
own holdout run before it could be called validated.

    python scripts/sleeve_weight_scan.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.portfolio_backtester import PortfolioBacktester   # noqa: E402
from settings import config                                 # noqa: E402
from dotcom_reconstruction import (                         # noqa: E402
    ReconstructedBacktester, apply_sticky_halt, build_synthetic_qld,
    calibrate_drag, load, stats, HALT_THRESHOLD, RECON_START,
)

TICKERS = ["SPY", "QQQ", "GLD", "IEF", "QLD"]
SLEEVE_WEIGHTS = [0.40, 0.30, 0.20, 0.10, 0.00]


def _apply_sleeve_weight(weight: float) -> None:
    """Point config.SLEEVES at a given sleeve weight, core taking the rest.

    core_scale + sleeve weight = 1.0 is the existing convention (0.60 + 0.40).
    core.sleeves reads config at call time, so both the live-path weight
    construction and the backtester see this immediately.
    """
    config.SLEEVES["core_scale"] = round(1.0 - weight, 6)
    config.SLEEVES["levered"] = (
        [{"ticker": "QLD", "signal": "QQQ", "weight": weight}] if weight > 0 else []
    )


def main() -> int:
    original = {
        "core_scale": config.SLEEVES["core_scale"],
        "levered": [dict(s) for s in config.SLEEVES["levered"]],
    }

    px = {t: load(t) for t in TICKERS}
    tbill = load("TBILL")["y"]
    slippage_bps = float(config.BACKTEST["slippage"]) * 10_000.0
    commission_bps = float(config.BACKTEST["commission"]) * 10_000.0

    drag = calibrate_drag(px["QQQ"], px["QLD"])
    recon_histories = dict(px)
    recon_histories["QLD"] = build_synthetic_qld(px["QQQ"], px["QLD"], drag)

    holdout_start = pd.Timestamp(config.HOLDOUT_START)
    rows = []

    try:
        for weight in SLEEVE_WEIGHTS:
            _apply_sleeve_weight(weight)

            holdout_bt = PortfolioBacktester(
                histories=px,
                initial_capital=float(config.BACKTEST["initial_capital"]),
                transaction_cost_bps=commission_bps,
                slippage_bps=slippage_bps,
                cash_yield_series=tbill,
            )
            full = holdout_bt.run().returns
            hold = full[full.index >= holdout_start]

            recon_bt = ReconstructedBacktester(
                histories=recon_histories,
                initial_capital=float(config.BACKTEST["initial_capital"]),
                transaction_cost_bps=commission_bps,
                slippage_bps=slippage_bps,
                cash_yield_series=tbill,
            )
            recon_full = recon_bt.run().returns
            recon = recon_full[recon_full.index >= pd.Timestamp(RECON_START)]
            _, halt_date = apply_sticky_halt(recon, HALT_THRESHOLD)

            rows.append({
                "weight": weight,
                "hold": stats(hold),
                "recon": stats(recon),
                "halt_date": halt_date,
            })
    finally:
        config.SLEEVES["core_scale"] = original["core_scale"]
        config.SLEEVES["levered"] = original["levered"]

    print("=" * 92)
    print("sleeve_weight_scan.py — MEASUREMENT ONLY, no accept/reject rule applied")
    print("=" * 92)
    print(f"Holdout        {config.HOLDOUT_START} onward (frozen)")
    print(f"Reconstruction {RECON_START} onward, synthetic pre-2006 QLD "
          f"(drag {drag:.2%}/yr, fit from the real overlap)")
    print(f"Costs          {slippage_bps:.1f} bp slippage + {commission_bps:.1f} bp commission, ^IRX cash")
    print(f"HALT threshold {HALT_THRESHOLD:.0%} (settings.config.RISK)")
    print("-" * 92)
    print(f"{'sleeve':>7} | {'HOLDOUT 2021-2026':^32} | {'DOTCOM RECONSTRUCTION 2000-2026':^40}")
    print(f"{'weight':>7} | {'CAGR':>9}{'Sharpe':>8}{'maxDD':>9} | "
          f"{'CAGR':>9}{'Sharpe':>8}{'maxDD':>9}  {'HALT?':>11}")
    print("-" * 92)
    for r in rows:
        h, c = r["hold"], r["recon"]
        halt = f"YES {r['halt_date'].date()}" if r["halt_date"] is not None else "no"
        marker = "  <- deployed" if abs(r["weight"] - 0.40) < 1e-9 else ""
        print(f"{r['weight']:>6.0%}  | {h['cagr']:>9.2%}{h['sharpe']:>8.2f}{h['max_dd']:>9.2%} | "
              f"{c['cagr']:>9.2%}{c['sharpe']:>8.2f}{c['max_dd']:>9.2%}  {halt:>11}{marker}")
    print("=" * 92)
    print("Reading this table: the left half is what each weight costs in a")
    print("period the book was NOT tuned on; the right half is what it does to")
    print("the tail the 2007-2026 sample structurally cannot show (finding K1).")
    print("No weight here is validated. Choosing one requires its own")
    print("pre-registration and holdout run — see the two existing ones for the")
    print("format, and config.py's SLEEVES comment for why this is an owner")
    print("decision rather than a tuning result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
