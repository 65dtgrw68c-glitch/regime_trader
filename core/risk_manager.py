"""
Risk Manager — the independent, hardcoded risk-control layer.

ABSOLUTE VETO POWER
-------------------
This component sits below every AI/strategy decision in the system.  It
does NOT consult the HMM or any strategy; its rules are fixed constants
read from settings/config.py and cannot be overridden at runtime by other
components.  Every order must pass through `validate_order()` before it can
reach the broker.

Circuit breakers (hardcoded, non-negotiable)
--------------------------------------------
  -2% single day   → halve all position sizes        (CBLevel.HALVE)
  -3% single day   → close ALL positions immediately  (CBLevel.FLATTEN)
  -5% in a week    → resize all remaining positions   (CBLevel.WEEKLY_RESIZE)
  -10% from peak   → STOP the bot, write a lock file   (CBLevel.HALT)

The HALT breaker writes a lock file to disk describing what happened.  The
bot refuses to start while that file exists; the user must delete it
manually after reviewing the incident.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Mapping, Optional

import numpy as np

from settings import config

logger = logging.getLogger(__name__)


# `compose_book()` (core/sleeves.py) rounds every weight to 6 decimals AFTER
# scaling to the gross cap, so a book that lands exactly on a cap can present
# here as e.g. 1.0000010000000001 instead of 1.0 — a rounding artifact, not a
# real breach. The float-comparison tolerance below has to be at least as
# coarse as that rounding (worst case ~0.5e-6 per position, several positions
# summed for the gross/class checks), or the book-level caps reject books the
# composer itself judged to be exactly at the cap. Order-level dollar/leverage
# checks below use their own, unrelated 1e-6 tolerance and are not affected.
_WEIGHT_CAP_EPS = 1e-5

# ===========================================================================
# Circuit-breaker severity ladder
# ===========================================================================

class CBLevel(IntEnum):
    """Ordered severity of the active circuit breaker (higher = worse)."""
    NONE          = 0
    HALVE         = 1   # -2% day: cut position sizes in half
    WEEKLY_RESIZE = 2   # -5% week: resize remaining positions down
    FLATTEN       = 3   # -3% day: close all positions now
    HALT          = 4   # -10% drawdown: stop the bot, write lock file


@dataclass
class OrderValidation:
    """Result of validating a single proposed order."""
    approved: bool
    reason:   str = ""
    # Size the risk manager will actually allow (may be reduced from request)
    approved_qty: float = 0.0


@dataclass
class RiskState:
    """Snapshot of the risk manager's internal state for logging/dashboard."""
    cb_level:        CBLevel
    halted:          bool
    day_start_equity: float
    peak_equity:     float
    current_equity:  float
    daily_return:    float
    drawdown:        float


# ===========================================================================
# Risk Manager
# ===========================================================================

class RiskManager:
    """
    Hardcoded risk controls with absolute veto power over all trading.

    Typical usage each bar:
        rm.update_equity(portfolio_value)        # refresh breakers
        if rm.is_halted(): ...                    # respect HALT
        qty = shares_for_target_weight(target_weight, price, equity,
                                       cb_scaling=rm.size_scaling_factor())
        v   = rm.validate_order(ticker, qty, price, portfolio_value,
                                buying_power, leverage, regime_label)
        if v.approved: submit(v.approved_qty)
    """

    def __init__(
        self,
        cfg: Optional[dict] = None,
        regime_leverage_caps: Optional[dict] = None,
        lock_file_path: Optional[str] = None,
        persist_state: bool = True,
    ) -> None:
        """
        persist_state : keep peak equity / daily-close history on disk so the
            drawdown and weekly breakers survive a process restart.  MUST stay
            True in production: the bot is deployed as a daily `--once` oneshot
            (deploy/regime-trader.service), i.e. a fresh process every morning.
            With in-memory-only state `startup()` re-anchored the peak to the
            CURRENT equity every day, so the measured drawdown was permanently
            0.00% and no drawdown breaker could ever fire — verified against a
            simulated -45% crash.  Backtests pass False (each window would
            otherwise inherit the previous run's peak, and it is per-bar disk
            I/O for nothing).
        """
        self._cfg = cfg or config.RISK
        self._regime_caps = regime_leverage_caps or getattr(
            config, "REGIME_LEVERAGE_CAPS", {}
        )
        self._lock_path = Path(lock_file_path or self._cfg["lock_file_path"])
        self._persist_state = bool(persist_state)
        self._state_path = self._lock_path.with_name(
            self._lock_path.stem + "_state.json"
        )

        # ── Equity tracking ──────────────────────────────────────────────
        self._day_start_equity: Optional[float] = None
        self._peak_equity:      Optional[float] = None
        self._current_equity:   Optional[float] = None
        # Rolling history of daily-close equities for the weekly breaker
        self._equity_history:   list[float] = []

        # ── Circuit-breaker state ────────────────────────────────────────
        self._cb_level: CBLevel = CBLevel.NONE
        self._halted:   bool     = self._lock_path.exists()

        # ── Price history for correlation checks: ticker -> [returns] ────
        self._price_history: dict[str, list[float]] = {}

        if self._persist_state:
            self._load_state()

    @property
    def lock_path(self) -> Path:
        """Path to this manager's circuit-breaker HALT lock file."""
        return self._lock_path

    @property
    def state_path(self) -> Path:
        """Path to the persisted peak-equity / equity-history file."""
        return self._state_path

    # ------------------------------------------------------------------
    # Cross-restart state
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Restore peak equity and daily-close history from a previous run."""
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except Exception as exc:
            logger.warning("Could not read risk state %s: %s — starting fresh.",
                           self._state_path, exc)
            return
        peak = data.get("peak_equity")
        if isinstance(peak, (int, float)) and peak > 0:
            self._peak_equity = float(peak)
        history = data.get("equity_history")
        if isinstance(history, list):
            self._equity_history = [
                float(x) for x in history if isinstance(x, (int, float))
            ]
        logger.info("Restored risk state: peak_equity=%.2f, %d daily closes.",
                    self._peak_equity or 0.0, len(self._equity_history))

    def _save_state(self) -> None:
        """Persist peak equity + daily-close history (best effort, never fatal)."""
        if not self._persist_state:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "peak_equity": self._peak_equity,
                "equity_history": self._equity_history,
                "updated_utc": datetime.now(timezone.utc).isoformat(),
            }))
            tmp.replace(self._state_path)   # atomic: never leave a torn file
        except Exception as exc:
            logger.warning("Could not persist risk state to %s: %s",
                           self._state_path, exc)

    # ------------------------------------------------------------------
    # Equity / circuit-breaker updates
    # ------------------------------------------------------------------

    def start_new_day(self, equity: float) -> None:
        """Call at the start of each trading day to anchor the daily baseline."""
        self._day_start_equity = float(equity)
        if self._peak_equity is None:
            self._peak_equity = float(equity)
        if self._current_equity is None:
            self._current_equity = float(equity)
        # Demote any intraday breaker (HALVE/FLATTEN) but never clear HALT.
        if self._cb_level in (CBLevel.HALVE, CBLevel.FLATTEN):
            self._cb_level = CBLevel.NONE

    def end_of_day(self, equity: float) -> None:
        """Record the closing equity for the rolling weekly-loss breaker."""
        self._equity_history.append(float(equity))
        lookback = self._cfg["weekly_lookback_days"] + 1
        if len(self._equity_history) > lookback:
            self._equity_history = self._equity_history[-lookback:]
        self._save_state()

    def update_equity(
        self,
        equity: float,
        open_positions: Optional[dict] = None,
        regime_label: str = "Unknown",
        market_note: str = "",
    ) -> CBLevel:
        """
        Update current equity and evaluate every circuit breaker.

        Returns the highest active CBLevel.  This is the heart of the
        risk layer — call it on every bar before sizing positions.
        """
        equity = float(equity)
        self._current_equity = equity

        if self._day_start_equity is None:
            self._day_start_equity = equity
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
            self._save_state()   # a new high must survive the next restart

        daily_ret = self._daily_return()
        drawdown  = self._drawdown()
        weekly_ret = self._weekly_return()

        level = CBLevel.NONE

        # The daily HALVE/FLATTEN breakers measure CLOSE-to-close equity on a
        # daily-bar system: they fire only after the loss is fully realised,
        # sell the low, and the next bar's drift trigger buys straight back.
        # Measured on the walk-forward grid they cost return with ZERO
        # drawdown benefit, so the pinned trend profile disables them via
        # cb_daily_enabled.  The weekly breaker and the max-drawdown HALT
        # (tail protection with manual review) always stay active.
        daily_enabled = self._cfg.get("cb_daily_enabled", True)

        # ── -2% single day → halve ───────────────────────────────────────
        if daily_enabled and daily_ret <= -self._cfg["cb_daily_halve_loss"]:
            level = max(level, CBLevel.HALVE)

        # ── -5% week → resize remaining positions down ───────────────────
        if weekly_ret is not None and weekly_ret <= -self._cfg["cb_weekly_resize_loss"]:
            level = max(level, CBLevel.WEEKLY_RESIZE)

        # ── -3% single day → flatten everything ──────────────────────────
        if daily_enabled and daily_ret <= -self._cfg["cb_daily_flatten_loss"]:
            level = max(level, CBLevel.FLATTEN)

        # ── -10% drawdown → HALT + lock file (terminal) ──────────────────
        if drawdown <= -self._cfg["cb_max_drawdown_halt"]:
            level = CBLevel.HALT
            if not self._halted:
                self._trigger_halt(
                    open_positions=open_positions or {},
                    regime_label=regime_label,
                    market_note=market_note,
                    daily_ret=daily_ret,
                    drawdown=drawdown,
                )

        # HALT is sticky — once halted, stay halted until lock file removed.
        if self._halted:
            level = CBLevel.HALT

        if level != self._cb_level:
            logger.warning(
                "Circuit breaker level change: %s → %s "
                "(daily=%.2f%% weekly=%s drawdown=%.2f%%)",
                self._cb_level.name, level.name,
                daily_ret * 100,
                f"{weekly_ret*100:.2f}%" if weekly_ret is not None else "n/a",
                drawdown * 100,
            )
        self._cb_level = level
        return level

    def size_scaling_factor(self) -> float:
        """
        Multiplier applied to all new position sizes based on the active
        circuit breaker.  FLATTEN/HALT → 0 (no new exposure).
        """
        if self._cb_level >= CBLevel.FLATTEN:
            return 0.0
        if self._cb_level == CBLevel.WEEKLY_RESIZE:
            return self._cfg["cb_weekly_resize_factor"]
        if self._cb_level == CBLevel.HALVE:
            return self._cfg["cb_halve_factor"]
        return 1.0

    # ------------------------------------------------------------------
    # Leverage enforcement
    # ------------------------------------------------------------------

    def max_leverage_for_regime(self, regime_label: str) -> float:
        """
        Return the leverage cap for a regime, reduced by any partial
        circuit breaker.  Falls back to the global RISK["max_leverage"].

        When RISK["use_regime_leverage_caps"] is False the per-regime caps
        are ignored entirely (global cap only).  The backtester never
        applied these caps, so leaving them active live made the demoted
        HMM a hard, never-validated entry gate: a (noisy) "Bear"/"Weak"
        label rejected every trend-core buy.
        """
        if self._cfg.get("use_regime_leverage_caps", True):
            cap = self._regime_caps.get(regime_label, self._cfg["max_leverage"])
        else:
            cap = self._cfg["max_leverage"]
        # Partial breakers shrink allowed leverage in step with sizing.
        cap *= self.size_scaling_factor()
        return cap

    # ------------------------------------------------------------------
    # Order validation (the gate every order must pass)
    # ------------------------------------------------------------------

    def _effective_per_name_cap(self) -> float:
        """Resolve the per-name exposure cap while keeping legacy aliases intact."""
        max_position_size = float(self._cfg.get("max_position_size", 1.0))
        per_name_cap = self._cfg.get("per_name_cap")
        if per_name_cap is None:
            return max_position_size
        return min(float(per_name_cap), max_position_size)

    def validate_book(self, target_weights: dict[str, float]) -> OrderValidation:
        """Validate the target-book weights before new orders are submitted."""
        if not target_weights:
            return OrderValidation(True, "empty book")

        gross = sum(abs(float(weight)) for weight in target_weights.values())
        gross_cap = self._cfg.get("gross_cap", self._cfg.get("max_leverage", 1.0))
        if gross > gross_cap + _WEIGHT_CAP_EPS:
            return OrderValidation(False, f"gross {gross:.2f} exceeds cap {gross_cap:.2f}")

        per_name_cap = self._effective_per_name_cap()
        for ticker, weight in target_weights.items():
            if abs(weight) > per_name_cap + _WEIGHT_CAP_EPS:
                return OrderValidation(
                    False,
                    f"per-name {ticker} weight {weight:.2f} exceeds cap {per_name_cap:.2f}",
                )

        # Economic exposure: a levered product carries more market risk than
        # its notional weight suggests, so the notional gross cap above cannot
        # see it (0.40 in a 2x ETF is 0.80 of market exposure).  Without this
        # second cap the book could be economically 2x levered while reporting
        # a gross of 1.0.
        economic_cap = self._cfg.get("economic_gross_cap")
        if economic_cap is not None:
            assets = getattr(config, "UNIVERSE", {}).get("assets", {})
            economic = sum(
                abs(float(weight)) * float(assets.get(ticker, {}).get("leverage", 1.0))
                for ticker, weight in target_weights.items()
            )
            if economic > float(economic_cap) + _WEIGHT_CAP_EPS:
                return OrderValidation(
                    False,
                    f"economic exposure {economic:.2f} exceeds cap "
                    f"{float(economic_cap):.2f}",
                )

        class_caps = self._cfg.get("class_caps", {})
        by_class: dict[str, float] = {}
        for ticker, weight in target_weights.items():
            meta = getattr(config, "UNIVERSE", {}).get("assets", {}).get(ticker, {})
            asset_class = meta.get("asset_class", "equity")
            by_class[asset_class] = by_class.get(asset_class, 0.0) + abs(float(weight))

        for asset_class, weight in by_class.items():
            cap = class_caps.get(asset_class)
            if cap is not None and weight > cap + _WEIGHT_CAP_EPS:
                return OrderValidation(
                    False,
                    f"class {asset_class} weight {weight:.2f} exceeds cap {cap:.2f}",
                )

        return OrderValidation(True, "approved")

    def clip_target_weight(
        self,
        ticker: str,
        target_weight: float,
        other_weights: Mapping[str, float],
    ) -> float:
        """
        Largest weight for `ticker` that keeps (this weight + `other_weights`
        held fixed) within every book-level cap: per-name, gross, economic and
        class.  `other_weights` may include a stale/zero entry for `ticker`
        itself; it is ignored.

        For a caller that can only move ONE ticker's position per call (the
        single-asset live path — main.TradingSystem.run_once), rejecting the
        whole book on a class/gross breach durably favours whichever ticker
        happened to claim the shared budget first: a later ticker's request
        is zeroed outright even when a SMALLER position would fit, and the
        budget it lost never comes back once its neighbour holds it. Clipping
        to whatever headroom remains gives every ticker its fair share of the
        shared budget regardless of arrival order — nobody is durably locked
        to zero as long as the book has room left.

        This system is long-only (shares_for_target_weight treats
        target_weight <= 0 as no position), so a negative input clips to 0
        rather than being preserved with its sign.
        """
        if target_weight <= 0:
            return 0.0
        others = {t: float(w) for t, w in other_weights.items() if t != ticker}
        room = float(target_weight)

        room = min(room, self._effective_per_name_cap())

        gross_cap = self._cfg.get("gross_cap", self._cfg.get("max_leverage", 1.0))
        other_gross = sum(abs(w) for w in others.values())
        room = min(room, max(0.0, gross_cap - other_gross))

        economic_cap = self._cfg.get("economic_gross_cap")
        assets = getattr(config, "UNIVERSE", {}).get("assets", {})
        if economic_cap is not None:
            leverage = float(assets.get(ticker, {}).get("leverage", 1.0))
            other_economic = sum(
                abs(w) * float(assets.get(t, {}).get("leverage", 1.0))
                for t, w in others.items()
            )
            if leverage > 0:
                room = min(room, max(0.0, (float(economic_cap) - other_economic) / leverage))

        class_caps = self._cfg.get("class_caps", {})
        asset_class = assets.get(ticker, {}).get("asset_class", "equity")
        cap = class_caps.get(asset_class)
        if cap is not None:
            other_class = sum(
                abs(w) for t, w in others.items()
                if assets.get(t, {}).get("asset_class", "equity") == asset_class
            )
            room = min(room, max(0.0, cap - other_class))

        return max(0.0, room)

    def validate_order(
        self,
        ticker: str,
        qty: float,
        price: float,
        portfolio_value: float,
        buying_power: float,
        proposed_leverage: float,
        regime_label: str = "Unknown",
        existing_returns: Optional[dict[str, np.ndarray]] = None,
        new_returns: Optional[np.ndarray] = None,
    ) -> OrderValidation:
        """
        Validate a proposed order against every hardcoded rule.

        Checks, in order:
          1. Not halted / no flatten breaker active
          2. Position size within per-position cap
          3. Leverage within per-regime cap
          4. Sufficient buying power
          5. Correlation with existing positions below threshold
        """
        # 1 — Halt / flatten gate
        if self._halted or self._cb_level >= CBLevel.FLATTEN:
            return OrderValidation(False, f"blocked by circuit breaker {self._cb_level.name}")

        notional = abs(qty) * price

        # 2 — Position size cap
        per_name_cap = self._effective_per_name_cap()
        max_notional = portfolio_value * per_name_cap
        if notional > max_notional + 1e-6:
            return OrderValidation(
                False,
                f"position {notional:.0f} exceeds cap {max_notional:.0f}",
            )

        # 3 — Leverage cap
        lev_cap = self.max_leverage_for_regime(regime_label)
        if proposed_leverage > lev_cap + 1e-9:
            return OrderValidation(
                False,
                f"leverage {proposed_leverage:.2f} exceeds cap {lev_cap:.2f} "
                f"for regime {regime_label}",
            )

        # 4 — Buying power
        if notional > buying_power + 1e-6:
            return OrderValidation(
                False,
                f"insufficient buying power: need {notional:.0f}, have {buying_power:.0f}",
            )

        # 5 — Optional correlation check (disabled by default; the current
        # portfolio design uses class caps and gross-cap controls instead.)
        if (
            self._cfg.get("enable_correlation_check", False)
            and existing_returns
            and new_returns is not None
        ):
            corr = self._max_correlation(new_returns, existing_returns)
            if corr > self._cfg["max_position_correlation"]:
                return OrderValidation(
                    False,
                    f"correlation {corr:.2f} with existing position exceeds "
                    f"{self._cfg['max_position_correlation']:.2f}",
                )

        return OrderValidation(True, "approved", approved_qty=qty)

    # ------------------------------------------------------------------
    # Correlation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _max_correlation(
        new_returns: np.ndarray,
        existing_returns: dict[str, np.ndarray],
    ) -> float:
        """Return the max absolute Pearson correlation vs. existing positions."""
        new = np.asarray(new_returns, dtype=float)
        max_corr = 0.0
        for _, other in existing_returns.items():
            other = np.asarray(other, dtype=float)
            n = min(len(new), len(other))
            if n < 2:
                continue
            a, b = new[-n:], other[-n:]
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            c = abs(float(np.corrcoef(a, b)[0, 1]))
            if not np.isnan(c):
                max_corr = max(max_corr, c)
        return max_corr

    # ------------------------------------------------------------------
    # Lock file mechanism
    # ------------------------------------------------------------------

    def _trigger_halt(
        self,
        open_positions: dict,
        regime_label: str,
        market_note: str,
        daily_ret: float,
        drawdown: float,
    ) -> None:
        """Write the halt lock file and flip the halted flag."""
        self._halted = True
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Identify which positions contributed most to the loss (by notional).
        culprits = sorted(
            (
                {"ticker": t, **(p if isinstance(p, dict) else {"detail": str(p)})}
                for t, p in open_positions.items()
            ),
            key=lambda d: abs(d.get("unrealised_pnl", 0.0)),
        )

        payload = {
            "event": "MAX_DRAWDOWN_HALT",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "what_happened": (
                f"Peak-to-trough drawdown of {drawdown*100:.2f}% breached the "
                f"{self._cfg['cb_max_drawdown_halt']*100:.0f}% hard limit. "
                f"Automated trading is halted."
            ),
            "drawdown_pct": round(drawdown * 100, 4),
            "daily_return_pct": round(daily_ret * 100, 4),
            "peak_equity": self._peak_equity,
            "current_equity": self._current_equity,
            "regime_at_halt": regime_label,
            "market_conditions": market_note or "n/a",
            "positions_at_halt": culprits,
            "how_to_resume": (
                f"Review this incident, then DELETE BOTH '{self._lock_path}' and "
                f"'{self._state_path}' to allow the bot to start again. "
                f"The state file holds the peak equity the drawdown is measured "
                f"against — leaving it in place would re-trigger this halt on "
                f"the next bar."
            ),
        }
        self._lock_path.write_text(json.dumps(payload, indent=2, default=str))
        logger.critical(
            "RISK HALT: drawdown %.2f%% — lock file written to %s",
            drawdown * 100, self._lock_path,
        )

    def is_halted(self) -> bool:
        """True when the bot is halted (lock file present)."""
        # Re-check disk in case the file was created/removed out of band.
        self._halted = self._lock_path.exists()
        return self._halted

    def clear_lock(self) -> bool:
        """
        Remove the lock file (simulating manual user review).  Returns True
        if a lock was removed.  Intended for operator/test use, NOT for the
        bot to call automatically.

        Also RE-ANCHORS the persisted peak equity to the current equity.  The
        halt fires on peak-to-trough drawdown, so leaving the old peak in place
        would re-trigger the breaker on the very next bar and the bot could
        never resume — clearing the lock is the operator stating that the new,
        lower equity is the baseline going forward.
        """
        if self._lock_path.exists():
            self._lock_path.unlink()
            self._halted = False
            self._cb_level = CBLevel.NONE
            if self._current_equity:
                self._peak_equity = float(self._current_equity)
            self._equity_history = []
            self._save_state()
            logger.info(
                "Risk lock cleared — peak equity re-anchored to %.2f, "
                "trading may resume.", self._peak_equity or 0.0,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def circuit_breaker_level(self) -> CBLevel:
        return self._cb_level

    def circuit_breaker_active(self) -> bool:
        return self._cb_level != CBLevel.NONE

    def should_flatten(self) -> bool:
        """True when the active breaker requires closing all positions."""
        return self._cb_level >= CBLevel.FLATTEN

    def state(self) -> RiskState:
        return RiskState(
            cb_level=self._cb_level,
            halted=self._halted,
            day_start_equity=self._day_start_equity or 0.0,
            peak_equity=self._peak_equity or 0.0,
            current_equity=self._current_equity or 0.0,
            daily_return=self._daily_return(),
            drawdown=self._drawdown(),
        )

    # ------------------------------------------------------------------
    # Internal return/drawdown math
    # ------------------------------------------------------------------

    def _daily_return(self) -> float:
        if not self._day_start_equity:
            return 0.0
        return (self._current_equity - self._day_start_equity) / self._day_start_equity

    def _drawdown(self) -> float:
        if not self._peak_equity:
            return 0.0
        return (self._current_equity - self._peak_equity) / self._peak_equity

    def _weekly_return(self) -> Optional[float]:
        """Return over the rolling weekly window, or None if not enough history."""
        lookback = self._cfg["weekly_lookback_days"]
        if len(self._equity_history) <= lookback:
            return None
        start = self._equity_history[-(lookback + 1)]
        if start == 0:
            return None
        return (self._current_equity - start) / start
