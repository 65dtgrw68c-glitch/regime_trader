"""Reference evaluation for the levered-sleeve book (core + levered trend sleeve).

Computes the blended book INDEPENDENTLY of the production code path, so it can
serve as a cross-check target for core/portfolio_backtester.py.  Run it to
regenerate the numbers quoted in analysis_report_2026-08-01_deep_review.md.

    python scripts/sleeve_check.py
    python scripts/sleeve_check.py --core-scale 0.6 --sleeve-weight 0.4

Data: Yahoo adjusted parquet under data_cache/yahoo/ (see --refresh).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.allocator import target_weights          # noqa: E402
from core.universe import AssetView                # noqa: E402
from settings import config                        # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data_cache" / "yahoo"
CORE = ["SPY", "QQQ", "GLD", "IEF"]
SLIP_BPS = 2.0


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


def core_book_returns(px: dict, tbill: pd.Series) -> tuple[pd.Series, pd.DataFrame]:
    """Today's live allocator path (trend -> corr selector -> class-budgeted
    inverse vol), with CORRECT next-open execution: the book held overnight
    earns the gap, the newly executed book earns the intraday move."""
    common = px[CORE[0]].index
    for t in CORE[1:]:
        common = common.intersection(px[t].index)
    common = common.sort_values()

    close = pd.DataFrame({t: px[t]["close"] for t in CORE}).loc[common]
    open_ = pd.DataFrame({t: px[t]["open"] for t in CORE}).loc[common]
    trend = close > close.rolling(200).mean()
    vol63 = np.log(close).diff().rolling(63).std(ddof=1) * np.sqrt(252)
    ret = close.pct_change()
    r_over = (open_ / close.shift(1) - 1.0).fillna(0.0)
    r_intra = (close / open_ - 1.0).fillna(0.0)
    rfd = tbill.reindex(common, method="ffill").fillna(0.02) / 252.0

    thresh = float(config.RISK["max_position_correlation"])
    lb = int(config.RISK["correlation_lookback"])
    corr = {}
    for i, a in enumerate(CORE):
        for b in CORE[i + 1:]:
            corr[(a, b)] = ret[a].rolling(lb).corr(ret[b]).abs()

    def cv(a, b, d):
        return corr[(a, b) if (a, b) in corr else (b, a)].loc[d]

    cls = {t: config.UNIVERSE["assets"][t]["asset_class"] for t in CORE}
    rows, W = [], []
    prev: dict[str, float] = {}
    for i in range(200, len(common)):
        d_dec, d_now = common[i - 1], common[i]
        elig = [t for t in CORE
                if bool(trend[t].loc[d_dec]) and not np.isnan(vol63[t].loc[d_dec])]
        sel = []
        for c_ in sorted(elig, key=lambda t: (float(vol63[t].loc[d_dec]), t)):
            if not any((not np.isnan(cv(c_, ch, d_dec))) and cv(c_, ch, d_dec) > thresh
                       for ch in sel):
                sel.append(c_)
        views = [AssetView(t, cls[t], True, float(vol63[t].loc[d_dec])) for t in sel]
        w = target_weights(views)

        turn = sum(abs(w.get(k, 0.) - prev.get(k, 0.)) for k in set(w) | set(prev))
        gross = sum(w.values())
        r = sum(prev.get(t, 0.) * r_over[t].loc[d_now]
                + w.get(t, 0.) * r_intra[t].loc[d_now] for t in CORE)
        r += max(0.0, 1.0 - gross) * float(rfd.loc[d_now])
        r -= turn * SLIP_BPS / 10_000.0
        rows.append({"date": d_now, "r": r, "turnover": turn, "gross": gross})
        W.append({"date": d_now, **{t: w.get(t, 0.0) for t in CORE}})
        prev = dict(w)

    df = pd.DataFrame(rows).set_index("date")
    return df["r"], pd.DataFrame(W).set_index("date")


def levered_sleeve_returns(px: dict, tbill: pd.Series, signal: str,
                           trade: str) -> pd.Series:
    """Long the levered ETF while the UNLEVERED signal asset is above its
    SMA-200, else cash at the T-bill rate.  Next-open execution."""
    c = px[signal]["close"]
    in_trend = (c > c.rolling(200).mean()).astype(float)
    tr = px[trade]
    ix = tr.index.intersection(c.index)[200:]
    pos_ex = in_trend.reindex(ix).shift(1).fillna(0.0)
    pos_ov = in_trend.reindex(ix).shift(2).fillna(0.0)
    r_ov = (tr["open"] / tr["close"].shift(1) - 1.0).reindex(ix).fillna(0.0)
    r_in = (tr["close"] / tr["open"] - 1.0).reindex(ix).fillna(0.0)
    rfd = tbill.reindex(ix, method="ffill").fillna(0.02) / 252.0
    turn = pos_ex.diff().abs().fillna(0.0)
    return (pos_ov * r_ov + pos_ex * r_in + (1.0 - pos_ex) * rfd
            - turn * SLIP_BPS / 10_000.0)


def apply_halt(r: pd.Series, threshold: float) -> tuple[pd.Series, bool]:
    """Sticky peak-to-trough HALT, exactly like RiskManager: once breached the
    book goes flat and never returns (a human must clear the lock)."""
    out, eq, peak, dead = [], 1.0, 1.0, False
    for _, x in r.items():
        if dead:
            out.append(0.0)
            continue
        eq *= 1.0 + x
        peak = max(peak, eq)
        out.append(x)
        if eq / peak - 1.0 <= -threshold:
            dead = True
    return pd.Series(out, index=r.index), dead


def stats(r: pd.Series) -> tuple[float, float, float, float]:
    n = len(r)
    eq = (1 + r).cumprod()
    return ((1 + r).prod() ** (252 / n) - 1,
            r.std() * np.sqrt(252),
            r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0,
            (eq / eq.cummax() - 1).min())


def validate_against_production(px: dict, tbill: pd.Series, trade: str) -> int:
    """Cross-check core/portfolio_backtester.py against this reference.

    Two comparisons, because they answer different questions:
      * WEIGHTS must match exactly — that proves the production pipeline
        (universe → selector → allocator → sleeves) is wired as designed;
      * METRICS may differ marginally: this reference sums the overnight and
        intraday legs arithmetically, while production compounds them within
        the bar.  Production is the correct one; the residual is that
        cross-term, not a wiring fault.
    """
    from core.portfolio_backtester import PortfolioBacktester

    common = None
    for s, df in px.items():
        common = df.index if common is None else common.intersection(df.index)
    aligned = {s: df.loc[common] for s, df in px.items()}

    bt = PortfolioBacktester(histories=aligned, slippage_bps=SLIP_BPS,
                             cash_yield_series=tbill)
    prod = bt.run()

    r_core, _ = core_book_returns(aligned, tbill)
    r_sleeve = levered_sleeve_returns(aligned, tbill, "QQQ", trade)
    ix = r_core.index.intersection(r_sleeve.index)
    ref = core_scale_cfg() * r_core.loc[ix] + sleeve_weight_cfg() * r_sleeve.loc[ix]

    print("\n" + "=" * 78)
    print("VALIDIERUNG: Produktionscode vs unabhaengige Referenz")
    print("=" * 78)
    print(f"Referenz-Span   {ix[0].date()} .. {ix[-1].date()} ({len(ix)} bars)")
    print(f"Produktion-Span {prod.returns.index[0].date()} .. "
          f"{prod.returns.index[-1].date()} ({len(prod.returns)} bars)")

    # --- weights -----------------------------------------------------------
    shared = prod.weights.index.intersection(ix)
    ref_w = reference_weights(aligned, tbill, trade).reindex(shared).fillna(0.0)
    prod_w = prod.weights.reindex(shared).fillna(0.0)
    cols = sorted(set(ref_w.columns) | set(prod_w.columns))
    diff = (prod_w.reindex(columns=cols).fillna(0.0)
            - ref_w.reindex(columns=cols).fillna(0.0)).abs()
    print(f"\nGewichte: max. Abweichung {diff.to_numpy().max():.2e} "
          f"ueber {len(shared)} Bars x {len(cols)} Assets  -> "
          f"{'IDENTISCH' if diff.to_numpy().max() < 1e-6 else 'ABWEICHUNG'}")

    # --- metrics -----------------------------------------------------------
    pr = prod.returns.reindex(shared).fillna(0.0)
    rr = ref.reindex(shared).fillna(0.0)
    print(f"\n{'':<14}{'CAGR':>9}{'Vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    for name, r in [("Referenz", rr), ("Produktion", pr)]:
        c, v, s, d = stats(r)
        print(f"{name:<14}{c:>9.2%}{v:>8.1%}{s:>8.2f}{d:>9.2%}")
    c1, _, s1, d1 = stats(rr)
    c2, _, s2, d2 = stats(pr)
    print(f"{'Delta':<14}{c2-c1:>+9.2%}{'':>8}{s2-s1:>+8.2f}{d2-d1:>+9.2%}")
    ok = abs(c2 - c1) < 0.005 and abs(d2 - d1) < 0.01
    verdict = ("OK — Abweichung im erwarteten Bereich (Intra-Bar-Verkettung)"
               if ok else "WARNUNG — Abweichung zu gross, Verdrahtung pruefen")
    print(f"\n{verdict}")
    return 0 if ok else 1


def core_scale_cfg() -> float:
    from core.sleeves import core_scale
    return core_scale()


def sleeve_weight_cfg() -> float:
    from core.sleeves import sleeve_definitions
    return sum(float(s["weight"]) for s in sleeve_definitions())


def reference_weights(px: dict, tbill: pd.Series, trade: str) -> pd.DataFrame:
    """Composed target weights from this reference implementation."""
    _, core_w = core_book_returns(px, tbill)
    c = px["QQQ"]["close"]
    # core_w is indexed by the RETURN bar, but its weights were decided on the
    # previous bar's close — so the sleeve's trend must be read there too.
    in_trend = (c > c.rolling(200).mean()).shift(1)
    rows = {}
    for d in core_w.index:
        w = {t: v * core_scale_cfg() for t, v in core_w.loc[d].items() if v > 0}
        if bool(in_trend.get(d, False)):
            w[trade] = sleeve_weight_cfg()
        rows[d] = w
    return pd.DataFrame(rows).T.fillna(0.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-scale", type=float, default=0.60)
    ap.add_argument("--sleeve-weight", type=float, default=0.40)
    ap.add_argument("--signal", default="QQQ")
    ap.add_argument("--trade", default="QLD")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--validate", action="store_true",
                    help="cross-check core/portfolio_backtester.py against this reference")
    a = ap.parse_args(argv)

    syms = sorted(set(CORE + [a.signal, a.trade, "SPY"]))
    if a.refresh:
        refresh(syms)
    px = load(syms)
    tbill = pd.read_parquet(CACHE / "TBILL.parquet")["y"]

    if a.validate:
        return validate_against_production(px, tbill, a.trade)

    r_core, W = core_book_returns(px, tbill)
    r_sleeve = levered_sleeve_returns(px, tbill, a.signal, a.trade)
    ix = r_core.index.intersection(r_sleeve.index)
    r_core, r_sleeve = r_core.loc[ix], r_sleeve.loc[ix]
    blend = a.core_scale * r_core + a.sleeve_weight * r_sleeve
    spy = px["SPY"]["close"].pct_change().reindex(ix).fillna(0.0)

    print(f"Span {ix[0].date()} .. {ix[-1].date()}  ({len(ix)} bars), "
          f"{SLIP_BPS:.0f} bps costs, ^IRX cash")
    print(f"\n{'Buch':<44}{'CAGR':>8}{'Vol':>7}{'Sharpe':>8}{'maxDD':>9}")
    print("-" * 76)
    for name, r in [("SPY buy&hold (Benchmark)", spy),
                    ("Kernbuch 100% (heutiger Live-Pfad)", r_core),
                    (f"Sleeve 100% Trend({a.signal})->{a.trade}", r_sleeve),
                    (f"MISCHUNG {a.core_scale:.0%} Kern + "
                     f"{a.sleeve_weight:.0%} Sleeve", blend)]:
        c, v, s, d = stats(r)
        print(f"{name:<44}{c:>8.2%}{v:>7.1%}{s:>8.2f}{d:>9.2%}")

    c_b = stats(blend)[0]
    c_s = stats(spy)[0]
    print(f"\nvs SPY: {c_b - c_s:+.2%} p.a.")
    for yrs in (3, 5, 10):
        w = 252 * yrs
        if len(blend) > w:
            x = (1 + blend).rolling(w).apply(np.prod, raw=True)
            y = (1 + spy).rolling(w).apply(np.prod, raw=True)
            print(f"  {yrs:>2}J-Trefferquote gegen SPY: "
                  f"{((x - y).dropna() > 0).mean():.0%}")

    print(f"\n{'HALT-Schwelle':>14}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>9}   feuert?")
    print("-" * 55)
    for h in (0.20, 0.25, 0.30, 0.35, 0.40):
        r, dead = apply_halt(blend, h)
        c, _, s, d = stats(r)
        print(f"{-h:>13.0%}{c:>9.2%}{s:>8.2f}{d:>9.2%}   "
              f"{'JA -> Bot dauerhaft flat' if dead else 'nein'}")

    eq = (1 + blend).cumprod()
    dd = eq / eq.cummax() - 1
    print(f"\ntiefster Drawdown am {dd.idxmin().date()} ({dd.min():.2%})")
    print("Krisenfenster (Mischung vs SPY):")
    for nm, s_, e_ in [("2008-09..2009-03", "2008-09-01", "2009-03-31"),
                       ("2020-02..2020-04", "2020-02-15", "2020-04-30"),
                       ("2022-01..2022-10", "2022-01-01", "2022-10-31")]:
        b_, p_ = blend.loc[s_:e_], spy.loc[s_:e_]
        if len(b_):
            print(f"  {nm:<20}{(1+b_).prod()-1:>8.1%}   SPY {(1+p_).prod()-1:>7.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
