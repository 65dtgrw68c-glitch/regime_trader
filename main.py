"""
main.py — central orchestrator for the regime_trader system.

Ties every component (data → features → HMM → strategy → risk → broker)
into one continuously running, fault-tolerant automated trading system.

Design for testability
-----------------------
`TradingSystem` accepts injectable dependencies (broker client, data feed,
alert manager, risk manager).  In production they default to the real
implementations; in tests they are replaced with fakes/mocks so the full
startup sequence and main loop can run on simulated data with no network.

Usage
-----
    python main.py               # live / paper trading
    python main.py --backtest    # historical simulation
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

from core.feature_engineering import FeatureEngineer
from core.hmm_engine import HMMEngine
from core.regime_strategies import RegimeOrchestrator
from core.risk_manager import CBLevel, RiskManager
from monitoring.alerts import AlertManager, SEVERITY_CRITICAL, SEVERITY_WARNING
from monitoring.logger import configure_logging, get_logger, TradeLogger
from settings import config

logger = logging.getLogger(__name__)


# ===========================================================================
# Per-ticker live state
# ===========================================================================

@dataclass
class TickerState:
    """Accumulating state for one symbol during live trading."""
    feature_engineer: FeatureEngineer
    engine: HMMEngine
    orchestrator: RegimeOrchestrator
    history: pd.DataFrame                       # accumulating OHLCV bars
    last_stable_regime: int = -1
    last_stable_label: str = "Unknown"
    bar_count: int = 0


@dataclass
class ShutdownReport:
    """Summary written on graceful shutdown."""
    started_at: str
    stopped_at: str
    bars_processed: int
    orders_submitted: int
    final_equity: float
    halted: bool
    reason: str = ""

    def to_text(self) -> str:
        return "\n".join([
            "=" * 50, "SHUTDOWN REPORT", "=" * 50,
            f"Started:          {self.started_at}",
            f"Stopped:          {self.stopped_at}",
            f"Bars processed:   {self.bars_processed}",
            f"Orders submitted: {self.orders_submitted}",
            f"Final equity:     ${self.final_equity:,.2f}",
            f"Risk halted:      {self.halted}",
            f"Reason:           {self.reason or 'normal shutdown'}",
            "=" * 50,
        ])


# ===========================================================================
# Trading system
# ===========================================================================

class TradingSystem:
    """The orchestrator: startup → main loop → graceful shutdown."""

    def __init__(
        self,
        tickers: Optional[list[str]] = None,
        client: Optional[object] = None,
        data_feed: Optional[object] = None,
        position_tracker: Optional[object] = None,
        order_executor: Optional[object] = None,
        risk_manager: Optional[object] = None,
        alert_manager: Optional[object] = None,
        trade_logger: Optional[object] = None,
        max_api_retries: Optional[int] = None,
    ) -> None:
        self.tickers = tickers if tickers is not None else config.TICKERS
        self._client = client
        self._data_feed = data_feed
        self._positions = position_tracker
        self._executor = order_executor
        self._risk = risk_manager
        self._alerts = alert_manager
        self._trade_logger = trade_logger
        self._max_api_retries = (
            max_api_retries if max_api_retries is not None else config.BROKER["max_retries"]
        )
        self._retry_delay = config.BROKER["retry_delay"]

        self._states: dict[str, TickerState] = {}
        self._running = False
        self._paused = False
        self._started_at: Optional[str] = None
        self._bars_processed = 0
        self._orders_submitted = 0
        self._equity = 0.0
        self._market_status = "unknown"

    # ==================================================================
    # STARTUP SEQUENCE (exact order required by Prompt 7)
    # ==================================================================

    def startup(self) -> bool:
        """Execute the 10-step startup sequence.  Returns True on success."""
        self._started_at = datetime.now(timezone.utc).isoformat()

        # 1 — Configuration
        logger.info("[startup 1/10] Loading configuration...")
        if not self.tickers:
            logger.error("No tickers configured in settings/config.TICKERS.")
            return False

        # 2 — Credentials (.env loaded by AlpacaClient)
        logger.info("[startup 2/10] Loading credentials / broker client...")
        if self._client is None:
            from broker.alpaca_client import AlpacaClient
            self._client = AlpacaClient()

        # 3 — Connect + verify account active & funded
        logger.info("[startup 3/10] Connecting to Alpaca and verifying account...")
        if not self._safe_call(self._client.verify_connection):
            self._alert("Alpaca account verification failed at startup.",
                        SEVERITY_CRITICAL, key="startup_verify")
            return False
        account = self._safe_call(self._client.get_account) or {}
        self._equity = float(account.get("equity", 0.0))
        if self._equity <= 0:
            logger.error("Account is not funded (equity=%.2f).", self._equity)
            return False

        # 4 — Market hours
        logger.info("[startup 4/10] Checking market hours...")
        self._market_status = self._current_market_status()
        logger.info("Market status: %s", self._market_status)

        # 5 — Historical data + 6 — train HMM (per ticker)
        if self._data_feed is None:
            from data.market_data import MarketDataFeed
            self._data_feed = MarketDataFeed(client=self._client)

        for ticker in self.tickers:
            logger.info("[startup 5-6/10] Fetching history & training HMM for %s...", ticker)
            hist = self._safe_call(self._data_feed.get_training_data, ticker)
            if hist is None or len(hist) < config.HMM["min_history_bars"]:
                logger.error("Insufficient history for %s — skipping.", ticker)
                continue
            try:
                fe = FeatureEngineer()
                feats = fe.fit_transform(hist)
                engine = HMMEngine(
                    n_iter=config.HMM["n_iter"],
                    random_state=config.HMM["random_state"],
                    min_history_bars=min(config.HMM["min_history_bars"], len(feats)),
                )
                engine.fit(feats)
            except Exception as exc:
                logger.error("HMM training failed for %s: %s", ticker, exc)
                continue
            self._states[ticker] = TickerState(
                feature_engineer=fe,
                engine=engine,
                orchestrator=RegimeOrchestrator(tickers=[ticker]),
                history=hist.copy(),
            )

        if not self._states:
            logger.error("No tickers successfully initialised — aborting startup.")
            return False

        # 7 — Risk manager with current equity + circuit breakers
        logger.info("[startup 7/10] Initialising Risk Manager (equity=%.2f)...", self._equity)
        if self._risk is None:
            self._risk = RiskManager()
        if self._risk.is_halted():
            logger.critical("Risk lock file present — refusing to start. "
                            "Review and delete it before resuming.")
            self._alert("Startup blocked: risk lock file present.",
                        SEVERITY_CRITICAL, key="startup_locked")
            return False
        self._risk.start_new_day(self._equity)

        # 8 — Position tracker sync
        logger.info("[startup 8/10] Syncing positions...")
        if self._positions is None:
            from broker.position_tracker import PositionTracker
            self._positions = PositionTracker(self._client)
        self._safe_call(self._positions.refresh)

        # 9 — Order executor + data feeds
        logger.info("[startup 9/10] Starting order executor & data feeds...")
        if self._executor is None:
            from broker.order_executor import OrderExecutor
            self._trade_logger = self._trade_logger or TradeLogger()
            self._executor = OrderExecutor(self._client, self._positions, self._trade_logger)
        if self._alerts is None:
            self._alerts = AlertManager()

        # 10 — Log startup status
        logger.info("[startup 10/10] Startup complete.")
        logger.info(
            "STATUS | equity=$%.2f | market=%s | tickers=%s | regime_models=%d",
            self._equity, self._market_status,
            list(self._states.keys()), len(self._states),
        )
        return True

    # ==================================================================
    # MAIN LOOP
    # ==================================================================

    def run(
        self,
        bar_source: Optional[Callable[[], Optional[dict]]] = None,
        max_iterations: Optional[int] = None,
        poll_interval: Optional[float] = None,
    ) -> None:
        """
        Run the main trading loop.

        Parameters
        ----------
        bar_source : callable returning {ticker: bar_series} per iteration,
                     or None to poll the data feed.  Returning None ends the loop.
        max_iterations : stop after N iterations (for tests / finite runs).
        poll_interval  : seconds between iterations (live mode).
        """
        self._running = True
        interval = poll_interval if poll_interval is not None else config.MONITORING["poll_interval_seconds"]
        iterations = 0

        self._install_signal_handlers()

        while self._running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            try:
                bars = bar_source() if bar_source else self._poll_bars()
            except Exception as exc:
                self._handle_data_drop(exc)
                bars = None

            if bars is None:
                break   # source exhausted / shutdown requested

            if not self._paused:
                for ticker, bar in bars.items():
                    self.run_once(ticker, bar)

            iterations += 1
            if bar_source is None and interval:
                time.sleep(interval)

        self.shutdown(reason="loop ended")

    def run_once(self, ticker: str, bar: pd.Series) -> dict:
        """
        Process a single new bar for one ticker.  Returns a decision dict
        for logging/inspection.  This is the unit the integration tests drive.
        """
        decision: dict = {"ticker": ticker, "action": "none"}
        state = self._states.get(ticker)
        if state is None:
            return decision

        # 1 — Append new bar to history
        state.history = self._append_bar(state.history, bar)
        state.bar_count += 1
        self._bars_processed += 1

        # 2 — Features (causal: only data up to this bar)
        try:
            feats = state.feature_engineer.transform(state.history)
            obs = feats.iloc[-1].values
        except Exception as exc:
            logger.warning("Feature computation failed for %s: %s", ticker, exc)
            return decision

        # 3-4 — HMM forward update + stability filter (with fallback)
        regime_idx, regime_label, proba, high_unc = self._safe_regime(state, obs)
        decision.update(regime=regime_label, confidence=float(max(proba) if len(proba) else 0))

        # 4 — Only act on a stable (confirmed) regime
        if regime_idx < 0:
            decision["action"] = "waiting_for_stable_regime"
            return decision
        state.last_stable_regime = regime_idx
        state.last_stable_label = regime_label

        # 5 — Strategy signal
        vol_z = float(feats.iloc[-1].get("volume_zscore_21d", 0.0))
        strat_signal = state.orchestrator.evaluate(
            regime_index=regime_idx, regime_label=regime_label,
            proba=proba, high_uncertainty=high_unc, volume_zscore=vol_z,
        )

        # 10 — Circuit-breaker check FIRST (risk has veto power)
        price = float(bar.get("close", state.history["close"].iloc[-1]))
        equity = self._current_equity()
        cb = self._risk.update_equity(
            equity, regime_label=regime_label,
            open_positions=self._open_positions_dict(),
        )
        if cb >= CBLevel.HALT or self._risk.is_halted():
            decision["action"] = "halted"
            self._alert(f"Trading halted by circuit breaker ({cb.name}).",
                        SEVERITY_CRITICAL, key="cb_halt")
            self._flatten_all()
            return decision
        if self._risk.should_flatten():
            decision["action"] = "flatten"
            self._flatten_all()
            return decision

        # 6 — Risk sizing
        stop_price = price * (1.0 - config.RISK["stop_loss_pct"])
        target_weight = sum(strat_signal.target_weights.values())
        qty = self._risk.size_position(price, stop_price, equity)
        qty = int(qty * min(1.0, target_weight))

        # 7 — Validate + submit
        if qty > 0:
            account = self._safe_call(self._client.get_account) or {}
            buying_power = float(account.get("buying_power", equity))
            validation = self._risk.validate_order(
                ticker, qty, price, equity, buying_power,
                proposed_leverage=strat_signal.effective_leverage,
                regime_label=regime_label,
            )
            if validation.approved:
                self._submit(ticker, int(validation.approved_qty), price, regime_label, strat_signal.confidence)
                decision["action"] = "order_submitted"
                decision["qty"] = int(validation.approved_qty)
            else:
                decision["action"] = "rejected_by_risk"
                decision["reason"] = validation.reason
                logger.info("Risk rejected %s order: %s", ticker, validation.reason)

        # 8 — Refresh positions
        self._safe_call(self._positions.refresh)

        # 9 — Log decision
        logger.info("Decision for %s: %s", ticker, decision)
        return decision

    # ==================================================================
    # ERROR HANDLING — four failure modes
    # ==================================================================

    def _safe_call(self, fn: Callable, *args, **kwargs):
        """
        Failure mode 1: Alpaca API down/unreachable.
        Retry with exponential backoff; after repeated failures, pause + alert.
        """
        last_exc = None
        for attempt in range(self._max_api_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                wait = self._retry_delay * (2 ** attempt)
                logger.warning(
                    "API call %s failed (attempt %d/%d): %s — backoff %.2fs",
                    getattr(fn, "__name__", fn), attempt + 1, self._max_api_retries, exc, wait,
                )
                if wait > 0:
                    time.sleep(wait)
        # Repeated failures → pause trading + alert
        self._paused = True
        self._alert(
            f"Alpaca API unreachable after {self._max_api_retries} retries: {last_exc}",
            SEVERITY_CRITICAL, key="api_down",
        )
        logger.error("Pausing trading after repeated API failures.")
        return None

    def _safe_regime(self, state: TickerState, obs):
        """
        Failure mode 2: HMM prediction error.
        Fall back to the last known stable regime; log and continue.
        """
        try:
            state.engine.update(obs)
            idx = state.engine.current_regime()
            label = state.engine.current_regime_label()
            proba = state.engine.predict_proba_current()
            high_unc = state.engine.high_uncertainty
            return idx, label, proba, high_unc
        except Exception as exc:
            logger.error("HMM prediction error for %s: %s — using last stable regime.",
                         state.engine, exc)
            import numpy as np
            n = max(state.engine.n_components, 1)
            proba = np.full(n, 1.0 / n)
            return state.last_stable_regime, state.last_stable_label, proba, True

    def _handle_data_drop(self, exc: Exception) -> None:
        """
        Failure mode 3: data feed drop.
        Attempt reconnect; if it fails, pause trading and alert.
        """
        logger.warning("Data feed error: %s — attempting reconnect...", exc)
        # Probe connectivity directly (no _safe_call: we don't want its
        # pause side-effect to mask the reconnect outcome).
        reconnected = False
        try:
            clock = self._client.get_clock() if self._client else None
            reconnected = clock is not None
        except Exception as probe_exc:
            logger.warning("Reconnect probe failed: %s", probe_exc)
            reconnected = False
        if not reconnected:
            self._paused = True
            self._alert("Data feed dropped and reconnect failed — trading paused.",
                        SEVERITY_CRITICAL, key="data_drop")
        else:
            logger.info("Data feed reconnected.")

    def _handle_order_rejection(self, ticker: str, reason: str) -> None:
        """
        Failure mode 4: order rejected by Alpaca.
        Log the reason; do NOT retry automatically; alert the user.
        """
        logger.error("Order rejected for %s: %s", ticker, reason)
        self._alert(f"Order rejected for {ticker}: {reason}",
                    SEVERITY_WARNING, key=f"reject_{ticker}")

    # ==================================================================
    # GRACEFUL SHUTDOWN
    # ==================================================================

    def shutdown(self, reason: str = "") -> ShutdownReport:
        """Cancel pending orders, flush logs, write a shutdown report."""
        self._running = False
        logger.info("Shutting down (%s)...", reason or "normal")

        # Cancel all pending orders
        if self._executor is not None:
            try:
                self._executor.cancel_all_open_orders()
            except Exception as exc:
                logger.warning("Failed to cancel orders on shutdown: %s", exc)

        report = ShutdownReport(
            started_at=self._started_at or "n/a",
            stopped_at=datetime.now(timezone.utc).isoformat(),
            bars_processed=self._bars_processed,
            orders_submitted=self._orders_submitted,
            final_equity=self._current_equity(),
            halted=bool(self._risk.is_halted()) if self._risk else False,
            reason=reason,
        )
        logger.info("\n%s", report.to_text())

        # Flush all logging handlers
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
        return report

    # ==================================================================
    # Helpers
    # ==================================================================

    def _submit(self, ticker, qty, price, regime, confidence) -> None:
        try:
            oid = self._executor.submit_order(ticker, qty, "buy", order_type="market")
            if not oid:
                self._handle_order_rejection(ticker, "broker returned no order id")
                return
            self._orders_submitted += 1
        except Exception as exc:
            self._handle_order_rejection(ticker, str(exc))

    def _flatten_all(self) -> None:
        if self._executor is not None:
            self._safe_call(self._executor.cancel_all_open_orders)
            positions = self._open_positions_dict()
            target = {t: 0 for t in positions}
            if target:
                self._safe_call(self._executor.rebalance, target)

    def _poll_bars(self) -> Optional[dict]:
        """Poll the data feed for the latest bar of each ticker (live mode)."""
        bars = {}
        for ticker in self._states:
            bar = self._safe_call(self._data_feed.get_latest_bar, ticker)
            if bar is not None:
                bars[ticker] = bar
        return bars or None

    def _current_market_status(self) -> str:
        clock = self._safe_call(self._client.get_clock) or {}
        if clock.get("is_open"):
            return "open"
        return "closed"

    def _current_equity(self) -> float:
        account = self._safe_call(self._client.get_account) if self._client else None
        if account:
            self._equity = float(account.get("equity", self._equity))
        return self._equity

    def _open_positions_dict(self) -> dict:
        if self._positions is None:
            return {}
        try:
            return {
                t: {"unrealised_pnl": p.unrealised_pnl, "qty": p.qty}
                for t, p in self._positions.get_positions().items()
            }
        except Exception:
            return {}

    @staticmethod
    def _append_bar(history: pd.DataFrame, bar: pd.Series) -> pd.DataFrame:
        row = bar.to_frame().T
        row.columns = [c.lower() for c in row.columns]
        return pd.concat([history, row])

    def _alert(self, message: str, severity: str, key: Optional[str] = None) -> None:
        if self._alerts is not None:
            try:
                self._alerts.alert(message, severity, key=key)
            except Exception as exc:
                logger.error("Alert dispatch failed: %s", exc)

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info("Received signal %s — initiating graceful shutdown.", signum)
            self._running = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # Not in main thread (e.g. tests) — ignore.
                pass


# ===========================================================================
# Entry points
# ===========================================================================

def run_live() -> int:
    configure_logging()
    system = TradingSystem()
    if not system.startup():
        logger.error("Startup failed — exiting.")
        return 1
    try:
        system.run()
    except KeyboardInterrupt:
        system.shutdown(reason="keyboard interrupt")
    return 0


def run_backtest() -> int:
    configure_logging()
    from core.backtester import Backtester
    from core.performance import PerformanceAnalyser
    from data.market_data import MarketDataFeed

    tickers = config.TICKERS or ["SPY"]
    feed = MarketDataFeed()
    ticker = tickers[0]
    data = feed.get_training_data(ticker, years=4.0)
    bt = Backtester(ticker=ticker)
    result = bt.run(data)
    print(PerformanceAnalyser.from_backtest_result(result).report())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="regime_trader")
    parser.add_argument("--backtest", action="store_true",
                        help="run a historical backtest instead of live trading")
    args = parser.parse_args()
    return run_backtest() if args.backtest else run_live()


if __name__ == "__main__":
    raise SystemExit(main())
