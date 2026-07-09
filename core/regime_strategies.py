"""
Regime Strategies — volatility-based portfolio allocation decisions.

Architecture
------------
The HMM engine classifies the current market regime.  This module
translates that classification into concrete portfolio parameters:
allocation %, leverage cap, sizing rules, and a rebalancing verdict.

Customisation guide
-------------------
All allocation percentages and leverage caps live in the
REGIME_PARAMS dict near the top of this file.  Every number is named
and commented — change them in one place and the rest follows.
Further tunables: the confidence ramp (CONFIDENCE_RAMP_*), the trend
filter (TREND_FILTER_WINDOW), volatility targeting (VOL_TARGET_ANNUAL,
0 = off) and the rebalance throttle (REBALANCE_MAX_BARS).

Data flow
---------
  HMMEngine.update()  →  RegimeOrchestrator.evaluate()  →  StrategySignal
      ↓ regime index       ↓ StrategyParams + confidence      ↓ target weights
      ↓ proba vector       ↓ rebalance flag
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. REGIME TIER DEFINITIONS
#    Map every possible HMM regime label (from hmm_engine._LABEL_MAPS) to
#    one of three volatility tiers.  Add a label here when you extend to
#    n_components > 5.
# ===========================================================================

class VolTier(str, Enum):
    """Broad volatility tier that drives allocation sizing."""
    LOW  = "LOW"    # bull / euphoria — trend strong, max participation
    MED  = "MED"    # neutral — stay invested only if trend intact
    HIGH = "HIGH"   # bear / crash — protect capital, reduce / exit


# Maps every HMM regime *label string* → VolTier
# ── Edit here if you add new regime labels ──────────────────────────────────
LABEL_TO_TIER: dict[str, VolTier] = {
    # 3-state
    "Bull":        VolTier.LOW,
    "Neutral":     VolTier.MED,
    "Bear":        VolTier.HIGH,
    # 4-state additions
    "Euphoria":    VolTier.LOW,
    "Crash":       VolTier.HIGH,
    # 5-state — same as above (Neutral already covered)
    # 6-state additions
    "Weak":        VolTier.MED,
    # 7-state additions
    "Deep Bear":   VolTier.HIGH,
    "Strong Bull": VolTier.LOW,
}


# ===========================================================================
# 2. ALLOCATION PARAMETERS
#    ── These are the numbers you tune to match your risk appetite ──────────
#    All downstream code reads from this dict; nothing is hardcoded below.
# ===========================================================================

@dataclass(frozen=True)
class StrategyParams:
    """Immutable parameter bundle for one volatility tier."""
    # Fraction of portfolio capital to deploy (0.0–1.0+)
    allocation_pct: float
    # Maximum gross leverage (1.0 = no leverage)
    max_leverage: float
    # Apply an additional trend filter before entering new positions?
    require_trend_confirmation: bool
    # Move this fraction to cash when the tier is active (0.0 = stay invested)
    cash_buffer_pct: float
    # Allow short positions in this tier?
    # NOTE: reserved for a future short-selling implementation — the weight
    # builder currently only produces long (>= 0) weights, so this flag has
    # NO effect on live behaviour yet.
    allow_shorts: bool
    # Human-readable rationale logged with every signal
    rationale: str


# ── EDIT THESE VALUES TO CUSTOMISE ALLOCATION BEHAVIOUR ─────────────────────
REGIME_PARAMS: dict[VolTier, StrategyParams] = {

    VolTier.LOW: StrategyParams(
        allocation_pct            = 0.95,   # 95% deployed — maximise participation
        max_leverage              = 1.25,   # up to 1.25× leverage allowed
        require_trend_confirmation= False,  # trend is strong — no extra filter needed
        cash_buffer_pct           = 0.05,   # keep 5% cash for execution slippage
        allow_shorts              = False,
        rationale = "Low-vol bull/euphoria: strong trend, maximum participation.",
    ),

    VolTier.MED: StrategyParams(
        allocation_pct            = 0.60,   # 60% deployed — stay in but cautious
        max_leverage              = 1.00,   # no leverage in neutral markets
        require_trend_confirmation= True,   # only enter if trend filter passes
        cash_buffer_pct           = 0.40,   # 40% cash as buffer
        allow_shorts              = False,
        rationale = "Medium-vol neutral: invest only when trend is confirmed.",
    ),

    VolTier.HIGH: StrategyParams(
        allocation_pct            = 0.20,   # 20% deployed — capital protection mode
        max_leverage              = 0.00,   # zero leverage; may hold net short later
        require_trend_confirmation= True,   # very selective
        cash_buffer_pct           = 0.80,   # 80% cash / safe haven
        allow_shorts              = True,   # defensive shorts allowed in crash
        rationale = "High-vol bear/crash: protect capital, reduce/exit risk assets.",
    ),
}

# Uncertainty penalty: when HMM high_uncertainty is True, multiply
# allocation_pct and max_leverage by this factor (applied ON TOP of the
# confidence ramp below — two independent warnings compound).
UNCERTAINTY_SCALING_FACTOR: float = 0.50   # halve exposure on flickering regimes

# Confidence ramp: exposure scales SMOOTHLY with the dominant regime's
# posterior probability instead of a hard on/off threshold.  (The old
# binary cutoff at 0.55 halved the position when confidence moved from
# 0.56 → 0.54 — whipsaw exactly when the model is least sure.)
#   confidence <= RAMP_LOW   → factor = CONFIDENCE_MIN_FACTOR
#   confidence >= RAMP_HIGH  → factor = 1.0 (full size)
#   in between               → linear interpolation
CONFIDENCE_RAMP_LOW:   float = 0.45
CONFIDENCE_RAMP_HIGH:  float = 0.70
CONFIDENCE_MIN_FACTOR: float = 0.50


# ===========================================================================
# 3. LOW-VOLUME BULL SPECIAL CASE
#    A "thin market" signal can be passed alongside the regime; the
#    orchestrator blends between LOW and MED params when volume is weak.
# ===========================================================================

# Volume z-score below this value is considered "low volume"
LOW_VOLUME_ZSCORE_THRESHOLD: float = -0.75

# Allocation fraction applied on top of LOW params when volume is thin
LOW_VOLUME_ALLOCATION_SCALE: float = 0.80   # reduce LOW allocation by 20%


# ===========================================================================
# 3b. TREND FILTER & VOLATILITY TARGETING
#     Both are consumed inside evaluate(); the helpers below let callers
#     (main.py live loop, backtester) derive the inputs from raw closes.
# ===========================================================================

# Simple-moving-average window for the trend filter.  Tiers with
# require_trend_confirmation=True go to cash while close <= SMA(window).
TREND_FILTER_WINDOW: int = 200

# Annualised portfolio volatility target.  When > 0 the allocation is
# scaled by min(1, target / realised_vol) — it only ever REDUCES exposure,
# never levers up.  0.0 disables vol targeting (default until backtests
# justify a value; see scripts/run_experiments.py).
VOL_TARGET_ANNUAL: float = 0.0


def is_trend_confirmed(
    close: Union[pd.Series, Sequence[float]],
    window: int = TREND_FILTER_WINDOW,
) -> Optional[bool]:
    """
    True when the latest close is above its `window`-bar SMA, False when
    at/below, None when there is not enough history to decide (callers
    should then skip the filter rather than block trading during warm-up).
    Strictly causal: uses only the closes passed in.
    """
    closes = pd.Series(close).dropna()
    if len(closes) < window:
        return None
    sma = float(closes.iloc[-window:].mean())
    return bool(float(closes.iloc[-1]) > sma)


def realised_vol_from_close(
    close: Union[pd.Series, Sequence[float]],
    window: int = 21,
) -> Optional[float]:
    """
    Annualised realised volatility of the last `window` daily log returns,
    or None with insufficient history.  Unlike the (RobustScaler-normalised)
    feature matrix, this returns RAW vol — required for vol targeting.
    """
    closes = pd.Series(close).dropna().astype(float)
    if len(closes) < window + 1:
        return None
    tail = closes.iloc[-(window + 1):].values
    log_rets = np.log(tail[1:] / tail[:-1])
    sd = float(np.std(log_rets, ddof=1))
    return sd * float(np.sqrt(252))


# ===========================================================================
# 4. REBALANCING RULES
# ===========================================================================

# Force a periodic re-sync to target weights after this many bars even if
# nothing else triggered.  Keep this LARGE: every forced rebalance pays
# slippage + commission.  Drift and regime changes are the PRIMARY triggers;
# this is only a staleness backstop.  (The old value of 1 forced a trade
# every other bar and made the drift threshold below meaningless.)
REBALANCE_MAX_BARS: int = 21   # ~1 month of daily bars

# Weight drift tolerance: rebalance if any ticker drifts > this from target
REBALANCE_DRIFT_THRESHOLD: float = 0.05   # 5 percentage points

# Always rebalance on a regime change regardless of the above
REBALANCE_ON_REGIME_CHANGE: bool = True


# ===========================================================================
# 5. SIGNAL DATA CLASS
# ===========================================================================

@dataclass
class StrategySignal:
    """
    Structured output from RegimeOrchestrator.evaluate().

    Consumed by RiskManager and OrderExecutor to make sizing and
    rebalancing decisions.
    """
    # ── Regime context ────────────────────────────────────────────────────
    regime_index:    int          # HMM ranked index (-1 = unknown)
    regime_label:    str          # e.g. "Bull", "Bear"
    vol_tier:        VolTier      # LOW / MED / HIGH
    confidence:      float        # dominant regime posterior probability (0–1)
    high_uncertainty: bool        # True when HMM stability filter is alarmed

    # ── Allocation output ────────────────────────────────────────────────
    params:          StrategyParams   # frozen parameter bundle for this signal
    effective_alloc: float            # params.allocation_pct after uncertainty scaling
    effective_leverage: float         # params.max_leverage after uncertainty scaling

    # ── Target weights ───────────────────────────────────────────────────
    # ticker -> target portfolio weight (0.0–1.0, sum <= effective_alloc)
    target_weights:  dict[str, float] = field(default_factory=dict)

    # ── Rebalancing ──────────────────────────────────────────────────────
    should_rebalance: bool = False
    rebalance_reason: str  = ""

    # ── Meta ─────────────────────────────────────────────────────────────
    rationale:       str   = ""
    low_volume_flag: bool  = False
    # True when a require_trend_confirmation tier was forced to cash
    # because the close sits below its trend SMA.
    trend_blocked:   bool  = False

    # ── Convenience aliases / display labels ─────────────────────────────
    @property
    def tier_label(self) -> str:
        """Short label for logging and dashboard display."""
        return self.vol_tier.value

    @property
    def is_invested(self) -> bool:
        """True when the signal has non-zero target weights."""
        return bool(self.target_weights) and any(
            v > 0 for v in self.target_weights.values()
        )

    @property
    def cash_weight(self) -> float:
        """Implied cash weight = 1 - sum(target_weights)."""
        return max(0.0, 1.0 - sum(self.target_weights.values()))

    def summary(self) -> str:
        """One-line string for logging."""
        return (
            f"[{self.regime_label}/{self.tier_label}] "
            f"alloc={self.effective_alloc:.0%} "
            f"lev={self.effective_leverage:.2f}x "
            f"conf={self.confidence:.2%} "
            f"{'⚠ HIGH-UNCERTAINTY ' if self.high_uncertainty else ''}"
            f"{'📉 LOW-VOL ' if self.low_volume_flag else ''}"
            f"{'⛔ TREND-BLOCK ' if self.trend_blocked else ''}"
            f"| {self.rationale}"
        )


# ===========================================================================
# 6. VOLATILITY RANK HELPER
# ===========================================================================

class VolatilityRanker:
    """
    Track realised volatility over a rolling window and return the
    percentile rank of the most recent reading.

    rank=0.0  → lowest volatility ever seen in the window
    rank=1.0  → highest
    """

    def __init__(self, window: int = 252) -> None:
        self._window  = window
        self._history: list[float] = []

    def update(self, current_vol: float) -> float:
        """Add one vol reading and return the current percentile rank (0–1)."""
        self._history.append(float(current_vol))
        if len(self._history) > self._window:
            self._history.pop(0)
        if len(self._history) < 2:
            return 0.5
        arr  = np.array(self._history)
        # Divide by (n-1) so the highest reading ranks 1.0 and the lowest 0.0
        # (standard percentile rank).  Dividing by n caps the max at (n-1)/n,
        # which never reaches the top of the [0,1] range.
        rank = float(np.sum(arr < current_vol) / (len(arr) - 1))
        return rank

    def current_rank(self) -> float:
        """Return the most recent percentile rank without adding a new value."""
        if len(self._history) < 2:
            return 0.5
        arr  = np.array(self._history)
        return float(np.sum(arr < self._history[-1]) / (len(arr) - 1))

    @property
    def n_observations(self) -> int:
        return len(self._history)


# ===========================================================================
# 7. REGIME ORCHESTRATOR
# ===========================================================================

class RegimeOrchestrator:
    """
    Central decision-maker: takes a regime signal from HMMEngine and
    returns a fully populated StrategySignal ready for RiskManager.

    Usage (live loop)
    -----------------
    >>> orch = RegimeOrchestrator(tickers=["SPY", "QQQ"])
    >>> signal = orch.evaluate(
    ...     regime_index    = engine.current_regime(),
    ...     regime_label    = engine.current_regime_label(),
    ...     proba           = engine.predict_proba_current(),
    ...     high_uncertainty= engine.high_uncertainty,
    ...     volume_zscore   = latest_volume_zscore,
    ... )
    >>> print(signal.summary())
    """

    def __init__(
        self,
        tickers:          list[str],
        vol_ranker:       Optional[VolatilityRanker] = None,
        rebalance_max_bars: int = REBALANCE_MAX_BARS,
        drift_threshold:    float = REBALANCE_DRIFT_THRESHOLD,
        vol_target:         float = VOL_TARGET_ANNUAL,
    ) -> None:
        self._tickers          = list(tickers)
        self._vol_ranker       = vol_ranker or VolatilityRanker()
        self._rebalance_max_bars = rebalance_max_bars
        self._drift_threshold    = drift_threshold
        self._vol_target         = vol_target

        self._last_regime_index: Optional[int]  = None
        self._bars_since_rebalance: int          = 0
        self._last_weights: dict[str, float]     = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        regime_index:    int,
        regime_label:    str,
        proba:           np.ndarray,
        high_uncertainty: bool,
        current_weights: Optional[dict[str, float]] = None,
        volume_zscore:   float = 0.0,
        current_vol:     Optional[float] = None,
        trend_confirmed: Optional[bool] = None,
    ) -> StrategySignal:
        """
        Evaluate the current regime and return a StrategySignal.

        Parameters
        ----------
        regime_index     : HMM ranked index from engine.current_regime()
        regime_label     : string label e.g. "Bull"
        proba            : posterior probability vector (one entry per regime)
        high_uncertainty : engine.high_uncertainty flag
        current_weights  : current live portfolio weights for drift check
        volume_zscore    : most recent volume z-score (from FeatureEngineer)
        current_vol      : RAW annualised realised vol (see
                           realised_vol_from_close); feeds the ranker and,
                           when vol_target > 0, scales the allocation
        trend_confirmed  : output of is_trend_confirmed(); None = not enough
                           history → the trend filter is skipped
        """
        # ── Derive tier ─────────────────────────────────────────────────
        tier = self._resolve_tier(regime_label)

        # ── Confidence ──────────────────────────────────────────────────
        confidence = self._dominant_confidence(proba, regime_index)

        # ── Low-volume bull adjustment ───────────────────────────────────
        low_vol_flag = (
            tier == VolTier.LOW
            and volume_zscore < LOW_VOLUME_ZSCORE_THRESHOLD
        )

        # ── Base params ─────────────────────────────────────────────────
        params = REGIME_PARAMS[tier]

        # ── Effective allocation after confidence / uncertainty scaling ──
        eff_alloc, eff_lev = self._apply_confidence_scaling(
            params, confidence, high_uncertainty, low_vol_flag
        )

        # ── Volatility targeting (only ever reduces exposure) ────────────
        if self._vol_target > 0 and current_vol is not None and current_vol > 1e-9:
            vol_scale = min(1.0, self._vol_target / current_vol)
            if vol_scale < 1.0:
                logger.debug(
                    "Vol targeting: realised=%.3f target=%.3f → alloc ×%.2f",
                    current_vol, self._vol_target, vol_scale,
                )
            eff_alloc *= vol_scale

        # ── Trend filter: confirmation-requiring tiers go to cash while
        #    the close sits below the trend SMA ─────────────────────────
        trend_blocked = bool(
            params.require_trend_confirmation and trend_confirmed is False
        )
        if trend_blocked:
            eff_alloc = 0.0
            logger.debug(
                "Trend filter: %s tier requires confirmation and close is "
                "below SMA — allocation forced to 0.", tier.value,
            )

        # ── Target weights ───────────────────────────────────────────────
        target_weights = self._build_weights(tier, eff_alloc, params)

        # ── Volatility rank update ───────────────────────────────────────
        if current_vol is not None:
            self._vol_ranker.update(current_vol)

        # ── Rebalance decision ───────────────────────────────────────────
        should_rebal, rebal_reason = self._rebalance_decision(
            regime_index, target_weights, current_weights or {}
        )

        # ── Build signal ─────────────────────────────────────────────────
        signal = StrategySignal(
            regime_index     = regime_index,
            regime_label     = regime_label,
            vol_tier         = tier,
            confidence       = confidence,
            high_uncertainty = high_uncertainty,
            params           = params,
            effective_alloc  = eff_alloc,
            effective_leverage = eff_lev,
            target_weights   = target_weights,
            should_rebalance = should_rebal,
            rebalance_reason = rebal_reason,
            rationale        = params.rationale,
            low_volume_flag  = low_vol_flag,
            trend_blocked    = trend_blocked,
        )

        # ── Bookkeeping ──────────────────────────────────────────────────
        self._last_regime_index = regime_index
        self._bars_since_rebalance = 0 if should_rebal else self._bars_since_rebalance + 1
        if should_rebal:
            self._last_weights = dict(target_weights)

        logger.info("RegimeOrchestrator: %s", signal.summary())
        return signal

    # ------------------------------------------------------------------
    # Tier resolution
    # ------------------------------------------------------------------

    def _resolve_tier(self, regime_label: str) -> VolTier:
        """
        Map a regime label string to a VolTier.  Falls back to MED (the
        most conservative middle ground) for unknown labels.
        """
        tier = LABEL_TO_TIER.get(regime_label)
        if tier is None:
            logger.warning(
                "Unknown regime label '%s' — defaulting to MED tier.", regime_label
            )
            tier = VolTier.MED
        return tier

    # ------------------------------------------------------------------
    # Confidence extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _dominant_confidence(proba: np.ndarray, regime_index: int) -> float:
        """Return the probability mass on the active regime (0–1)."""
        if regime_index < 0 or regime_index >= len(proba):
            return 0.0
        return float(proba[regime_index])

    # ------------------------------------------------------------------
    # Uncertainty & low-volume scaling
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_confidence_scaling(
        params: StrategyParams,
        confidence: float,
        high_uncertainty: bool,
        low_vol_flag: bool,
    ) -> tuple[float, float]:
        """
        Return (effective_allocation, effective_leverage) after applying
        the confidence ramp, uncertainty and low-volume penalties.

        Rules (applied multiplicatively in order):
          1. Confidence ramp: linear from CONFIDENCE_MIN_FACTOR (at/below
             RAMP_LOW) to 1.0 (at/above RAMP_HIGH).  Smooth by design —
             a hard threshold caused position whipsaw around the cutoff.
          2. If high_uncertainty (HMM flicker alarm) → × UNCERTAINTY_SCALING_FACTOR
          3. If low_vol_flag (thin volume in bull) → alloc × LOW_VOLUME_ALLOCATION_SCALE
        """
        ramp_span = CONFIDENCE_RAMP_HIGH - CONFIDENCE_RAMP_LOW
        factor = (confidence - CONFIDENCE_RAMP_LOW) / ramp_span
        factor = float(np.clip(factor, CONFIDENCE_MIN_FACTOR, 1.0))

        if high_uncertainty:
            factor *= UNCERTAINTY_SCALING_FACTOR

        alloc = params.allocation_pct * factor
        lev   = params.max_leverage   * factor
        if factor < 1.0:
            logger.debug(
                "Confidence scaling (conf=%.2f, high_uncertainty=%s): "
                "factor=%.2f alloc→%.2f lev→%.2f",
                confidence, high_uncertainty, factor, alloc, lev,
            )

        if low_vol_flag:
            alloc *= LOW_VOLUME_ALLOCATION_SCALE
            logger.debug(
                "Low-volume bull scaling applied: alloc→%.2f", alloc
            )

        # Hard floor: never go below 0
        alloc = max(0.0, alloc)
        lev   = max(0.0, lev)
        return alloc, lev

    # ------------------------------------------------------------------
    # Weight construction
    # ------------------------------------------------------------------

    def _build_weights(
        self,
        tier: VolTier,
        effective_alloc: float,
        params: StrategyParams,
    ) -> dict[str, float]:
        """
        Distribute effective_alloc equally across tickers.
        In HIGH tier the cash_buffer_pct squeezes weight further.
        Returns empty dict when allocation rounds to zero.
        """
        if not self._tickers or effective_alloc <= 0:
            return {}

        # Equal-weight across all tickers up to effective_alloc
        per_ticker = effective_alloc / len(self._tickers)
        weights    = {t: round(per_ticker, 6) for t in self._tickers}

        # Sanity check: total must not exceed 1.0
        total = sum(weights.values())
        if total > 1.0 + 1e-9:
            scale  = 1.0 / total
            weights = {t: round(w * scale, 6) for t, w in weights.items()}

        return weights

    # ------------------------------------------------------------------
    # Rebalancing decision
    # ------------------------------------------------------------------

    def _rebalance_decision(
        self,
        regime_index:    int,
        target_weights:  dict[str, float],
        current_weights: dict[str, float],
    ) -> tuple[bool, str]:
        """
        Returns (should_rebalance, reason_string).

        Triggers (in priority order):
          0. Very first evaluation (establish the initial position)
          A. Regime change
          B. Max weight drift exceeds threshold
          C. Staleness backstop: REBALANCE_MAX_BARS since last rebalance

        A and B are the PRIMARY triggers.  C exists only so a long-held
        book is eventually re-synced; keep it large — every rebalance
        costs slippage + commission.
        """
        # 0 — First-ever evaluation: no reference point yet → establish one
        if self._last_regime_index is None:
            return True, "initial"

        # A — Regime change
        if (
            REBALANCE_ON_REGIME_CHANGE
            and regime_index != self._last_regime_index
        ):
            return True, f"regime_change({self._last_regime_index}→{regime_index})"

        # B — Drift check
        if current_weights:
            max_drift = max(
                abs(target_weights.get(t, 0.0) - current_weights.get(t, 0.0))
                for t in set(target_weights) | set(current_weights)
            )
            if max_drift > self._drift_threshold:
                return True, f"drift={max_drift:.3f}"

        # C — Staleness backstop
        if self._bars_since_rebalance >= self._rebalance_max_bars:
            return True, f"interval={self._bars_since_rebalance}bars"

        return False, ""


# ===========================================================================
# 8. CONVENIENCE ALIASES — for display, logging, and downstream labelling
# ===========================================================================

#: Maps VolTier → short display string used in the dashboard
TIER_DISPLAY: dict[VolTier, str] = {
    VolTier.LOW:  "🟢 LOW VOL / BULL",
    VolTier.MED:  "🟡 MED VOL / NEUTRAL",
    VolTier.HIGH: "🔴 HIGH VOL / BEAR",
}

#: Maps VolTier → suggested colour hex for Plotly / Streamlit charts
TIER_COLOUR: dict[VolTier, str] = {
    VolTier.LOW:  "#2ecc71",   # green
    VolTier.MED:  "#f39c12",   # amber
    VolTier.HIGH: "#e74c3c",   # red
}

#: Human-readable short form for each known regime label
REGIME_SHORT: dict[str, str] = {
    "Bull":        "BULL",
    "Euphoria":    "EUPH",
    "Strong Bull": "SBUL",
    "Neutral":     "NEUT",
    "Weak":        "WEAK",
    "Bear":        "BEAR",
    "Deep Bear":   "DBEAR",
    "Crash":       "CRSH",
}


def regime_display_label(regime_label: str, confidence: float) -> str:
    """
    Return a formatted string combining regime name, tier icon, and
    confidence for use in log lines and dashboard headers.
    """
    tier  = LABEL_TO_TIER.get(regime_label, VolTier.MED)
    short = REGIME_SHORT.get(regime_label, regime_label[:4].upper())
    icon  = TIER_DISPLAY[tier].split()[0]
    return f"{icon} {short} ({confidence:.0%})"
