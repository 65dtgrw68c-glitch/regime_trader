"""k1_return_ledger.py — what each K1 option actually EARNS, once the sticky
HALT is allowed to do what it does.

analysis_report_2026-08-25_k1_options.md prices every option in holdout CAGR
and reconstruction maxDD.  Those two columns cannot answer a return question,
because they are measured on different quantities:

  * the holdout CAGR column is realised return over 5.6 years that contain no
    dotcom-shaped regime;
  * the reconstruction column reports a DRAWDOWN, and reports it on the
    UN-halted return stream — the path the book would have taken if the -35%
    circuit breaker were not there.

But the breaker IS there, and it is sticky: `RiskManager` never re-arms it, so
in the reconstruction the deployed book does not "draw down 52% and recover",
it goes flat on 2000-07-28 and stays flat.  A return comparison that ignores
that is comparing a path the deployed configuration would never have been
allowed to travel.

This script adds the missing column: post-HALT realised CAGR and terminal
wealth over 2000-2026, for exactly the eight configurations already measured in
the memo.  It introduces NO new candidates.  That restriction is the point —
config.py forbids re-optimising the sleeve split by grid search, and scanning
vol-target levels until one wins is that same search wearing a different hat.
Every row below already exists in the memo; only the metric is new.

    python scripts/k1_return_ledger.py

Data: Yahoo adjusted parquet under data_cache/yahoo/ (same source as
scripts/reproduce_headline.py and scripts/sleeve_weight_scan.py).
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
VOL_LOOKBACK = 21

# (label, sleeve_weight, book_vol_target) — the memo's eight rows, unchanged.
OPTIONS = [
    ("Deployed (40%, no VT)", 0.40, 0.00),
    ("Vol-target 20%",        0.40, 0.20),
    ("Sleeve 30%",            0.30, 0.00),
    ("Sleeve 30% + VT 20%",   0.30, 0.20),
    ("Vol-target 12%",        0.40, 0.12),
    ("Sleeve 20%",            0.20, 0.00),
    ("Sleeve 10%",            0.10, 0.00),
    ("Sleeve 0% (core only)", 0.00, 0.00),
]


def _apply_sleeve_weight(weight: float) -> None:
    """Same convention as scripts/sleeve_weight_scan.py: core_scale + sleeve = 1."""
    config.SLEEVES["core_scale"] = round(1.0 - weight, 6)
    config.SLEEVES["levered"] = (
        [{"ticker": "QLD", "signal": "QQQ", "weight": weight}] if weight > 0 else []
    )


def terminal_multiple(r: pd.Series) -> float:
    return float((1.0 + r).prod())


def main() -> int:
    original = {
        "core_scale": config.SLEEVES["core_scale"],
        "levered": [dict(s) for s in config.SLEEVES["levered"]],
    }

    px = {t: load(t) for t in TICKERS}
    tbill = load("TBILL")["y"]
    slippage_bps = float(config.BACKTEST["slippage"]) * 10_000.0
    commission_bps = float(config.BACKTEST["commission"]) * 10_000.0
    capital = float(config.BACKTEST["initial_capital"])

    drag = calibrate_drag(px["QQQ"], px["QLD"])
    recon_histories = dict(px)
    recon_histories["QLD"] = build_synthetic_qld(px["QQQ"], px["QLD"], drag)

    holdout_start = pd.Timestamp(config.HOLDOUT_START)
    rows = []

    try:
        for label, sleeve, vt in OPTIONS:
            _apply_sleeve_weight(sleeve)

            hold_full = PortfolioBacktester(
                histories=px, initial_capital=capital,
                transaction_cost_bps=commission_bps, slippage_bps=slippage_bps,
                cash_yield_series=tbill,
                book_vol_target=vt, vol_target_lookback=VOL_LOOKBACK,
            ).run().returns
            hold = hold_full[hold_full.index >= holdout_start]

            recon_full = ReconstructedBacktester(
                histories=recon_histories, initial_capital=capital,
                transaction_cost_bps=commission_bps, slippage_bps=slippage_bps,
                cash_yield_series=tbill,
                book_vol_target=vt, vol_target_lookback=VOL_LOOKBACK,
            ).run().returns
            recon = recon_full[recon_full.index >= pd.Timestamp(RECON_START)]
            recon_halted, halt_date = apply_sticky_halt(recon, HALT_THRESHOLD)

            rows.append({
                "label": label, "sleeve": sleeve, "vt": vt,
                "hold": stats(hold),
                "recon_raw": stats(recon),
                "recon_halted": stats(recon_halted),
                "mult": terminal_multiple(recon_halted),
                "halt_date": halt_date,
            })
            print(f"  ... {label} done", flush=True)
    finally:
        config.SLEEVES["core_scale"] = original["core_scale"]
        config.SLEEVES["levered"] = original["levered"]

    print()
    print("=" * 104)
    print("k1_return_ledger.py — the return column the K1 memo is missing")
    print("=" * 104)
    print(f"Holdout        {config.HOLDOUT_START} onward (frozen), real QLD only")
    print(f"Reconstruction {RECON_START} onward, synthetic pre-2006 QLD "
          f"(drag {drag:.2%}/yr, fit from the real overlap)")
    print(f"HALT           {HALT_THRESHOLD:.0%} peak-to-trough, sticky (never re-arms), "
          f"applied to the reconstruction")
    print(f"Costs          {slippage_bps:.1f} bp slippage + {commission_bps:.1f} bp commission, ^IRX cash")
    print("-" * 104)
    print(f"{'option':<24}| {'HOLDOUT 2021-26':^25}| {'RECON 2000-26, HALT APPLIED':^33}| {'HALT?':^14}")
    print(f"{'':<24}| {'CAGR':>8}{'Sharpe':>8}{'maxDD':>9}| {'CAGR':>8}{'maxDD':>9}{'x capital':>14}|")
    print("-" * 104)
    for r in rows:
        h, k = r["hold"], r["recon_halted"]
        halt = f"{r['halt_date'].date()}" if r["halt_date"] is not None else "never"
        print(f"{r['label']:<24}| {h['cagr']:>8.2%}{h['sharpe']:>8.2f}{h['max_dd']:>9.2%}| "
              f"{k['cagr']:>8.2%}{k['max_dd']:>9.2%}{r['mult']:>13.2f}x| {halt:^14}")
    print("-" * 104)

    base = rows[0]
    print()
    print("Delta vs deployed (the trade every option actually asks for):")
    print(f"{'option':<24}{'holdout CAGR':>16}{'recon CAGR':>16}{'recon terminal':>18}")
    for r in rows[1:]:
        d_h = (r["hold"]["cagr"] - base["hold"]["cagr"]) * 10_000.0
        d_r = (r["recon_halted"]["cagr"] - base["recon_halted"]["cagr"]) * 10_000.0
        ratio = r["mult"] / base["mult"] if base["mult"] else float("nan")
        print(f"{r['label']:<24}{d_h:>+15.0f}bp{d_r:>+15.0f}bp{ratio:>17.2f}x")
    print("=" * 104)
    print("Read this as: the left block is what an option costs in a regime the")
    print("book was never at risk in; the right block is what it earns in the")
    print("regime the 2007-2026 sample structurally cannot contain. An option")
    print("that halts is not 'down 52% and recovering' — it is flat from the")
    print("halt date to today, which is what the terminal multiple prices in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
