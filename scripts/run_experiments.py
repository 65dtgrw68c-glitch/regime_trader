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
1. Alpaca (default): uses the ALPACA_API_KEY / ALPACA_SECRET_KEY already in
   .env for live trading — the same paper-trading account this bot trades
   through. Since the bot itself talks to api.alpaca.markets, this endpoint
   is normally already reachable from wherever the bot runs (Codespaces,
   corporate networks), unlike Stooq. Plain ticker symbols, e.g. "SPY".
2. --csv: a local OHLCV file you downloaded yourself (Stooq or Yahoo Finance
   "Historical Data" export). Use this if Alpaca credentials aren't set up
   or its data endpoint is blocked too.
3. --stooq: force the free Stooq CSV download instead of Alpaca. Stooq
   blocks cloud-datacenter IPs (Codespaces) and is blocked by some corporate
   firewalls — try --csv or the Alpaca default instead if this 404s.
4. --synthetic: offline regime-switching random walk.  ONLY useful as a
   smoke test — parameter choices tuned on synthetic noise mean nothing
   for real markets.

Usage
-----
    python scripts/run_experiments.py                          # SPY via Alpaca, full grid
    python scripts/run_experiments.py --ticker QQQ --bars 1500
    python scripts/run_experiments.py --csv spy_daily.csv       # local file, no network
    python scripts/run_experiments.py --stooq --ticker spy.us   # legacy Stooq path
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
import os
import sys
import time
from datetime import datetime, timedelta, timezone
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
    # ── References ─────────────────────────────────────────────────────────
    (
        "legacy_churn",
        {
            "use_trend_filter": False,
            "strategy_overrides": {"rebalance_max_bars": 1, "drift_threshold": 0.0},
        },
        "pre-fix behaviour: rebalance every bar, no trend filter",
    ),
    (
        "regime_defaults",
        {},
        "legacy regime-driven mode with current defaults",
    ),
    (
        "regime_smooth_30",
        {"strategy_overrides": {"alloc_smoothing": 0.30}},
        "legacy mode + best turnover damper from the damper grid",
    ),
    # ── Trend-core family ──────────────────────────────────────────────────
    # SMA-200 was the only signal robust across SPY AND QQQ (Sharpe
    # 0.77/1.00); these make it the core and demote the HMM to a risk
    # overlay. Goal: reproduce the sma_200 benchmark net of costs, then see
    # whether any overlay ADDS to it on both tickers.
    (
        "trend_core",
        {"strategy_overrides": {"trend_core": True}},
        "pure SMA-200 core: in-trend=100%, out=cash, no overlay",
    ),
    (
        "tc_confirm3",
        {"strategy_overrides": {"trend_core": True, "trend_confirm_bars": 3}},
        "trend core, flips must persist 3 bars (whipsaw damper)",
    ),
    (
        "tc_hi50",
        {"strategy_overrides": {"trend_core": True,
                                "trend_core_high_scale": 0.5}},
        "trend core, HIGH-tier regimes halve the allocation",
    ),
    (
        "tc_confirm3_hi50",
        {"strategy_overrides": {"trend_core": True, "trend_confirm_bars": 3,
                                "trend_core_high_scale": 0.5}},
        "trend core + 3-bar confirm + HIGH-tier halving",
    ),
    (
        "tc_confirm3_brake",
        {"strategy_overrides": {"trend_core": True, "trend_confirm_bars": 3,
                                "min_trade_delta": 0.02}},
        "trend core + 3-bar confirm + skip <2pt resync trades",
    ),
    (
        "tc_vol15",
        {"strategy_overrides": {"trend_core": True, "vol_target": 0.15}},
        "trend core + 15% annualised vol target",
    ),
    # ── Risk-layer A/B: daily HALVE/FLATTEN breakers on vs off ─────────────
    # The daily breakers measure close-to-close equity on a daily system:
    # they fire after the loss is realised, sell the low, and the drift
    # trigger re-buys next bar.  These rows measure what that whipsaw costs.
    (
        "trend_core_nocb",
        {"strategy_overrides": {"trend_core": True},
         "risk_overrides": {"cb_daily_enabled": False}},
        "pure SMA-200 core, daily -2%/-3% breakers OFF",
    ),
    (
        "tc_vol15_nocb",
        {"strategy_overrides": {"trend_core": True, "vol_target": 0.15},
         "risk_overrides": {"cb_daily_enabled": False}},
        "trend core + vol target, daily -2%/-3% breakers OFF",
    ),
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def fetch_alpaca(symbol: str, bars: int) -> pd.DataFrame:
    """Download daily history for `symbol` via the Alpaca Market Data API,
    using the same ALPACA_API_KEY / ALPACA_SECRET_KEY the bot trades with.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY not set (checked .env and "
            "environment). Set them, or use --csv / --stooq / --synthetic instead."
        )

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    symbol = symbol.upper().replace(".US", "").lstrip("^")
    client = StockHistoricalDataClient(api_key, secret_key)
    end = datetime.now(timezone.utc) - timedelta(minutes=16)   # free IEX feed lag
    start = end - timedelta(days=int(bars * 1.6) + 15)         # buffer for weekends/holidays
    print(f"Fetching {symbol} from Alpaca (IEX feed, adjusted, {start.date()} … {end.date()}) ...")
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
        adjustment=Adjustment.ALL,   # total-return prices (splits + dividends)
    )
    bars_df = client.get_stock_bars(req).df
    if bars_df.empty:
        raise ValueError(f"Alpaca returned no bars for '{symbol}'. Wrong symbol?")
    df = bars_df.xs(symbol, level="symbol").copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    out = df[["open", "high", "low", "close", "volume"]].astype(float).dropna()
    if len(out) < 400:
        raise ValueError(f"Only {len(out)} usable bars for '{symbol}' — too few.")
    print(f"Got {len(out)} daily bars ({out.index[0].date()} … {out.index[-1].date()}).")
    return out


def fetch_yahoo(symbol: str, bars: int, range_str: str = "30y") -> pd.DataFrame:
    """
    Download long daily history from the Yahoo Finance chart API (reachable
    from Codespaces, unlike Stooq).  OHLC are rescaled by adjclose/close so
    prices are split+dividend adjusted (total-return), matching the Alpaca
    adjustment=ALL path.

    This is the STRUCTURAL-validation data source: Alpaca/IEX history only
    reaches back to ~2020 (essentially one bear market).  Decisions like
    breaker design or vol-target level should hold on 25+ years — 2000-02,
    2008, 2011, 2015, 2018 — before they are trusted.
    """
    import requests

    symbol = symbol.upper().replace(".US", "").lstrip("^")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_str, "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    print(f"Fetching {symbol} from Yahoo Finance ({range_str}, adjusted) ...")
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts = (
        pd.to_datetime(result["timestamp"], unit="s", utc=True)
        .tz_convert("America/New_York").tz_localize(None).normalize()
    )
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"]["adjclose"][0]["adjclose"]
    df = pd.DataFrame(
        {"open": quote["open"], "high": quote["high"], "low": quote["low"],
         "close": quote["close"], "volume": quote["volume"], "adjclose": adj},
        index=ts,
    ).dropna()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    factor = df["adjclose"] / df["close"]
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * factor
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    if len(out) < 400:
        raise ValueError(f"Only {len(out)} usable bars for '{symbol}' — too few.")
    print(f"Got {len(out)} daily bars ({out.index[0].date()} … {out.index[-1].date()}).")
    return out


def fetch_tbill_yields(range_str: str = "30y") -> pd.Series:
    """
    Daily annualised 13-week T-bill yields (^IRX, decimals) from Yahoo.

    Feeds the backtester's idle-cash credit with the REAL risk-free rate
    instead of a flat approximation — bills paid ~5% pre-2008 and in
    2023-24 but ~0% in 2020-21, which materially changes what long cash
    stretches were worth.
    """
    import requests

    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EIRX"
    params = {"range": range_str, "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts = (
        pd.to_datetime(result["timestamp"], unit="s", utc=True)
        .tz_convert("America/New_York").tz_localize(None).normalize()
    )
    close = result["indicators"]["quote"][0]["close"]
    ser = pd.Series(close, index=ts, name="tbill").dropna() / 100.0
    ser = ser[~ser.index.duplicated(keep="last")].sort_index()
    if len(ser) < 100:
        raise ValueError(f"Only {len(ser)} ^IRX observations — too few.")
    print(f"Got {len(ser)} ^IRX yields ({ser.index[0].date()} … "
          f"{ser.index[-1].date()}, last={ser.iloc[-1]:.2%}).")
    return ser


def fetch_stooq(symbol: str) -> pd.DataFrame:
    """Download full daily history for `symbol` from Stooq."""
    if "." not in symbol and "^" not in symbol:
        symbol = f"{symbol}.us"          # allow plain tickers like "SPY" too
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

def sharpe_block_bootstrap_ci(
    returns: pd.Series,
    n_boot: int = 1000,
    block: int = 21,
    level: float = 0.90,
    seed: int = 0,
) -> tuple[float, float]:
    """
    Circular block-bootstrap confidence interval for the annualised Sharpe.

    Daily returns are autocorrelated in volatility, so an i.i.d. bootstrap
    understates uncertainty; resampling contiguous ~1-month blocks keeps
    that structure.  The CI is the honesty check the summary table lacks —
    two variants whose intervals overlap heavily are NOT distinguishable,
    however tidy the point-estimate ranking looks.
    """
    r = np.asarray(returns.dropna(), dtype=float)
    n = len(r)
    if n < 2 * block:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    # Circular blocks: indices wrap around so every start is valid.
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    samples = r[idx.reshape(n_boot, -1)[:, :n]]          # (n_boot, n)
    mean = samples.mean(axis=1)
    sd = samples.std(axis=1, ddof=1)
    valid = sd > 1e-12
    sharpes = np.where(valid, mean / np.where(valid, sd, 1.0) * np.sqrt(252), 0.0)
    alpha = (1.0 - level) / 2.0
    lo, hi = np.quantile(sharpes, [alpha, 1.0 - alpha])
    return (float(lo), float(hi))


def summarise(name: str, returns: pd.Series, trade_log: pd.DataFrame,
              commission: float, initial_capital: float) -> dict:
    """One comparison-table row."""
    if trade_log is not None and len(trade_log) and "qty" in trade_log:
        traded_notional = float((trade_log["qty"] * trade_log["fill_price"]).sum())
        n_trades = len(trade_log)
    else:
        traded_notional = 0.0
        n_trades = 0
    ci_lo, ci_hi = sharpe_block_bootstrap_ci(returns)
    return {
        "variant":       name,
        "total_return":  total_return(returns),
        "cagr":          annualised_return(returns),
        "sharpe":        sharpe_ratio(returns),
        "sharpe_ci":     (ci_lo, ci_hi),
        "max_dd":        max_drawdown(returns),
        "n_trades":      n_trades,
        "turnover_x":    traded_notional / initial_capital if initial_capital else 0.0,
        "est_commission": traded_notional * commission,
    }


def to_markdown(rows: list[dict], meta: str) -> str:
    header = (
        "| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | "
        "Trades | Turnover× | Est. commission |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['variant']} | ERROR: {r['error']} ||||||||")
            continue
        lo, hi = r.get("sharpe_ci", (float("nan"), float("nan")))
        ci = "n/a" if lo != lo else f"[{lo:.2f}, {hi:.2f}]"
        lines.append(
            f"| {r['variant']} "
            f"| {r['total_return']:+.1%} "
            f"| {r['cagr']:+.1%} "
            f"| {r['sharpe']:.2f} "
            f"| {ci} "
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
    ap.add_argument("--ticker", default="SPY",
                    help="Ticker symbol, e.g. SPY / QQQ (default: SPY). "
                         "With --stooq, Stooq notation also works (spy.us).")
    ap.add_argument("--bars", type=int, default=2000,
                    help="Use only the most recent N bars (default: 2000 ≈ 8y)")
    ap.add_argument("--csv", default=None,
                    help="Path to a local OHLCV CSV (Stooq or Yahoo export). "
                         "Use this if Alpaca creds/endpoint aren't available.")
    ap.add_argument("--yahoo", action="store_true",
                    help="Fetch up to 30y of adjusted daily history from the "
                         "Yahoo chart API — the long-history source for "
                         "structural validation (Alpaca/IEX only reaches ~2020).")
    ap.add_argument("--stooq", action="store_true",
                    help="Force the free Stooq download instead of Alpaca "
                         "(blocked from Codespaces and some corporate networks).")
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
    elif args.yahoo:
        data = fetch_yahoo(args.ticker, args.bars).iloc[-args.bars:]
        source = f"{args.ticker} via Yahoo (30y, adjusted)"
    elif args.synthetic:
        data = synthetic_ohlcv(args.bars, seed=args.seed)
        source = f"synthetic (seed={args.seed})"
    elif args.stooq:
        data = fetch_stooq(args.ticker).iloc[-args.bars:]
        source = f"{args.ticker} via Stooq"
    else:
        data = fetch_alpaca(args.ticker, args.bars).iloc[-args.bars:]
        source = f"{args.ticker} via Alpaca (IEX feed)"

    # Real risk-free series for the idle-cash credit; flat fallback offline.
    if args.synthetic:
        tbill, yield_note = None, "flat (synthetic run)"
    else:
        try:
            tbill = fetch_tbill_yields()
            yield_note = "^IRX daily series"
        except Exception as exc:
            tbill = None
            yield_note = "flat fallback (^IRX fetch failed)"
            print(f"^IRX fetch failed ({exc}) — using flat "
                  f"cash_yield_annual from config.")

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
                cash_yield_series=tbill,
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
    from settings import config as _cfg
    meta = (
        f"Data: **{source}**, {len(data)} bars "
        f"({data.index[0].date()} … {data.index[-1].date()})  \n"
        f"Walk-forward: train={args.train_window} / test={args.test_window}, "
        f"seed={args.seed}  \n"
        f"Costs: commission={_cfg.BACKTEST['commission']:.4f}, "
        f"slippage={_cfg.BACKTEST['slippage']:.4f} per fill (charged to equity)  \n"
        f"Execution: decisions on bar close fill at the NEXT bar's open "
        f"(benchmarks use the SAME timing, costless); idle cash earns "
        f"{yield_note} (flat fallback "
        f"{_cfg.BACKTEST.get('cash_yield_annual', 0.0):.1%} p.a.), credited to "
        f"strategy and sma_200/random benchmarks alike  \n"
        f"Exposure cap (RISK.max_position_size): "
        f"{_cfg.RISK['max_position_size']:.2f} — strategy rows are capped at "
        f"this fraction of equity, benchmarks run at 100%.  \n"
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
