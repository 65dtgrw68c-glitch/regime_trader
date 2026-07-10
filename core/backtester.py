"""
Backtester — walk-forward simulation with zero look-ahead bias.

This is NOT an in-sample optimisation.  The dataset is sliced into rolling
windows; the HMM and feature scaler are fit ONLY on each in-sample training
window, then evaluated on the immediately following out-of-sample window
using bar-by-bar forward inference.  The window then rolls forward.

Walk-forward structure (configurable in settings/config.BACKTEST)
----------------------------------------------------------------
    train_window = 252 bars  (~1 year, in-sample)
    test_window  = 126 bars  (~6 months, out-of-sample)
    step         = test_window  (non-overlapping OOS windows)

Pipeline per bar (identical ordering to the live loop)
------------------------------------------------------
    FeatureEngineer.transform  →  HMMEngine.update  →  RegimeOrchestrator
        →  RiskManager (sizing + circuit breakers)  →  simulated fill

Realism
-------
* Next-open execution: a decision made on bar i's completed close fills
  at bar i+1's OPEN (a same-close fill is impossible live and hides the
  overnight gap on trend-flip days).
* Slippage and commission are charged against equity on every fill.
* Idle cash earns a flat T-bill-like yield (BACKTEST["cash_yield_annual"]).
* All position sizing flows through the RiskManager (caps + breakers).
* Circuit breakers can flatten the book mid-window.

Benchmarks (computed over the same OOS period)
----------------------------------------------
    1. Buy & Hold
    2. 200-day SMA trend following
    3. Random entry with the SAME risk rules (isolates HMM edge)

Stress testing
--------------
    Synthetic single-day crashes (-10% to -15%) injected at random bars to
    observe drawdown and circuit-breaker behaviour.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
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
from settings import config

logger = logging.getLogger(__name__)

# Trailing window fed to the per-bar feature computation.  Matches the live
# loop's history cap (main._MAX_HISTORY_BARS): every rolling feature needs at
# most ~221 bars and the trend SMA 200, so results are identical to the full
# expanding window — but the per-bar cost stays O(1), which is what makes
# multi-decade (7000+ bar) structural runs feasible at all.
_FEATURE_WINDOW_BARS = 800


# ===========================================================================
# Result containers
# ===========================================================================

@dataclass
class WalkForwardSplit:
    """One in-sample / out-of-sample window pair (integer index slices)."""
    train_start: int
    train_end:   int   # exclusive
    test_start:  int   # == train_end (OOS begins right after training)
    test_end:    int   # exclusive

    def as_tuple(self) -> tuple[slice, slice]:
        return (slice(self.train_start, self.train_end),
                slice(self.test_start, self.test_end))


@dataclass
class BacktestResult:
    """Everything performance.py needs to build its report."""
    returns:        pd.Series            # per-bar OOS strategy returns
    equity_curve:   pd.Series            # cumulative equity over OOS bars
    trade_log:      pd.DataFrame         # one row per fill
    regime_labels:  pd.Series            # confirmed regime label per OOS bar
    confidence:     pd.Series            # HMM confidence per OOS bar
    splits:         list[WalkForwardSplit]
    benchmark_returns: dict[str, pd.Series] = field(default_factory=dict)
    initial_capital:  float = 0.0
    metadata:         dict = field(default_factory=dict)


# ===========================================================================
# Backtester
# ===========================================================================

class Backtester:
    """Walk-forward backtesting engine."""

    def __init__(
        self,
        ticker: str = "ASSET",
        train_window: int = 252,
        test_window: int = 126,
        initial_capital: Optional[float] = None,
        slippage: Optional[float] = None,
        commission: Optional[float] = None,
        cash_yield_annual: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        random_seed: int = 42,
        risk_manager: Optional[RiskManager] = None,
        use_trend_filter: bool = True,
        strategy_overrides: Optional[dict] = None,
        risk_overrides: Optional[dict] = None,
        cash_yield_series: Optional[pd.Series] = None,
    ) -> None:
        """
        use_trend_filter   : feed is_trend_confirmed() into the orchestrator
                             (tiers with require_trend_confirmation go to
                             cash below the SMA).  False = filter off.
        strategy_overrides : extra kwargs forwarded to RegimeOrchestrator
                             (e.g. {"vol_target": 0.10,
                                    "rebalance_max_bars": 5}) — the hook
                             scripts/run_experiments.py uses for A/B runs.
        risk_overrides     : keys merged over config.RISK for the per-window
                             RiskManager (e.g. {"cb_daily_enabled": False})
                             so risk-layer settings can be A/B-tested with
                             the same walk-forward machinery.  Ignored when
                             an explicit risk_manager is injected.
        cash_yield_series  : optional Series of ANNUALISED risk-free yields
                             (decimals, DatetimeIndex, e.g. ^IRX/100) used
                             for the idle-cash credit.  Dates are matched by
                             calendar day and forward-filled; bars before
                             the first yield observation fall back to the
                             flat cash_yield_annual.  None = flat rate.
        """
        self.ticker        = ticker
        self.train_window  = train_window
        self.test_window   = test_window
        self.initial_capital = (
            initial_capital if initial_capital is not None
            else config.BACKTEST["initial_capital"]
        )
        self.slippage   = slippage   if slippage   is not None else config.BACKTEST["slippage"]
        self.commission = commission if commission is not None else config.BACKTEST["commission"]
        self.cash_yield_annual = (
            cash_yield_annual if cash_yield_annual is not None
            else config.BACKTEST.get("cash_yield_annual", 0.0)
        )
        self.cash_yield_series = cash_yield_series
        # Per-bar daily yield aligned to the current run's data; built in run().
        self._daily_yield: Optional[pd.Series] = None
        # Per-trade protective exits, simulated intraday against the bar's
        # low/high (0.0 = disabled).  A long exits at entry*(1-sl) when the
        # LOW breaches it, at entry*(1+tp) when the HIGH does; a gap through
        # the level fills at the open (never at the untouchable level), and
        # when both trigger inside one bar the STOP is assumed first
        # (conservative).  Exits happen mid-bar; the normal decision logic
        # may re-enter at the next open, so the realistic stop-whipsaw cost
        # is fully modelled.
        self.stop_loss_pct = (
            stop_loss_pct if stop_loss_pct is not None
            else config.RISK.get("stop_loss_pct", 0.0)
        )
        self.take_profit_pct = (
            take_profit_pct if take_profit_pct is not None
            else config.RISK.get("take_profit_pct", 0.0)
        )
        self.random_seed = random_seed
        self._rng = np.random.default_rng(random_seed)
        self._risk_manager = risk_manager
        self.use_trend_filter = use_trend_filter
        self.strategy_overrides = dict(strategy_overrides or {})
        self.risk_overrides = dict(risk_overrides or {})

        self._result: Optional[BacktestResult] = None

    # ------------------------------------------------------------------
    # Walk-forward window construction
    # ------------------------------------------------------------------

    def walk_forward_splits(self, n_bars: int) -> list[WalkForwardSplit]:
        """
        Build non-overlapping OOS windows.  Each split trains on
        `train_window` bars and tests on the next `test_window` bars.
        The OOS windows tile the post-warmup region with no gaps/overlap.
        """
        splits: list[WalkForwardSplit] = []
        train_end = self.train_window
        while train_end + 1 <= n_bars:
            test_start = train_end
            test_end   = min(test_start + self.test_window, n_bars)
            if test_end <= test_start:
                break
            splits.append(WalkForwardSplit(
                train_start=train_end - self.train_window,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ))
            if test_end >= n_bars:
                break
            train_end += self.test_window   # roll forward by one OOS window
        return splits

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, ohlcv: pd.DataFrame, inject_stress: bool = False) -> BacktestResult:
        """
        Execute the full walk-forward backtest.

        Parameters
        ----------
        ohlcv : DataFrame with open/high/low/close/volume and a DatetimeIndex.
        inject_stress : if True, inject synthetic crash events before running.
        """
        data = ohlcv.copy()
        data.columns = [c.lower() for c in data.columns]
        if inject_stress:
            data = self.inject_stress_events(data)
        self._daily_yield = self._build_daily_yield(data.index)

        n_bars = len(data)
        splits = self.walk_forward_splits(n_bars)
        if not splits:
            raise ValueError(
                f"Not enough data ({n_bars} bars) for one walk-forward split "
                f"(need >= {self.train_window + 1})."
            )

        # OOS accumulators
        oos_index:   list = []
        oos_returns: list[float] = []
        oos_regimes: list[str] = []
        oos_conf:    list[float] = []
        trades:      list[dict] = []

        equity = self.initial_capital

        for split in splits:
            equity = self._run_window(
                data, split, equity,
                oos_index, oos_returns, oos_regimes, oos_conf, trades,
            )

        # Assemble series
        ret_series  = pd.Series(oos_returns, index=oos_index, name="strategy")
        equity_curve = self.initial_capital * (1.0 + ret_series).cumprod()
        equity_curve.name = "equity"

        # Benchmarks over the same OOS span
        oos_close = data["close"].loc[oos_index]
        benchmarks = self._compute_benchmarks(data, oos_index, oos_close)

        result = BacktestResult(
            returns=ret_series,
            equity_curve=equity_curve,
            trade_log=pd.DataFrame(trades),
            regime_labels=pd.Series(oos_regimes, index=oos_index, name="regime"),
            confidence=pd.Series(oos_conf, index=oos_index, name="confidence"),
            splits=splits,
            benchmark_returns=benchmarks,
            initial_capital=self.initial_capital,
            metadata={
                "ticker": self.ticker,
                "train_window": self.train_window,
                "test_window": self.test_window,
                "n_splits": len(splits),
                "slippage": self.slippage,
                "commission": self.commission,
                "cash_yield_annual": self.cash_yield_annual,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "execution": "next_open",
                "stress_injected": inject_stress,
                "trend_filter": self.use_trend_filter,
                "strategy_overrides": dict(self.strategy_overrides),
                "risk_overrides": dict(self.risk_overrides),
                "max_position_size": config.RISK["max_position_size"],
            },
        )
        self._result = result
        return result

    # ------------------------------------------------------------------
    # Per-window simulation
    # ------------------------------------------------------------------

    def _run_window(
        self,
        data: pd.DataFrame,
        split: WalkForwardSplit,
        equity: float,
        oos_index: list,
        oos_returns: list,
        oos_regimes: list,
        oos_conf: list,
        trades: list,
    ) -> float:
        """
        Train on the in-sample slice, then walk the OOS slice bar by bar.

        Look-ahead guarantee: the HMM and scaler see ONLY data with index
        < split.test_start.  OOS bars are fed one at a time via .update().
        """
        train_df = data.iloc[split.train_start:split.train_end]

        # ── Fit feature scaler + HMM on the training window ONLY ─────────
        fe = FeatureEngineer()
        try:
            train_feats = fe.fit_transform(train_df)
            engine = HMMEngine(
                n_iter=config.HMM["n_iter"],
                random_state=self.random_seed,
                min_history_bars=min(self.train_window // 2, len(train_feats)),
            )
            engine.fit(train_feats)
        except Exception as exc:
            logger.warning("Window train failed (%s) — skipping OOS span.", exc)
            # Still advance equity flat across the skipped OOS bars.
            for i in range(split.test_start, split.test_end):
                oos_index.append(data.index[i])
                oos_returns.append(0.0)
                oos_regimes.append("Unknown")
                oos_conf.append(0.0)
            return equity

        # The exposure cap lives inside the orchestrator target (not in share
        # sizing) so the drift check compares against what will actually be
        # held; strategy_overrides may still override it for experiments.
        orch_kwargs = {
            "max_exposure": config.RISK["max_position_size"],
            **self.strategy_overrides,
        }
        orchestrator = RegimeOrchestrator(tickers=[self.ticker], **orch_kwargs)
        # Isolate the circuit-breaker lock file: a backtest that hits the
        # -10% HALT breaker must NEVER write the live bot's RISK_HALT.lock
        # (that would halt real trading), nor leak halted state into the next
        # backtest run or test. Each run gets its own throwaway lock path.
        if self._risk_manager is not None:
            risk = self._risk_manager
        else:
            lock_path = Path(tempfile.gettempdir()) / f"bt_halt_{uuid.uuid4().hex}.lock"
            lock_path.unlink(missing_ok=True)
            risk_cfg = {**config.RISK, **self.risk_overrides}
            risk = RiskManager(cfg=risk_cfg, lock_file_path=str(lock_path))

        # We need features for OOS bars too, computed from data up to each
        # bar WITHOUT refitting the scaler (transform only).  To stay strictly
        # causal we recompute features on the expanding window [0 : i] and take
        # the last row — only past data ever enters a given bar's features.
        position_shares = 0.0
        entry_price     = 0.0
        # Execution timing: a decision made on bar i's COMPLETED close cannot
        # fill at that same close — the order fills at bar i+1's OPEN.  The
        # decision is parked here and executed at the top of the next
        # iteration.  (Same-close fills gave the backtest an overnight-gap
        # advantage the live bot can never have, systematically flattering
        # trend flips on crash days.)
        pending: Optional[dict] = None

        for i in range(split.test_start, split.test_end):
            bar_open  = float(data["open"].iloc[i])
            bar_close = float(data["close"].iloc[i])
            prev_close = float(data["close"].iloc[i - 1]) if i > 0 else bar_open

            # ── New trading day: anchor the DAILY breakers to yesterday's
            #    close. Anchoring once per window (as this used to) made the
            #    "-2%/-3% single-day" breakers fire on CUMULATIVE window
            #    losses and then kept the book flat for the rest of the
            #    window — systematically selling lows. One bar = one day.
            risk.start_new_day(equity)

            # ── 1) Execute the pending decision at THIS bar's open ───────
            pos_before = position_shares
            trade_cost = 0.0
            if pending is not None:
                delta = pending["target"] - position_shares
                if abs(delta) > 1e-9:
                    fill_price = self._fill_price(bar_open, delta)
                    # Slippage must hit the equity curve, not just the trade
                    # log: the adverse fill-vs-open difference is a realised
                    # cash cost.
                    slip_cost = abs(delta) * abs(fill_price - bar_open)
                    trade_cost = abs(delta) * fill_price * self.commission + slip_cost
                    trades.append({
                        "timestamp":  data.index[i],
                        "ticker":     self.ticker,
                        "side":       "BUY" if delta > 0 else "SELL",
                        "qty":        abs(delta),
                        "fill_price": fill_price,
                        "regime":     pending["regime"],
                        "confidence": pending["confidence"],
                        "cb_level":   risk.circuit_breaker_level().name,
                        "exit_reason": "",
                    })
                    # Only a BUY re-anchors the entry price; a partial
                    # reduction must not move the stop/TP reference (and
                    # previously set it to the SELL fill).
                    if delta > 0:
                        entry_price = fill_price
                    position_shares = pending["target"]
                pending = None

            # ── 1b) Intraday protective exit (stop-loss / take-profit),
            #    simulated against the bar's low/high.  Gap through the
            #    level → fill at the open; stop assumed before take-profit
            #    when both trigger inside one bar (conservative). ──────────
            intraday_pnl = position_shares * (bar_close - bar_open)
            if position_shares > 0 and entry_price > 0 and (
                self.stop_loss_pct > 0 or self.take_profit_pct > 0
            ):
                exit_reason, exit_base = None, 0.0
                if self.stop_loss_pct > 0:
                    stop_lvl = entry_price * (1.0 - self.stop_loss_pct)
                    if float(data["low"].iloc[i]) <= stop_lvl:
                        exit_reason, exit_base = "stop_loss", min(bar_open, stop_lvl)
                if exit_reason is None and self.take_profit_pct > 0:
                    tp_lvl = entry_price * (1.0 + self.take_profit_pct)
                    if float(data["high"].iloc[i]) >= tp_lvl:
                        exit_reason, exit_base = "take_profit", max(bar_open, tp_lvl)
                if exit_reason is not None:
                    fill_price = self._fill_price(exit_base, -position_shares)
                    slip_cost = position_shares * abs(fill_price - exit_base)
                    trade_cost += (
                        position_shares * fill_price * self.commission + slip_cost
                    )
                    trades.append({
                        "timestamp":  data.index[i],
                        "ticker":     self.ticker,
                        "side":       "SELL",
                        "qty":        position_shares,
                        "fill_price": fill_price,
                        "regime":     "n/a",
                        "confidence": 0.0,
                        "cb_level":   risk.circuit_breaker_level().name,
                        "exit_reason": exit_reason,
                    })
                    # Position rode open → exit level, cash for the rest of
                    # the bar; the close-side decision below may re-enter at
                    # the NEXT open (the realistic stop-whipsaw round trip).
                    intraday_pnl = position_shares * (exit_base - bar_open)
                    position_shares = 0.0
                    entry_price = 0.0

            # ── 2) Mark-to-market: old position over the overnight gap,
            #    new position over the intraday move, plus cash yield
            #    (per-bar rate from the aligned T-bill series). ────────────
            daily_yield = (
                float(self._daily_yield.iloc[i])
                if self._daily_yield is not None else 0.0
            )
            pnl = (
                pos_before * (bar_open - prev_close)
                + intraday_pnl
                - trade_cost
            )
            if daily_yield and equity > 0:
                cash_frac = 1.0 - (pos_before * prev_close / equity)
                pnl += equity * float(np.clip(cash_frac, 0.0, 1.0)) * daily_yield
            equity_after = equity + pnl

            # ── Causal features: trailing window ending at bar i ─────────
            window_df = data.iloc[max(0, i + 1 - _FEATURE_WINDOW_BARS): i + 1]
            try:
                feats = fe.transform(window_df)
                obs   = feats.iloc[-1].values
            except Exception:
                obs = None

            if obs is not None:
                engine.update(obs)
            regime_idx   = engine.current_regime()
            regime_label = engine.current_regime_label()
            proba        = engine.predict_proba_current()
            high_unc     = engine.high_uncertainty
            vol_z        = float(feats.iloc[-1].get("volume_zscore_21d", 0.0)) if obs is not None else 0.0

            # Causal strategy inputs from closes up to and including bar i:
            # trend state, raw realised vol, and the live portfolio weight
            # (all mirror what main.py feeds the orchestrator in live mode).
            closes_so_far = window_df["close"]
            trend_ok = (
                is_trend_confirmed(closes_so_far)
                if self.use_trend_filter else None
            )
            current_vol = realised_vol_from_close(closes_so_far)
            current_weight = (
                position_shares * bar_close / equity_after if equity_after > 0 else 0.0
            )

            signal = orchestrator.evaluate(
                regime_index=regime_idx,
                regime_label=regime_label,
                proba=proba,
                high_uncertainty=high_unc,
                volume_zscore=vol_z,
                current_weights={self.ticker: current_weight},
                current_vol=current_vol,
                trend_confirmed=trend_ok,
            )
            confidence = signal.confidence

            # ── Risk layer: update breakers on the new equity ────────────
            risk.update_equity(
                equity_after,
                open_positions={self.ticker: {"unrealised_pnl":
                    position_shares * (bar_close - entry_price)}},
                regime_label=regime_label,
            )

            # ── 3) Decide the target to fill at the NEXT bar's open ──────
            target_shares = self._target_shares(
                signal, risk, bar_close, equity_after, regime_label
            )

            # Trade only when the strategy calls for a rebalance — this is
            # what keeps turnover (and thus slippage + commission) low.
            execute_trade = signal.should_rebalance

            # Circuit-breaker flatten overrides any target AND the gate
            if risk.should_flatten() or risk.is_halted():
                target_shares = 0.0
                execute_trade = True

            if execute_trade and abs(target_shares - position_shares) > 1e-9:
                pending = {
                    "target":     target_shares,
                    "regime":     regime_label,
                    "confidence": confidence,
                }

            # ── Record OOS bar ───────────────────────────────────────────
            bar_total_ret = (equity_after - equity) / equity if equity else 0.0
            oos_index.append(data.index[i])
            oos_returns.append(bar_total_ret)
            oos_regimes.append(regime_label)
            oos_conf.append(confidence)
            equity = equity_after
            # Feed the rolling weekly-loss breaker with the daily close
            # (end_of_day existed but was never called anywhere).
            risk.end_of_day(equity)

        # Remove this run's throwaway HALT lock (only exists if the breaker
        # fired). Skip when the caller injected its own RiskManager.
        if self._risk_manager is None:
            Path(risk.lock_path).unlink(missing_ok=True)
        return equity

    # ------------------------------------------------------------------
    # Sizing / fills
    # ------------------------------------------------------------------

    def _target_shares(
        self,
        signal,
        risk: RiskManager,
        price: float,
        equity: float,
        regime_label: str,
    ) -> float:
        """
        Convert a strategy signal into a share count.

        `target_weight` (the sum of the signal's target weights) IS the
        fraction of equity to deploy — the vol-tier allocations already
        express this, and the orchestrator has already applied the portfolio
        exposure cap inside the target (so drift check and sizing agree).
        Only the RiskManager's circuit-breaker factor is folded in here.
        main.py uses the same helper so live and backtest sizing cannot
        diverge.
        """
        target_weight = sum(signal.target_weights.values())
        return shares_for_target_weight(
            target_weight,
            price,
            equity,
            cb_scaling=risk.size_scaling_factor(),
        )

    def _fill_price(self, mid_price: float, delta_shares: float) -> float:
        """Apply slippage: buys fill higher, sells fill lower."""
        direction = 1.0 if delta_shares > 0 else -1.0
        return mid_price * (1.0 + direction * self.slippage)

    # ------------------------------------------------------------------
    # Benchmarks
    # ------------------------------------------------------------------

    def _compute_benchmarks(
        self,
        data: pd.DataFrame,
        oos_index: list,
        oos_close: pd.Series,
    ) -> dict[str, pd.Series]:
        """Compute the three required benchmark return series over OOS bars."""
        return {
            "buy_and_hold":   self._bench_buy_and_hold(oos_close),
            "sma_200":        self._bench_sma_trend(data, oos_index),
            "random_entry":   self._bench_random_entry(data, oos_index),
        }

    @staticmethod
    def _bench_buy_and_hold(oos_close: pd.Series) -> pd.Series:
        """Hold the asset for the entire OOS span."""
        rets = oos_close.pct_change().fillna(0.0)
        rets.name = "buy_and_hold"
        return rets

    def _build_daily_yield(self, index: pd.Index) -> pd.Series:
        """
        Per-bar daily cash yield aligned to `index` (calendar-day matched,
        forward-filled).  Uses the injected annualised-yield series when
        given, otherwise the flat cash_yield_annual.
        """
        idx = pd.DatetimeIndex(index)
        if self.cash_yield_series is not None and len(self.cash_yield_series):
            ann = self.cash_yield_series.copy()
            ann.index = pd.DatetimeIndex(ann.index).normalize()
            ann = ann[~ann.index.duplicated(keep="last")].sort_index()
            aligned = ann.reindex(idx.normalize(), method="ffill")
            aligned = aligned.fillna(self.cash_yield_annual).clip(lower=0.0)
        else:
            aligned = pd.Series(self.cash_yield_annual, index=idx.normalize())
        daily = (1.0 + aligned.astype(float)) ** (1.0 / 252) - 1.0
        daily.index = idx
        return daily

    def _signal_bench_returns(self, data: pd.DataFrame, sig: pd.Series) -> pd.Series:
        """
        Per-bar returns for a long/flat signal series with the SAME
        execution timing as the strategy: the signal known at close i takes
        effect at open i+1.  Overnight into bar i is therefore held by
        sig[i-2]'s position, intraday of bar i by sig[i-1]'s.  Idle bars
        earn the per-bar cash yield.  Costless by design — benchmarks stay
        the ideal reference, but no longer enjoy a timing advantage.
        """
        close, open_ = data["close"], data["open"]
        pos_intra = sig.shift(1).fillna(0.0)
        pos_over  = sig.shift(2).fillna(0.0)
        r_over  = (open_ / close.shift(1) - 1.0).fillna(0.0)
        r_intra = (close / open_ - 1.0).fillna(0.0)
        yld = (
            self._daily_yield if self._daily_yield is not None
            else pd.Series(0.0, index=data.index)
        )
        return pos_over * r_over + pos_intra * r_intra + yld * (1.0 - pos_intra)

    def _bench_sma_trend(self, data: pd.DataFrame, oos_index: list) -> pd.Series:
        """Long when close > 200-day SMA (causal), else cash — next-open fills."""
        close = data["close"]
        sma = close.rolling(200).mean()
        sig = (close > sma).astype(float)
        bench = self._signal_bench_returns(data, sig).loc[oos_index]
        bench.name = "sma_200"
        return bench

    def _bench_random_entry(self, data: pd.DataFrame, oos_index: list) -> pd.Series:
        """
        Random long/flat entries (50/50) with the same execution timing and
        cash yield.  Isolates whether the HMM adds value beyond random
        timing.
        """
        rng = np.random.default_rng(self.random_seed + 1)
        sig = pd.Series(
            rng.integers(0, 2, len(data)).astype(float), index=data.index
        )
        bench = self._signal_bench_returns(data, sig).loc[oos_index]
        bench.name = "random_entry"
        return bench

    # ------------------------------------------------------------------
    # Stress testing
    # ------------------------------------------------------------------

    def inject_stress_events(
        self,
        data: pd.DataFrame,
        n_events: int = 3,
        min_drop: float = 0.10,
        max_drop: float = 0.15,
    ) -> pd.DataFrame:
        """
        Inject synthetic single-day crashes (-10% to -15%) at random bars.

        All bars from the crash onward are scaled down so the drop persists
        in the price path (a true regime shock, not a one-bar blip that
        instantly recovers).  Returns a NEW DataFrame.
        """
        df = data.copy()
        n = len(df)
        if n < 10:
            return df
        # Choose crash bars in the back 80% so there's history beforehand.
        candidates = np.arange(int(n * 0.2), n)
        chosen = self._rng.choice(
            candidates, size=min(n_events, len(candidates)), replace=False
        )
        for idx in sorted(chosen):
            drop = float(self._rng.uniform(min_drop, max_drop))
            factor = 1.0 - drop
            df.iloc[idx:, df.columns.get_loc("close")] *= factor
            df.iloc[idx:, df.columns.get_loc("open")]  *= factor
            df.iloc[idx:, df.columns.get_loc("high")]  *= factor
            df.iloc[idx:, df.columns.get_loc("low")]   *= factor
            logger.info("Injected -%.1f%% crash at bar %d (%s)",
                        drop * 100, idx, df.index[idx])
        df.attrs["stress_bars"] = sorted(int(c) for c in chosen)
        return df

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_returns(self) -> pd.Series:
        if self._result is None:
            raise RuntimeError("Call run() first.")
        return self._result.returns

    def get_trade_log(self) -> pd.DataFrame:
        if self._result is None:
            raise RuntimeError("Call run() first.")
        return self._result.trade_log
