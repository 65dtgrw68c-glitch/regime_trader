"""
dashboard_data — assembles the `state` dict the Streamlit dashboard renders.

Two data sources:
  * Demo mode  — generates synthetic OHLCV, runs the real walk-forward
    Backtester, and turns the result into a fully populated dashboard state.
    This makes `streamlit run monitoring/dashboard.py` show a working UI
    with no broker connection or credentials.
  * Live mode  — pulls account + recent bars from Alpaca, fits the HMM, and
    reports the current regime.  Falls back to demo on any failure.

The light, pure helpers (generate_demo_ohlcv, assemble_signals) are unit
tested.  The heavier providers import core/broker modules lazily.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from settings import config

logger = logging.getLogger(__name__)


# ===========================================================================
# Pure helpers (testable)
# ===========================================================================

def generate_demo_ohlcv(ticker: str = "DEMO", n: int = 400, seed: int = 7) -> pd.DataFrame:
    """
    Reproducible synthetic OHLCV with three embedded volatility regimes so the
    HMM has structure to detect.  Used for the demo dashboard.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    vol = np.select(
        [np.arange(n) < n // 3, np.arange(n) < 2 * n // 3],
        [0.006, 0.020], default=0.011,
    )
    drift = np.select(
        [np.arange(n) < n // 3, np.arange(n) < 2 * n // 3],
        [0.0009, -0.0008], default=0.0003,
    )
    log_ret = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(log_ret))
    wig = rng.uniform(0.001, 0.004, n)
    return pd.DataFrame({
        "open":   close * (1 + rng.normal(0, 0.001, n)),
        "high":   close * (1 + wig),
        "low":    close * (1 - wig),
        "close":  close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    }, index=dates)


def assemble_signals(
    trade_log: pd.DataFrame,
    stop_loss_pct: Optional[float] = None,
) -> list[dict]:
    """
    Map a Backtester trade log into signal-feed rows with the dashboard's
    expected columns.  Unknown fields are left blank.
    """
    if trade_log is None or len(trade_log) == 0:
        return []
    stop_loss_pct = stop_loss_pct if stop_loss_pct is not None else config.RISK["stop_loss_pct"]
    rows = []
    for _, t in trade_log.iterrows():
        side = str(t.get("side", "")).lower()
        entry = float(t.get("fill_price", 0.0) or 0.0)
        # Protective stop sits below entry for longs, above for shorts.
        stop = entry * (1 - stop_loss_pct) if side == "buy" else entry * (1 + stop_loss_pct)
        rows.append({
            "timestamp":      t.get("timestamp", ""),
            "ticker":         t.get("ticker", ""),
            "direction":      side,
            "regime":         t.get("regime", ""),
            "allocation_pct": round(float(t.get("confidence", 0.0) or 0.0) * 100, 1),
            "entry_price":    round(entry, 2),
            "stop_price":     round(stop, 2),
            "pnl":            "",
            "status":         "filled",
        })
    return rows


def _leverage_for(regime_label: str) -> float:
    caps = getattr(config, "REGIME_LEVERAGE_CAPS", {})
    return caps.get(regime_label, config.RISK["max_leverage"])


# ===========================================================================
# Demo provider (runs the real backtester)
# ===========================================================================

def get_demo_state(ticker: str = "DEMO", seed: int = 7) -> dict:
    """Build a fully populated dashboard state from a backtest on demo data."""
    from core.backtester import Backtester
    from core.performance import PerformanceAnalyser

    ohlcv = generate_demo_ohlcv(ticker=ticker, seed=seed)
    bt = Backtester(ticker=ticker, train_window=252, test_window=126, random_seed=seed)
    result = bt.run(ohlcv)
    pa = PerformanceAnalyser.from_backtest_result(result)

    regimes = result.regime_labels
    confidence = result.confidence
    equity = result.equity_curve

    last_regime = str(regimes.iloc[-1]) if len(regimes) else "Unknown"
    last_conf = float(confidence.iloc[-1]) if len(confidence) else 0.0
    final_equity = float(equity.iloc[-1]) if len(equity) else result.initial_capital
    peak_equity = float(equity.max()) if len(equity) else result.initial_capital

    # Price frame aligned to the out-of-sample (regime) period for overlays.
    price_df = ohlcv.loc[regimes.index].copy()
    price_df.columns = [c.lower() for c in price_df.columns]

    return {
        "mode":            "demo",
        "regime_label":    last_regime,
        "confidence":      last_conf,
        "portfolio_value": final_equity,
        "buying_power":    final_equity,        # demo: no margin model
        "n_regimes":       int(regimes.nunique()) if len(regimes) else 0,
        "positions":       {},                  # backtest has no live positions
        "price_df":        price_df,
        "regime_history":  regimes,
        "confidence_series": confidence,
        "equity_curve":    equity,
        "signals":         assemble_signals(result.trade_log),
        "cb_level":        "NONE",
        "current_equity":  final_equity,
        "peak_equity":     peak_equity,
        "leverage":        _leverage_for(last_regime),
        "metrics":         pa.summary(),
        "benchmarks":      pa.vs_benchmark().to_dict("records"),
    }


# ===========================================================================
# Live provider (Alpaca) — falls back to demo on failure
# ===========================================================================

def get_live_state(ticker: str) -> Optional[dict]:
    """
    Build dashboard state from live Alpaca data + a freshly fit HMM.
    Returns None if anything fails (caller falls back to demo).
    """
    try:
        from broker.alpaca_client import AlpacaClient
        from broker.position_tracker import PositionTracker
        from data.market_data import MarketDataFeed
        from core.feature_engineering import FeatureEngineer
        from core.hmm_engine import HMMEngine

        client = AlpacaClient()
        if not client.verify_connection():
            return None
        account = client.get_account()

        feed = MarketDataFeed(client=client)
        hist = feed.get_training_data(ticker, years=2.0)

        fe = FeatureEngineer()
        feats = fe.fit_transform(hist)
        engine = HMMEngine(
            n_iter=config.HMM["n_iter"],
            random_state=config.HMM["random_state"],
            min_history_bars=min(config.HMM["min_history_bars"], len(feats)),
        )
        engine.fit(feats)
        # Replay features causally to get the current confirmed regime.
        engine.update_batch(feats)
        regime_label = engine.current_regime_label()
        proba = engine.predict_proba_current()

        tracker = PositionTracker(client)
        tracker.refresh()
        positions = {
            t: {"qty": p.qty, "unrealised_pnl": p.unrealised_pnl}
            for t, p in tracker.get_positions().items()
        }

        price_df = hist.copy()
        price_df.columns = [c.lower() for c in price_df.columns]

        return {
            "mode":            "live",
            "regime_label":    regime_label,
            "confidence":      float(max(proba)) if len(proba) else 0.0,
            "portfolio_value": account["portfolio_value"],
            "buying_power":    account["buying_power"],
            "n_regimes":       engine.n_components,
            "positions":       positions,
            "price_df":        price_df.tail(250),
            "regime_history":  None,
            "confidence_series": None,
            "cb_level":        "NONE",
            "current_equity":  account["equity"],
            "peak_equity":     account["equity"],
            "leverage":        _leverage_for(regime_label),
            "signals":         [],
        }
    except Exception as exc:
        logger.warning("Live state unavailable (%s) — falling back to demo.", exc)
        return None


# ===========================================================================
# Dispatcher
# ===========================================================================

def load_state(mode: str, ticker: str, seed: int = 7) -> dict:
    """Return dashboard state for the chosen mode, falling back to demo."""
    if mode == "live":
        state = get_live_state(ticker)
        if state is not None:
            return state
    return get_demo_state(ticker=ticker, seed=seed)
