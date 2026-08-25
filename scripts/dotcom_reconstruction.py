"""dotcom_reconstruction.py — re-validate finding K1 against the real
production pipeline, not the audit's original (unshipped) scratchpad.

analysis_report_2026-08-24_audit.md finding K1: the 40% sleeve is a 2x-QQQ
ETF (QLD) that only exists from 2006-06-21, so every published sleeve/book
number is validated on a sample that structurally cannot contain a
tech-driven bear market. The audit's own dotcom reconstruction (synthetic
pre-2006 QLD, book maxDD -52.1% vs -24.8%, own -35% HALT fires) was
produced by a scratchpad script this repo never had. This script rebuilds
that reconstruction independently, against the actual shipped
core.portfolio_backtester.PortfolioBacktester (same compute_daily_targets,
same _vol_target_scale as the deployed evaluation path), and checks whether
book_vol_target=0.20 (accepted on the 2021-2026 holdout, but only weakly
tested there — see preregistration_2026-08-24_book_vol_target.md) actually
holds up here, which is the scenario it was proposed for.

Method
------
1. Calibrate a synthetic 2x-QQQ ("QLD-like") daily return as
   2*r_qqq - drag, where `drag` (annualised) is fit from the REAL overlap
   between QQQ and QLD (2006-06-21 .. today) by matching total log return —
   not assumed from the audit's quoted -3.27%/yr, so this is checked, not
   copied.
2. Splice: synthetic QLD from QQQ's own inception (1999-03-10) through
   2006-06-20, then real QLD from 2006-06-21 on. Price levels are matched
   at the splice so there is no artificial jump.
3. GLD (real data from 2004-11-18) and IEF (real data from 2002-07-30) are
   used AS-IS, not synthesized — before their real launch dates they are
   simply not part of the eligible universe, exactly as they were not
   tradable in reality. This is the same limitation the audit's own
   reconstruction flagged ("Der Kern-Proxy vor 2002 hat keine Anleihen").
4. Drive core.portfolio_backtester.PortfolioBacktester.compute_daily_targets
   (the REAL weight-construction path) and _vol_target_scale (the REAL
   Phase-3 scaling logic) over SPY's full trading calendar back to 1996,
   via a thin subclass that replaces the intersection-based common index
   with SPY's own calendar (the only asset with data back that far) — the
   per-day eligibility check inside compute_daily_targets() already
   excludes any ticker without data on a given date, so this reproduces an
   evolving universe correctly rather than truncating the whole run to
   whichever asset launched last (see finding H3 in Phase 0 for why an
   intersection-based common index cannot do this).
5. Report calendar-year 2000, the 2000-03..2002-10 drawdown, and whether
   the sticky HALT (cb_max_drawdown_halt) fires, for book_vol_target=0.0
   (baseline) vs 0.20 (the Phase-3 candidate) — both sliced to >= 2000-01-01.

    python scripts/dotcom_reconstruction.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.portfolio_backtester import PortfolioBacktester   # noqa: E402
from settings import config                                 # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data_cache" / "yahoo"
RECON_START = "2000-01-01"
HALT_THRESHOLD = float(config.RISK["cb_max_drawdown_halt"])


def load(ticker: str) -> pd.DataFrame:
    p = CACHE / f"{ticker}.parquet"
    if not p.exists():
        raise SystemExit(f"missing {p} — run scripts/reproduce_headline.py --refresh first")
    return pd.read_parquet(p)


def calibrate_drag(qqq: pd.DataFrame, qld: pd.DataFrame) -> float:
    """Annualised daily drag such that 2*r_qqq - drag/252, compounded, best
    matches real QLD's total log return over their real overlap."""
    common = qqq.index.intersection(qld.index)
    r_qqq = qqq["close"].pct_change().reindex(common).dropna()
    r_qld = qld["close"].pct_change().reindex(common).dropna()
    common2 = r_qqq.index.intersection(r_qld.index)
    r_qqq, r_qld = r_qqq.loc[common2], r_qld.loc[common2]

    log_real = np.log1p(r_qld).sum()
    log_synth_undragged = np.log1p(2.0 * r_qqq).sum()
    n_years = len(common2) / 252.0
    return float((log_synth_undragged - log_real) / n_years)


def build_synthetic_qld(qqq: pd.DataFrame, qld_real: pd.DataFrame, drag_annual: float) -> pd.DataFrame:
    """Synthetic QLD-like OHLC from QQQ's inception through the day before
    QLD's real launch, price-matched at the splice, then spliced onto the
    real QLD series."""
    real_start = qld_real.index.min()
    pre = qqq.loc[:real_start].iloc[:-1].copy()  # up to (not including) real_start

    close, open_ = pre["close"], pre["open"]
    overnight_ret = (open_ / close.shift(1) - 1.0) * 2.0
    overnight_ret.iloc[0] = 0.0
    intraday_ret = (close / open_ - 1.0) * 2.0 - drag_annual / 252.0

    n = len(pre)
    synth_open = np.empty(n)
    synth_close = np.empty(n)
    level = 1.0
    for i in range(n):
        o = level * (1.0 + overnight_ret.iloc[i])
        c = o * (1.0 + intraday_ret.iloc[i])
        synth_open[i] = o
        synth_close[i] = c
        level = c

    # Match price level at the splice: last synthetic close ~ first real
    # close, so the join doesn't create an artificial one-day jump.
    scale = float(qld_real["close"].iloc[0]) / synth_close[-1]
    synth_open *= scale
    synth_close *= scale

    synth = pd.DataFrame(
        {
            "open": synth_open,
            "high": np.maximum(synth_open, synth_close) * 1.01,
            "low": np.minimum(synth_open, synth_close) * 0.99,
            "close": synth_close,
            "volume": 1_000_000.0,
        },
        index=pre.index,
    )
    combined = pd.concat([synth, qld_real])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


class ReconstructedBacktester(PortfolioBacktester):
    """Drives the real compute_daily_targets()/_vol_target_scale() over an
    evolving universe instead of the intersection of all five histories
    (which would truncate the whole run to GLD's 2004 launch)."""

    def _common_index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.histories["SPY"].index).sort_values()


def apply_sticky_halt(r: pd.Series, threshold: float) -> tuple[pd.Series, "pd.Timestamp | None"]:
    """Peak-to-trough HALT exactly like RiskManager: once breached, flat
    forever (no re-arm) — mirrors scripts/sleeve_check.py's apply_halt()."""
    out = []
    eq, peak = 1.0, 1.0
    halt_date = None
    for date, x in r.items():
        if halt_date is not None:
            out.append(0.0)
            continue
        eq *= 1.0 + x
        peak = max(peak, eq)
        out.append(x)
        if eq / peak - 1.0 <= -threshold:
            halt_date = date
    return pd.Series(out, index=r.index), halt_date


def stats(r: pd.Series) -> dict[str, float]:
    n = len(r)
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    return {
        "cagr": (1 + r).prod() ** (252 / n) - 1 if n else 0.0,
        "sharpe": (r.mean() / r.std() * np.sqrt(252)) if n and r.std() else 0.0,
        "max_dd": dd.min() if n else 0.0,
    }


def window_return(r: pd.Series, start: str, end: str) -> float:
    w = r.loc[start:end]
    return float((1 + w).prod() - 1) if len(w) else float("nan")


def main() -> int:
    spy, qqq, gld, ief, qld_real = (
        load("SPY"), load("QQQ"), load("GLD"), load("IEF"), load("QLD"),
    )
    tbill = load("TBILL")["y"] if (CACHE / "TBILL.parquet").exists() else None

    drag = calibrate_drag(qqq, qld_real)
    print("=" * 78)
    print("dotcom_reconstruction.py — re-validating finding K1")
    print("=" * 78)
    print(f"Calibrated synthetic-QLD drag: {drag:.2%}/yr "
          f"(audit quoted -3.27%/yr; fit here from the real QQQ/QLD overlap, "
          f"not assumed)")

    qld_ext = build_synthetic_qld(qqq, qld_real, drag)
    print(f"Synthetic QLD span:  {qld_ext.index.min().date()} .. "
          f"{qld_real.index.min().date()} (synthetic), "
          f"{qld_real.index.min().date()} .. {qld_real.index.max().date()} (real)")

    # Sanity: how well does the SAME formula track real QLD over the real
    # overlap period (this is what the drag was fit to reproduce)?
    overlap_ix = qld_real.index
    r_check = (2.0 * qqq["close"].pct_change() - drag / 252.0).reindex(overlap_ix).dropna()
    r_real_check = qld_real["close"].pct_change().dropna()
    common_check = r_check.index.intersection(r_real_check.index)
    s_check, s_real = stats(r_check.loc[common_check]), stats(r_real_check.loc[common_check])
    print(f"Overlap check (2006-06-21 .. today, formula vs real QLD): "
          f"CAGR synth {s_check['cagr']:.2%} vs real {s_real['cagr']:.2%}  |  "
          f"maxDD synth {s_check['max_dd']:.2%} vs real {s_real['max_dd']:.2%}")

    histories = {"SPY": spy, "QQQ": qqq, "GLD": gld, "IEF": ief, "QLD": qld_ext}

    results = {}
    for label, vol_target in [("baseline", 0.0), ("candidate_20pct", 0.20)]:
        bt = ReconstructedBacktester(
            histories=histories,
            initial_capital=100_000.0,
            slippage_bps=float(config.BACKTEST["slippage"]) * 10_000.0,
            transaction_cost_bps=float(config.BACKTEST["commission"]) * 10_000.0,
            cash_yield_series=tbill,
            book_vol_target=vol_target,
            vol_target_lookback=21,
        )
        full = bt.run().returns
        r = full[full.index >= pd.Timestamp(RECON_START)]
        results[label] = r

    print("-" * 78)
    print(f"Reconstructed span: {RECON_START} .. today  "
          f"(core universe grows in: SPY+QQQ from the start, +IEF from "
          f"2002-07-30, +GLD from 2004-11-18 — matches real launch dates, "
          f"not synthesized)")
    print("-" * 78)
    print(f"{'':<28}{'CAGR':>9}{'Sharpe':>9}{'maxDD':>9}   HALT fires?")
    for label in ("baseline", "candidate_20pct"):
        r = results[label]
        m = stats(r)
        r_halted, halt_date = apply_sticky_halt(r, HALT_THRESHOLD)
        m_halted = stats(r_halted)
        fire_str = f"YES {halt_date.date()}" if halt_date is not None else "no"
        print(f"{label:<28}{m['cagr']:>9.2%}{m['sharpe']:>9.2f}{m['max_dd']:>9.2%}   {fire_str}")
        if halt_date is not None:
            print(f"{'  -> post-HALT CAGR':<28}{m_halted['cagr']:>9.2%}"
                  f"{m_halted['sharpe']:>9.2f}{m_halted['max_dd']:>9.2%}")

    print("-" * 78)
    print(f"{'':<28}{'Cal.yr 2000':>13}{'2000-03..2002-10':>20}")
    for label in ("baseline", "candidate_20pct"):
        r = results[label]
        y2000 = window_return(r, "2000-01-01", "2000-12-31")
        crash = window_return(r, "2000-03-01", "2002-10-31")
        print(f"{label:<28}{y2000:>13.2%}{crash:>20.2%}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
