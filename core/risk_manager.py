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
from typing import Optional

import numpy as np

from settings import config

logger = logging.getLogger(__name__)


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
        qty = rm.size_position(price, stop_price, portfolio_value)
        v   = rm.validate_order(ticker, qty, price, portfolio_value,
                                buying_power, leverage, regime_label)
        if v.approved: submit(v.approved_qty)
    """

    def __init__(
        self,
        cfg: Optional[dict] = None,
        regime_leverage_caps: Optional[dict] = None,
        lock_file_path: Optional[str] = None,
    ) -> None:
        self._cfg = cfg or config.RISK
        self._regime_caps = regime_leverage_caps or getattr(
            config, "REGIME_LEVERAGE_CAPS", {}
        )
        self._lock_path = Path(lock_file_path or self._cfg["lock_file_path"])

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

    @property
    def lock_path(self) -> Path:
        """Path to this manager's circuit-breaker HALT lock file."""
        return self._lock_path

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

        daily_ret = self._daily_return()
        drawdown  = self._drawdown()
        weekly_ret = self._weekly_return()

        level = CBLevel.NONE

        # ── -2% single day → halve ───────────────────────────────────────
        if daily_ret <= -self._cfg["cb_daily_halve_loss"]:
            level = max(level, CBLevel.HALVE)

        # ── -5% week → resize remaining positions down ───────────────────
        if weekly_ret is not None and weekly_ret <= -self._cfg["cb_weekly_resize_loss"]:
            level = max(level, CBLevel.WEEKLY_RESIZE)

        # ── -3% single day → flatten everything ──────────────────────────
        if daily_ret <= -self._cfg["cb_daily_flatten_loss"]:
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

    # ------------------------------------------------------------------
    # Position sizing (1% risk rule)
    # ------------------------------------------------------------------

    def size_position(
        self,
        entry_price: float,
        stop_price: float,
        portfolio_value: float,
        max_risk_per_trade: Optional[float] = None,
    ) -> int:
        """
        Size a position from the per-trade risk limit and stop distance.

            risk_dollars = portfolio_value * max_risk_per_trade
            qty          = risk_dollars / |entry_price - stop_price|

        The result is reduced by any active circuit-breaker scaling factor
        and floored to a whole number of shares.
        """
        if entry_price <= 0 or portfolio_value <= 0:
            return 0
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            logger.warning("size_position: zero stop distance — returning 0 shares.")
            return 0

        risk_pct     = max_risk_per_trade or self._cfg["max_risk_per_trade"]
        risk_dollars = portfolio_value * risk_pct
        raw_qty      = risk_dollars / stop_distance

        # Apply circuit-breaker scaling
        raw_qty *= self.size_scaling_factor()

        # Never exceed the per-position notional cap
        max_notional = portfolio_value * self._cfg["max_position_size"]
        max_qty      = max_notional / entry_price
        qty          = min(raw_qty, max_qty)

        return int(max(0, np.floor(qty)))

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
        """
        cap = self._regime_caps.get(regime_label, self._cfg["max_leverage"])
        # Partial breakers shrink allowed leverage in step with sizing.
        cap *= self.size_scaling_factor()
        return cap

    # ------------------------------------------------------------------
    # Order validation (the gate every order must pass)
    # ------------------------------------------------------------------

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
        max_notional = portfolio_value * self._cfg["max_position_size"]
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

        # 5 — Correlation check
        if existing_returns and new_returns is not None:
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
                f"Review this incident, then DELETE '{self._lock_path}' to allow "
                f"the bot to start again."
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
        """
        if self._lock_path.exists():
            self._lock_path.unlink()
            self._halted = False
            self._cb_level = CBLevel.NONE
            logger.info("Risk lock file cleared — trading may resume.")
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
