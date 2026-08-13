"""Pre-registered test: does a realized-vol spike gate on the QLD sleeve help?

PRE-REGISTERED 2026-08-13 (owner sign-off), before this script was run:

  Rule (fixed, not tuned here):
    Zero the QLD sleeve when QQQ's 10-day realized vol > 2.0x its own
    trailing 252-day median. This is one of two variants an earlier,
    UNREGISTERED 6-point grid search had already scored against exactly two
    known crises (2020, 2022) -- that search is why this script does not
    re-sweep parameters. Locking in one candidate and testing it against
    FRESH data is the whole point.

  Pass bar (adopt only if ALL hold), on data NOT used to pick the rule:
    - Full-period Sharpe (blended 60/40 book, real data ~2004-11..2026,
      the GLD-inception-limited window) not worse than baseline by more
      than 0.02.
    - CAGR not worse than baseline by more than 0.5pp.
    - maxDD equal-or-better than baseline in EVERY one of: dotcom
      (2000-03..2002-10), GFC (2007-10..2009-03), 2020-02..04, 2022.

  Data-availability caveat, agreed before running: QLD (real product)
  starts 2006-06-21, so the dotcom window cannot use real QLD data. A
  synthetic 2x-QQQ daily-reset proxy stands in for QLD before that date
  (captures compounding decay; ignores QLD's ~0.95%/yr expense ratio --
  immaterial next to crisis-era swings). The dotcom check is therefore
  SLEEVE-ONLY (not the blended book, since GLD/IEF also postdate 2000).

  Any condition failing -> REJECT. No iteration on the threshold after
  seeing results.

    python scripts/vol_gate_check.py [--refresh]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sleeve_check import (  # noqa: E402
    CACHE, CORE, SLIP_BPS, core_book_returns, core_scale_cfg, load, refresh,
    sleeve_weight_cfg, stats,
)

GATE_SHORT = 10
GATE_LONG = 252
GATE_MULT = 2.0

CRISES = {
    "dotcom (sleeve-only, synthetic pre-2006)": ("2000-03-01", "2002-10-15"),
    "GFC":     ("2007-10-01", "2009-03-09"),
    "2020":    ("2020-02-01", "2020-04-30"),
    "2022":    ("2022-01-01", "2022-12-31"),
}

SHARPE_TOL = 0.02
CAGR_TOL = 0.005


def vol_gate_mask(close: pd.Series) -> pd.Series:
    """True where the gate is ACTIVE (sleeve must be zero)."""
    log_ret = np.log(close).diff()
    vol_short = log_ret.rolling(GATE_SHORT).std(ddof=1)
    vol_long_med = vol_short.rolling(GATE_LONG).median()
    return (vol_short > GATE_MULT * vol_long_med).fillna(False)


def sleeve_returns(px: dict, tbill: pd.Series, signal: str, trade: str,
                    gated: bool) -> pd.Series:
    """Same construction as sleeve_check.levered_sleeve_returns, with an
    optional vol-gate ANDed into the trend eligibility signal."""
    c = px[signal]["close"]
    eligible = (c > c.rolling(200).mean())
    if gated:
        eligible = eligible & (~vol_gate_mask(c))
    eligible = eligible.astype(float)

    tr = px[trade]
    ix = tr.index.intersection(c.index)[200:]
    pos_ex = eligible.reindex(ix).shift(1).fillna(0.0)
    pos_ov = eligible.reindex(ix).shift(2).fillna(0.0)
    r_ov = (tr["open"] / tr["close"].shift(1) - 1.0).reindex(ix).fillna(0.0)
    r_in = (tr["close"] / tr["open"] - 1.0).reindex(ix).fillna(0.0)
    rfd = tbill.reindex(ix, method="ffill").fillna(0.02) / 252.0
    turn = pos_ex.diff().abs().fillna(0.0)
    return (pos_ov * r_ov + pos_ex * r_in + (1.0 - pos_ex) * rfd
            - turn * SLIP_BPS / 10_000.0)


def synthetic_2x_qqq(qqq: pd.DataFrame) -> pd.DataFrame:
    """Daily-reset 2x-QQQ proxy: captures compounding/decay correctly,
    ignores the real product's expense ratio and any borrow cost."""
    close = (1.0 + 2.0 * qqq["close"].pct_change().fillna(0.0)).cumprod() * 100.0
    open_ret = 2.0 * (qqq["open"] / qqq["close"].shift(1) - 1.0).fillna(0.0)
    open_ = close.shift(1).fillna(100.0) * (1.0 + open_ret)
    return pd.DataFrame({"open": open_, "close": close}, index=qqq.index)


def extended_sleeve_returns(px: dict, tbill: pd.Series, gated: bool) -> pd.Series:
    """Sleeve-only return series stretched back to QQQ's 1999 start via the
    synthetic proxy, spliced to real QLD from its real 2006-06-21 inception."""
    qld_start = px["QLD"].index.min()
    synth = synthetic_2x_qqq(px["QQQ"])
    trade_df = pd.concat([synth.loc[synth.index < qld_start],
                           px["QLD"].loc[px["QLD"].index >= qld_start]])
    px_ext = dict(px)
    px_ext["QLD_EXT"] = trade_df
    return sleeve_returns(px_ext, tbill, "QQQ", "QLD_EXT", gated=gated)


def dd_series(r: pd.Series) -> pd.Series:
    eq = (1.0 + r).cumprod()
    return eq / eq.cummax() - 1.0


def window_dd(dd: pd.Series, start: str, end: str) -> float:
    seg = dd.loc[start:end]
    return float(seg.min()) if len(seg) else float("nan")


def blended_book(px: dict, tbill: pd.Series, gated: bool) -> pd.Series:
    r_core, _ = core_book_returns(px, tbill)
    r_sleeve = sleeve_returns(px, tbill, "QQQ", "QLD", gated=gated)
    ix = r_core.index.intersection(r_sleeve.index)
    return core_scale_cfg() * r_core.loc[ix] + sleeve_weight_cfg() * r_sleeve.loc[ix]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)

    syms = sorted(set(CORE + ["QQQ", "QLD"]))
    if a.refresh:
        refresh(syms)
    px = load(syms)
    tbill = pd.read_parquet(CACHE / "TBILL.parquet")["y"]

    print("=" * 78)
    print("PRE-REGISTERED VOL-GATE TEST — rule fixed before this run, see docstring")
    print(f"Gate: {GATE_SHORT}d realized vol > {GATE_MULT}x {GATE_LONG}d median")
    print("=" * 78)

    # --- full-period blended book (real data only) --------------------------
    book_base = blended_book(px, tbill, gated=False)
    book_gate = blended_book(px, tbill, gated=True)
    ix = book_base.index.intersection(book_gate.index)
    book_base, book_gate = book_base.loc[ix], book_gate.loc[ix]

    print(f"\nBlended 60/40 book, real data, {ix[0].date()} .. {ix[-1].date()} "
          f"({len(ix)} bars)")
    print(f"{'':<10}{'CAGR':>9}{'Vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    c_base, _, s_base, d_base = stats(book_base)
    c_gate, _, s_gate, d_gate = stats(book_gate)
    print(f"{'baseline':<10}{c_base:>9.2%}{'':>8}{s_base:>8.2f}{d_base:>9.2%}")
    print(f"{'gated':<10}{c_gate:>9.2%}{'':>8}{s_gate:>8.2f}{d_gate:>9.2%}")

    sharpe_ok = s_gate >= s_base - SHARPE_TOL
    cagr_ok = c_gate >= c_base - CAGR_TOL
    print(f"\nSharpe delta {s_gate - s_base:+.3f} (bar: >= -{SHARPE_TOL}) "
          f"-> {'OK' if sharpe_ok else 'FAIL'}")
    print(f"CAGR delta   {c_gate - c_base:+.2%} (bar: >= -{CAGR_TOL:.1%}) "
          f"-> {'OK' if cagr_ok else 'FAIL'}")

    # --- crisis-window maxDD -------------------------------------------------
    dd_base_blend = dd_series(book_base)
    dd_gate_blend = dd_series(book_gate)

    r_ext_base = extended_sleeve_returns(px, tbill, gated=False)
    r_ext_gate = extended_sleeve_returns(px, tbill, gated=True)
    dd_base_sleeve = dd_series(r_ext_base)
    dd_gate_sleeve = dd_series(r_ext_gate)

    print(f"\n{'Crisis window':<42}{'baseline':>10}{'gated':>10}{'':>8}")
    crisis_ok = {}
    for label, (start, end) in CRISES.items():
        if label.startswith("dotcom"):
            b = window_dd(dd_base_sleeve, start, end)
            g = window_dd(dd_gate_sleeve, start, end)
        else:
            b = window_dd(dd_base_blend, start, end)
            g = window_dd(dd_gate_blend, start, end)
        ok = (not np.isnan(b)) and (not np.isnan(g)) and (g >= b - 1e-9)
        crisis_ok[label] = ok
        print(f"{label:<42}{b:>10.2%}{g:>10.2%}{'OK' if ok else 'FAIL':>8}")

    # --- verdict --------------------------------------------------------------
    all_ok = sharpe_ok and cagr_ok and all(crisis_ok.values())
    print("\n" + "=" * 78)
    print(f"VERDICT: {'ADOPT' if all_ok else 'REJECT'}")
    if not all_ok:
        failed = [k for k, v in {"Sharpe": sharpe_ok, "CAGR": cagr_ok, **crisis_ok}.items()
                  if not v]
        print("Failed condition(s): " + ", ".join(failed))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
