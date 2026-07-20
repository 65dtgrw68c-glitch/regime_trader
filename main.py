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
from core.regime_strategies import (
    RegimeOrchestrator,
    is_trend_confirmed,
    realised_vol_from_close,
    shares_for_target_weight,
)
from core.risk_manager import CBLevel, RiskManager
from monitoring.alerts import AlertManager, SEVERITY_CRITICAL, SEVERITY_WARNING
from monitoring.logger import configure_logging, get_logger, TradeLogger
from settings import config

logger = logging.getLogger(__name__)

# Cap on the per-ticker OHLCV history kept in memory.  The widest causal
# window any feature needs is the SMA-200 (plus warm-up); 800 bars is ample
# and keeps the per-bar feature recompute O(1) instead of growing forever.
_MAX_HISTORY_BARS = 800

# Bars replayed through the HMM at startup so its stability filter has a
# confirmed regime immediately (must exceed the 3-bar confirmation window;
# matches the periodic-refit warm-up).
_HMM_WARMUP_BARS = 30


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
        # Trading date the daily circuit breakers are currently anchored to;
        # run_once rolls it forward when a bar from a new day arrives.
        self._risk_day = None
        self._market_open_cache: Optional[bool] = None
        self._market_check_ts = 0.0

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

        # 2 — Credentials (.env loaded by the concrete broker client)
        logger.info("[startup 2/10] Loading credentials / broker client (%s)...",
                    config.BROKER.get("provider", "alpaca"))
        if self._client is None:
            from broker.factory import create_broker
            self._client = create_broker()

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
                # Warm the forward pass + stability filter so a regime is
                # CONFIRMED before the first live bar. The HMM only confirms a
                # regime after _CONFIRM_BARS (3) consecutive updates; the
                # scheduled --once model feeds just ONE new bar per process, so
                # without this warm-up current_regime() stays -1 forever and
                # run_once bails out at "waiting_for_stable_regime" every day —
                # i.e. the bot would never trade. (Also removes the 3-bar
                # cold-start delay in the continuous loop.)
                for row in feats.iloc[-_HMM_WARMUP_BARS:].values:
                    engine.update(row)
            except Exception as exc:
                logger.error("HMM training failed for %s: %s", ticker, exc)
                continue
            # The portfolio exposure cap goes INTO the orchestrator target so
            # the drift trigger compares against what will actually be held
            # (a downstream sizing clip left a permanent target-vs-held gap
            # that fired a rebalance on every bar).  ORCHESTRATOR may still
            # override it explicitly.
            orch_kwargs = {
                "max_exposure": config.RISK["max_position_size"],
                **getattr(config, "ORCHESTRATOR", {}),
            }
            self._states[ticker] = TickerState(
                feature_engineer=fe,
                engine=engine,
                orchestrator=RegimeOrchestrator(tickers=[ticker], **orch_kwargs),
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
        # Reconcile: a crash/restart may have left working orders at the
        # broker.  Start from a clean slate — positions were synced in step 8,
        # and any still-pending order would double up with the next decision.
        self._safe_call(self._executor.cancel_all_open_orders)

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
            # A pause (API outage, data drop) must not be terminal: probe the
            # broker and resume automatically once connectivity is back.
            # (Previously nothing ever reset _paused — one transient outage
            # silently stopped trading until a manual restart.)
            if self._paused:
                self._attempt_resume()
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

        # 1 — Append new bar to history.  The poll loop re-delivers the same
        #     (possibly still-forming) daily bar many times per day; appending
        #     it repeatedly corrupted every rolling feature and produced one
        #     order per poll.  A re-delivered timestamp only UPDATES the row
        #     (keep=last); the decision pipeline runs once per NEW bar.
        prev_len = len(state.history)
        state.history = self._append_bar(state.history, bar)
        if len(state.history) <= prev_len:
            decision["action"] = "stale_bar"
            return decision
        if len(state.history) > _MAX_HISTORY_BARS:
            state.history = state.history.iloc[-_MAX_HISTORY_BARS:]
        state.bar_count += 1
        self._bars_processed += 1

        # 1b — Periodic model refresh.  The HMM and feature scaler were
        #      previously fitted exactly once at startup and drifted stale
        #      for the bot's whole lifetime (refit_interval_bars was dead
        #      config); the backtester refits every walk-forward window.
        refit_interval = config.HMM.get("refit_interval_bars", 0)
        if refit_interval and state.bar_count % refit_interval == 0:
            self._refit_models(ticker, state)

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

        # 4 — Only act on a stable (confirmed) regime unless explicitly
        # disabled in config.  The pinned trend-core profile can safely fall
        # back to the last stable regime while the HMM warms up.
        if regime_idx < 0:
            if config.HMM.get("required", True):
                decision["action"] = "waiting_for_stable_regime"
                return decision
            regime_idx = state.last_stable_regime if state.last_stable_regime >= 0 else 0
            if not regime_label or regime_label == "Unknown":
                regime_label = state.last_stable_label or "Unknown"
            if len(proba) == 0:
                proba = pd.Series([1.0]).to_numpy()
        state.last_stable_regime = regime_idx
        state.last_stable_label = regime_label

        # 5 — Strategy signal (trend filter, vol targeting and the drift
        #     check all need causal inputs derived from the price history)
        vol_z = float(feats.iloc[-1].get("volume_zscore_21d", 0.0))
        closes = state.history["close"]
        price = float(bar.get("close", closes.iloc[-1]))
        equity = self._current_equity()
        current_qty = self._position_qty(ticker)
        current_weight = (current_qty * price / equity) if equity > 0 else 0.0
        strat_signal = state.orchestrator.evaluate(
            regime_index=regime_idx, regime_label=regime_label,
            proba=proba, high_uncertainty=high_unc, volume_zscore=vol_z,
            current_weights={ticker: current_weight},
            current_vol=realised_vol_from_close(closes),
            trend_confirmed=is_trend_confirmed(closes),
        )

        # 10 — Circuit-breaker check FIRST (risk has veto power).
        #     Roll the daily anchor when a bar from a new trading day
        #     arrives: the "-2%/-3% single-day" breakers must reference
        #     yesterday's close, not the equity at bot startup (start_new_day
        #     used to be called exactly once, at startup — so after a few
        #     weeks any cumulative -3% would flatten the book for good).
        try:
            bar_day = pd.Timestamp(state.history.index[-1]).date()
        except (TypeError, ValueError):
            bar_day = None
        if bar_day is not None and bar_day != self._risk_day:
            if self._risk_day is not None:
                self._risk.end_of_day(equity)   # feed weekly-loss breaker
            self._risk.start_new_day(equity)
            self._risk_day = bar_day

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

        # 6 — Sizing → ABSOLUTE target position, then delta vs holding.
        #     target_weight IS the fraction of equity to deploy; the
        #     orchestrator has already applied the exposure cap inside the
        #     target (so drift trigger and sizing agree).  The shared helper
        #     turns it into a share count with the circuit-breaker factor
        #     folded in — the backtester uses the exact same helper so live
        #     and backtest sizing cannot diverge.  (Submitting the full size
        #     every bar — as this loop once did — silently accumulates
        #     positions; only the DELTA may trade.)
        target_weight = sum(strat_signal.target_weights.values())

        # Portfolio selector gate:
        # The live loop is still ticker-driven, but this gate applies the same
        # decorrelation selector used by the portfolio backtester. If the
        # current ticker is rejected by the portfolio selector, its target is
        # forced to zero. If a position is already open, force a rebalance so
        # the duplicate exposure can be reduced.
        portfolio_forced_rebalance = False
        try:
            from core.universe import build_views
            from core.selector import select_decorrelated_views

            histories = {
                asset: st.history
                for asset, st in self._states.items()
                if st.history is not None and not st.history.empty
            }
            trend_states = {
                asset: is_trend_confirmed(st.history["close"])
                for asset, st in self._states.items()
                if st.history is not None and not st.history.empty
            }

            views = build_views(histories, trend_states)
            selected_views = select_decorrelated_views(views, histories)
            selected_tickers = {v.ticker for v in selected_views}

            decision["portfolio_selected"] = ticker in selected_tickers
            decision["portfolio_selected_tickers"] = sorted(selected_tickers)

            if ticker not in selected_tickers:
                if target_weight:
                    logger.info(
                        "Portfolio selector suppresses %s target. selected=%s",
                        ticker,
                        sorted(selected_tickers),
                    )
                target_weight = 0.0
                portfolio_forced_rebalance = current_qty != 0
        except Exception as exc:
            logger.warning("Portfolio selector gate failed for %s: %s", ticker, exc)

        candidate_weights = {}
        for asset in self._states:
            if asset == ticker:
                candidate_weights[asset] = target_weight
            else:
                qty = self._position_qty(asset)
                if qty:
                    price_for_asset = self._states[asset].history["close"].iloc[-1]
                    candidate_weights[asset] = (qty * float(price_for_asset) / equity) if equity > 0 else 0.0
                else:
                    candidate_weights[asset] = 0.0
        candidate_weights[ticker] = target_weight
        book_validation = self._risk.validate_book(candidate_weights)
        if not book_validation.approved:
            decision["action"] = "rejected_by_risk"
            decision["reason"] = book_validation.reason
            logger.info("Portfolio risk rejected %s target: %s", ticker, book_validation.reason)
            return decision

        target_qty = int(shares_for_target_weight(
            target_weight, price, equity,
            cb_scaling=self._risk.size_scaling_factor(),
        ))
        delta = target_qty - current_qty

        # 7 — Trade only on a rebalance trigger (regime change / drift /
        #     staleness) and only while the exchange is open; a market
        #     order placed after hours would just queue until the next open.
        if not strat_signal.should_rebalance and not portfolio_forced_rebalance:
            decision["action"] = "hold"
            decision["reason"] = "no_rebalance_trigger"
        elif delta == 0:
            decision["action"] = "no_change"
        elif config.BROKER.get("trade_only_when_open", True) and not self._market_is_open():
            decision["action"] = "skipped_market_closed"
            logger.info(
                "Market closed — %s decision observed but not submitted "
                "(would-be delta=%+d @ %.2f).", ticker, delta, price,
            )
        elif delta > 0:
            account = self._safe_call(self._client.get_account) or {}
            buying_power = float(account.get("buying_power", equity))
            validation = self._risk.validate_order(
                ticker, delta, price, equity, buying_power,
                proposed_leverage=strat_signal.effective_leverage,
                regime_label=regime_label,
            )
            if validation.approved:
                self._submit(ticker, int(validation.approved_qty), price,
                             regime_label, strat_signal.confidence, side="buy",
                             bar_date=bar_day)
                decision["action"] = "order_submitted"
                decision["side"] = "buy"
                decision["qty"] = int(validation.approved_qty)
            else:
                decision["action"] = "rejected_by_risk"
                decision["reason"] = validation.reason
                logger.info("Risk rejected %s order: %s", ticker, validation.reason)
        else:
            # delta < 0 — reducing risk needs no order validation
            self._submit(ticker, int(-delta), price,
                         regime_label, strat_signal.confidence, side="sell",
                         bar_date=bar_day)
            decision["action"] = "order_submitted"
            decision["side"] = "sell"
            decision["qty"] = int(-delta)

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

    def _refit_models(self, ticker: str, state: TickerState) -> None:
        """
        Refit the feature scaler + HMM on the trailing history (mirrors the
        backtester's per-window retraining) and warm the forward state by
        replaying recent observations so the stability filter has a
        confirmed regime immediately — without the replay, every refit
        would block trading for the 3-bar confirmation warm-up.

        On any failure the previous fitted models stay in place: a failed
        refit must never take down a working pipeline.
        """
        try:
            train_df = state.history.iloc[-max(config.HMM["min_history_bars"], 252):]
            fe = FeatureEngineer()
            feats = fe.fit_transform(train_df)
            engine = HMMEngine(
                n_iter=config.HMM["n_iter"],
                random_state=config.HMM["random_state"],
                min_history_bars=min(config.HMM["min_history_bars"], len(feats)),
                refit_interval_bars=config.HMM["refit_interval_bars"],
            )
            engine.fit(feats)
            # Replay the tail so confirmed-regime + flicker state are warm.
            for row in feats.iloc[-20:].values:
                engine.update(row)
        except Exception as exc:
            logger.warning("Model refit failed for %s — keeping previous "
                           "models: %s", ticker, exc)
            return
        state.feature_engineer = fe
        state.engine = engine
        logger.info("Refit HMM for %s on %d bars: k=%d, regime=%s",
                    ticker, len(feats), engine.n_components,
                    engine.current_regime_label())

    def _attempt_resume(self, min_probe_interval: float = 60.0) -> bool:
        """
        While paused, probe broker connectivity (rate-limited) and resume
        trading once it is back.  Returns True if trading was resumed.
        """
        now = time.time()
        if now - getattr(self, "_last_resume_probe", 0.0) < min_probe_interval:
            return False
        self._last_resume_probe = now
        try:
            clock = self._client.get_clock() if self._client else None
        except Exception as exc:
            logger.warning("Resume probe failed — staying paused: %s", exc)
            return False
        if clock is None:
            return False
        self._paused = False
        logger.info("Broker connectivity restored — resuming trading.")
        self._alert("Trading resumed after pause (connectivity restored).",
                    SEVERITY_WARNING, key="resume")
        return True

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

    def _submit(self, ticker, qty, price, regime, confidence, side="buy",
                bar_date=None) -> None:
        # Idempotency: one decision per ticker per bar.  If a retry / stale
        # loop ever re-submits the same decision, the broker rejects the
        # duplicate client_order_id instead of silently doubling the
        # position (this happened: 126 duplicate orders in 40 minutes).
        coid = f"rt-{ticker}-{bar_date}-{side}" if bar_date is not None else None
        try:
            oid = self._executor.submit_order(
                ticker, qty, side, order_type="market", client_order_id=coid,
            )
            if not oid:
                self._handle_order_rejection(ticker, "broker returned no order id")
                return
            self._orders_submitted += 1
        except Exception as exc:
            self._handle_order_rejection(ticker, str(exc))
            return
        # Wait briefly for the fill so the position snapshot the NEXT
        # decision diffs against is fresh; a still-working order after the
        # timeout is surfaced instead of silently re-traded.
        try:
            self._executor.await_fills([oid], timeout=15)
        except TimeoutError:
            self._alert(f"Order {oid} for {ticker} not filled within 15s — "
                        f"position snapshot may lag.", SEVERITY_WARNING,
                        key=f"slow_fill_{ticker}")
        except Exception as exc:
            logger.warning("await_fills failed for %s: %s", oid, exc)

    def _position_qty(self, ticker: str) -> int:
        """Current share count held for `ticker` (0 when flat/unknown)."""
        if self._positions is None:
            return 0
        try:
            pos = self._positions.get_positions().get(ticker)
            return int(pos.qty) if pos else 0
        except Exception:
            return 0

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

    def _market_is_open(self, ttl: float = 30.0) -> bool:
        """
        Return True if the exchange is currently open.

        The result is cached for `ttl` seconds so a single loop iteration over
        many tickers does not hammer the clock endpoint.  If the clock call
        cannot be resolved we fall back to the last known value (or True on the
        very first call) so a transient failure never silently freezes trading.
        """
        now = time.time()
        if self._market_open_cache is not None and now - self._market_check_ts < ttl:
            return self._market_open_cache
        clock = self._safe_call(self._client.get_clock) if self._client else None
        if clock is None:
            return True if self._market_open_cache is None else self._market_open_cache
        self._market_open_cache = bool(clock.get("is_open", False))
        self._market_check_ts = now
        self._market_status = "open" if self._market_open_cache else "closed"
        return self._market_open_cache

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
        """
        Append one bar, deduplicating on the timestamp index: a re-delivered
        bar (same timestamp) REPLACES the stored row rather than duplicating
        it.  Duplicated rows previously turned every rolling feature into
        garbage (ret_1d = 0, collapsed vol) once the poll loop re-delivered
        the latest bar each iteration.
        """
        row = bar.to_frame().T
        row.columns = [c.lower() for c in row.columns]
        out = pd.concat([history, row])
        return out[~out.index.duplicated(keep="last")].sort_index()

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

# Exit codes — meaningful to a supervisor (systemd) so it can distinguish a
# transient crash (restart) from a state that needs a human (do NOT restart).
EXIT_OK          = 0
EXIT_STARTUP_ERR = 1   # transient/config startup failure → safe to retry
EXIT_HALTED      = 3   # risk HALT lock present → needs manual review, NO restart


def _halt_lock_present() -> bool:
    """True when the risk-manager HALT lock file exists on disk."""
    try:
        from pathlib import Path
        return Path(config.RISK["lock_file_path"]).exists()
    except Exception:
        return False


def run_live() -> int:
    configure_logging()
    # A present HALT lock means the -drawdown breaker fired and a human must
    # review before resuming. Return a DISTINCT code so a Restart=always
    # supervisor stops instead of restart-looping into the same wall.
    if _halt_lock_present():
        logger.critical("Risk HALT lock present (%s) — refusing to start. "
                        "Review the incident and delete the file to resume.",
                        config.RISK["lock_file_path"])
        return EXIT_HALTED
    system = TradingSystem()
    if not system.startup():
        # startup() also refuses on a lock that appeared between the check
        # above and now; surface that as HALTED, everything else as retryable.
        if _halt_lock_present():
            return EXIT_HALTED
        logger.error("Startup failed — exiting.")
        return EXIT_STARTUP_ERR
    try:
        system.run()
    except KeyboardInterrupt:
        system.shutdown(reason="keyboard interrupt")
    return EXIT_OK


def run_once_daily() -> int:
    """
    One decision cycle for every ticker, then exit — the entry point for a
    scheduled once-a-day run (systemd timer).  This is the natural cadence
    for a DAILY-bar system: startup() rebuilds all state from scratch
    (history, HMM, position sync, stale-order cancel), one poll cycle makes
    at most one decision per ticker, then the process shuts down cleanly and
    the host can idle until tomorrow.

    Returns the same supervisor-friendly codes as run_live(): EXIT_HALTED
    (3) when the risk lock is present so the timer's next fire is a harmless
    no-op instead of trading into a halt that needs a human.
    """
    configure_logging()
    if _halt_lock_present():
        logger.critical("Risk HALT lock present (%s) — skipping daily run. "
                        "Review the incident and delete the file to resume.",
                        config.RISK["lock_file_path"])
        return EXIT_HALTED
    system = TradingSystem()
    if not system.startup():
        if _halt_lock_present():
            return EXIT_HALTED
        logger.error("Startup failed — exiting.")
        return EXIT_STARTUP_ERR
    # One poll cycle (no inter-iteration sleep); run() shuts itself down when
    # the iteration budget is spent.
    system.run(max_iterations=1, poll_interval=0)
    return EXIT_HALTED if _halt_lock_present() else EXIT_OK


def run_backtest() -> int:
    configure_logging()
    from core.portfolio_backtester import PortfolioBacktester
    from data.market_data import MarketDataFeed

    tickers = config.TICKERS or ["SPY"]
    feed = MarketDataFeed()

    histories = {}
    for ticker in tickers:
        data = feed.get_training_data(ticker, years=4.0)
        if data is None or data.empty:
            logger.warning("Skipping %s: no training data returned.", ticker)
            continue
        histories[ticker] = data

    if not histories:
        logger.error("No training data available for portfolio backtest.")
        return EXIT_STARTUP_ERR

    bt = PortfolioBacktester(histories=histories)
    result = bt.run()

    if result.returns.empty:
        logger.error("Portfolio backtest produced no returns: %s", result.metadata)
        return EXIT_STARTUP_ERR

    total_return = (result.equity_curve.iloc[-1] / result.initial_capital) - 1.0
    max_drawdown = (result.equity_curve / result.equity_curve.cummax() - 1.0).min()
    avg_gross = result.weights.abs().sum(axis=1).mean() if not result.weights.empty else 0.0
    max_gross = result.weights.abs().sum(axis=1).max() if not result.weights.empty else 0.0

    print("Portfolio backtest")
    print("==================")
    print(f"Tickers:        {sorted(histories.keys())}")
    print(f"Initial equity: ${result.initial_capital:,.2f}")
    print(f"Final equity:   ${result.equity_curve.iloc[-1]:,.2f}")
    print(f"Total return:   {total_return:.2%}")
    print(f"Max drawdown:   {max_drawdown:.2%}")
    print(f"Avg gross exp.: {avg_gross:.2%}")
    print(f"Max gross exp.: {max_gross:.2%}")
    print("")
    print("Last target weights:")
    print(result.weights.tail(1).T.to_string(header=False))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="regime_trader")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backtest", action="store_true",
                      help="run a historical backtest instead of live trading")
    mode.add_argument("--once", action="store_true",
                      help="run ONE decision cycle for every ticker then exit "
                           "(scheduled once-a-day mode; see deploy/)")
    args = parser.parse_args()
    if args.backtest:
        return run_backtest()
    if args.once:
        return run_once_daily()
    return run_live()


if __name__ == "__main__":
    raise SystemExit(main())
