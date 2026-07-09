"""
Tests for core/regime_strategies.py.

Run with:  pytest tests/test_strategies.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.regime_strategies import (
    CONFIDENCE_MIN_FACTOR,
    CONFIDENCE_RAMP_HIGH,
    CONFIDENCE_RAMP_LOW,
    LABEL_TO_TIER,
    LOW_VOLUME_ALLOCATION_SCALE,
    LOW_VOLUME_ZSCORE_THRESHOLD,
    REBALANCE_DRIFT_THRESHOLD,
    REBALANCE_MAX_BARS,
    REBALANCE_ON_REGIME_CHANGE,
    REGIME_PARAMS,
    REGIME_SHORT,
    TIER_COLOUR,
    TIER_DISPLAY,
    TREND_FILTER_WINDOW,
    UNCERTAINTY_SCALING_FACTOR,
    RegimeOrchestrator,
    StrategyParams,
    StrategySignal,
    VolTier,
    VolatilityRanker,
    is_trend_confirmed,
    realised_vol_from_close,
    regime_display_label,
    shares_for_target_weight,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uniform_proba(n: int, dominant: int) -> np.ndarray:
    """Probability vector with 0.8 on `dominant` and rest split uniformly."""
    proba = np.full(n, 0.2 / max(n - 1, 1))
    proba[dominant] = 0.8
    return proba


def _low_proba(n: int, dominant: int) -> np.ndarray:
    """Probability vector with only 0.40 on `dominant` — below CONFIDENCE_RAMP_LOW."""
    proba = np.full(n, 0.60 / max(n - 1, 1))
    proba[dominant] = 0.40
    return proba


def _make_orch(tickers=None) -> RegimeOrchestrator:
    return RegimeOrchestrator(tickers=tickers or ["SPY", "QQQ", "IWM"])


# ---------------------------------------------------------------------------
# 1. VolTier mapping
# ---------------------------------------------------------------------------

class TestLabelToTierMapping:

    def test_bull_maps_to_low(self):
        assert LABEL_TO_TIER["Bull"] == VolTier.LOW

    def test_euphoria_maps_to_low(self):
        assert LABEL_TO_TIER["Euphoria"] == VolTier.LOW

    def test_strong_bull_maps_to_low(self):
        assert LABEL_TO_TIER["Strong Bull"] == VolTier.LOW

    def test_neutral_maps_to_med(self):
        assert LABEL_TO_TIER["Neutral"] == VolTier.MED

    def test_weak_maps_to_med(self):
        assert LABEL_TO_TIER["Weak"] == VolTier.MED

    def test_bear_maps_to_high(self):
        assert LABEL_TO_TIER["Bear"] == VolTier.HIGH

    def test_crash_maps_to_high(self):
        assert LABEL_TO_TIER["Crash"] == VolTier.HIGH

    def test_deep_bear_maps_to_high(self):
        assert LABEL_TO_TIER["Deep Bear"] == VolTier.HIGH

    def test_unknown_label_defaults_to_med(self):
        orch = _make_orch()
        tier = orch._resolve_tier("Totally Unknown Regime")
        assert tier == VolTier.MED


# ---------------------------------------------------------------------------
# 2. REGIME_PARAMS sanity
# ---------------------------------------------------------------------------

class TestRegimeParams:

    def test_all_tiers_have_params(self):
        for tier in VolTier:
            assert tier in REGIME_PARAMS

    def test_low_tier_highest_allocation(self):
        assert REGIME_PARAMS[VolTier.LOW].allocation_pct > REGIME_PARAMS[VolTier.MED].allocation_pct
        assert REGIME_PARAMS[VolTier.MED].allocation_pct > REGIME_PARAMS[VolTier.HIGH].allocation_pct

    def test_low_tier_highest_leverage(self):
        assert REGIME_PARAMS[VolTier.LOW].max_leverage >= REGIME_PARAMS[VolTier.MED].max_leverage
        assert REGIME_PARAMS[VolTier.MED].max_leverage >= REGIME_PARAMS[VolTier.HIGH].max_leverage

    def test_high_tier_no_leverage(self):
        assert REGIME_PARAMS[VolTier.HIGH].max_leverage == 0.0

    def test_high_tier_allows_shorts(self):
        assert REGIME_PARAMS[VolTier.HIGH].allow_shorts is True

    def test_low_tier_no_trend_filter(self):
        assert REGIME_PARAMS[VolTier.LOW].require_trend_confirmation is False

    def test_params_are_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            REGIME_PARAMS[VolTier.LOW].allocation_pct = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. RegimeOrchestrator — strategy paths (one per tier)
# ---------------------------------------------------------------------------

class TestOrchestratorStrategyPaths:

    def test_low_vol_bull_signal(self):
        orch   = _make_orch()
        proba  = _uniform_proba(3, 2)
        signal = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert signal.vol_tier == VolTier.LOW
        assert signal.effective_alloc > 0.50

    def test_med_vol_neutral_signal(self):
        orch   = _make_orch()
        proba  = _uniform_proba(3, 1)
        signal = orch.evaluate(1, "Neutral", proba, high_uncertainty=False)
        assert signal.vol_tier == VolTier.MED

    def test_high_vol_bear_signal(self):
        orch   = _make_orch()
        proba  = _uniform_proba(3, 0)
        signal = orch.evaluate(0, "Bear", proba, high_uncertainty=False)
        assert signal.vol_tier == VolTier.HIGH
        assert signal.effective_alloc < REGIME_PARAMS[VolTier.MED].allocation_pct

    def test_crash_regime_signal(self):
        orch   = _make_orch()
        proba  = _uniform_proba(4, 0)
        signal = orch.evaluate(0, "Crash", proba, high_uncertainty=False)
        assert signal.vol_tier == VolTier.HIGH

    def test_euphoria_regime_signal(self):
        orch   = _make_orch()
        proba  = _uniform_proba(4, 3)
        signal = orch.evaluate(3, "Euphoria", proba, high_uncertainty=False)
        assert signal.vol_tier == VolTier.LOW

    def test_target_weights_populated(self):
        orch   = _make_orch(["SPY", "QQQ"])
        proba  = _uniform_proba(3, 2)
        signal = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert set(signal.target_weights.keys()) == {"SPY", "QQQ"}

    def test_target_weights_sum_within_alloc(self):
        orch   = _make_orch(["SPY", "QQQ", "IWM"])
        proba  = _uniform_proba(3, 2)
        signal = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        total  = sum(signal.target_weights.values())
        assert total <= signal.effective_alloc + 1e-6

    def test_weights_never_exceed_one(self):
        for label, tier in LABEL_TO_TIER.items():
            orch  = _make_orch(["SPY"])
            idx   = list(LABEL_TO_TIER.keys()).index(label)
            proba = _uniform_proba(len(LABEL_TO_TIER), idx)
            sig   = orch.evaluate(idx, label, proba, high_uncertainty=False)
            total = sum(sig.target_weights.values())
            assert total <= 1.0 + 1e-6, f"Weights exceed 1.0 for label={label}"


# ---------------------------------------------------------------------------
# 4. Confidence & uncertainty scaling
# ---------------------------------------------------------------------------

class TestConfidenceScaling:

    def test_high_uncertainty_halves_allocation(self):
        orch     = _make_orch()
        proba_hi = _uniform_proba(3, 2)
        base_sig = orch.evaluate(2, "Bull", proba_hi, high_uncertainty=False)

        orch2    = _make_orch()
        unc_sig  = orch2.evaluate(2, "Bull", proba_hi, high_uncertainty=True)

        assert abs(unc_sig.effective_alloc - base_sig.effective_alloc * UNCERTAINTY_SCALING_FACTOR) < 1e-6

    def test_low_confidence_halves_allocation(self):
        orch     = _make_orch()
        lo_proba = _low_proba(3, 2)          # confidence = 0.40 < threshold
        hi_proba = _uniform_proba(3, 2)      # confidence = 0.80

        base_sig = orch.evaluate(2, "Bull", hi_proba, high_uncertainty=False)
        orch2    = _make_orch()
        low_sig  = orch2.evaluate(2, "Bull", lo_proba, high_uncertainty=False)

        assert low_sig.effective_alloc < base_sig.effective_alloc

    def test_high_uncertainty_halves_leverage(self):
        orch     = _make_orch()
        proba    = _uniform_proba(3, 2)
        base_lev = orch.evaluate(2, "Bull", proba, high_uncertainty=False).effective_leverage

        orch2    = _make_orch()
        unc_lev  = orch2.evaluate(2, "Bull", proba, high_uncertainty=True).effective_leverage

        assert abs(unc_lev - base_lev * UNCERTAINTY_SCALING_FACTOR) < 1e-6

    def test_confidence_zero_gives_near_zero_alloc(self):
        orch  = _make_orch()
        proba = np.zeros(3)    # no confidence at all
        sig   = orch.evaluate(2, "Bull", proba, high_uncertainty=True)
        assert sig.effective_alloc < REGIME_PARAMS[VolTier.LOW].allocation_pct

    def test_high_confidence_no_penalty(self):
        orch  = _make_orch()
        proba = _uniform_proba(3, 2)      # 0.80 > CONFIDENCE_RAMP_HIGH
        sig   = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert sig.effective_alloc == pytest.approx(
            REGIME_PARAMS[VolTier.LOW].allocation_pct, abs=1e-6
        )

    def test_confidence_ramp_is_smooth(self):
        """Confidence inside the ramp must scale linearly, not jump to a cliff."""
        conf = CONFIDENCE_RAMP_LOW + 0.8 * (CONFIDENCE_RAMP_HIGH - CONFIDENCE_RAMP_LOW)
        proba = np.full(3, (1 - conf) / 2)
        proba[2] = conf
        sig = _make_orch().evaluate(2, "Bull", proba, high_uncertainty=False)
        # 80% up the ramp → factor 0.8 (above the 0.5 floor, below 1.0)
        expected_factor = float(np.clip(0.8, CONFIDENCE_MIN_FACTOR, 1.0))
        assert sig.effective_alloc == pytest.approx(
            REGIME_PARAMS[VolTier.LOW].allocation_pct * expected_factor, abs=1e-6
        )

    def test_confidence_between_ramp_points_monotonic(self):
        """Higher confidence must never produce a smaller allocation."""
        allocs = []
        for conf in (0.40, 0.50, 0.60, 0.70, 0.80):
            proba = np.full(3, (1 - conf) / 2)
            proba[2] = conf
            sig = _make_orch().evaluate(2, "Bull", proba, high_uncertainty=False)
            allocs.append(sig.effective_alloc)
        assert allocs == sorted(allocs)


# ---------------------------------------------------------------------------
# 5. Low-volume bull
# ---------------------------------------------------------------------------

class TestLowVolumeBull:

    def test_low_volume_flag_set(self):
        orch  = _make_orch()
        proba = _uniform_proba(3, 2)
        sig   = orch.evaluate(
            2, "Bull", proba,
            high_uncertainty=False,
            volume_zscore=LOW_VOLUME_ZSCORE_THRESHOLD - 0.1,
        )
        assert sig.low_volume_flag is True

    def test_low_volume_reduces_allocation(self):
        orch  = _make_orch()
        proba = _uniform_proba(3, 2)
        normal_sig = orch.evaluate(2, "Bull", proba, high_uncertainty=False, volume_zscore=0.0)

        orch2 = _make_orch()
        low_vol_sig = orch2.evaluate(
            2, "Bull", proba,
            high_uncertainty=False,
            volume_zscore=LOW_VOLUME_ZSCORE_THRESHOLD - 0.1,
        )
        assert low_vol_sig.effective_alloc < normal_sig.effective_alloc

    def test_low_volume_not_triggered_in_bear(self):
        orch  = _make_orch()
        proba = _uniform_proba(3, 0)
        sig   = orch.evaluate(
            0, "Bear", proba,
            high_uncertainty=False,
            volume_zscore=LOW_VOLUME_ZSCORE_THRESHOLD - 0.5,
        )
        assert sig.low_volume_flag is False   # only applies in LOW tier


# ---------------------------------------------------------------------------
# 6. Rebalancing trigger logic
# ---------------------------------------------------------------------------

class TestRebalancingLogic:

    def test_first_evaluation_triggers_initial_rebalance(self):
        """The very first signal must rebalance to establish the position."""
        orch  = _make_orch()
        sig1  = orch.evaluate(2, "Bull", _uniform_proba(3, 2), high_uncertainty=False)
        assert sig1.should_rebalance is True
        assert sig1.rebalance_reason == "initial"

    def test_regime_change_triggers_rebalance(self):
        orch  = _make_orch()
        proba = _uniform_proba(3, 2)
        # First call establishes last_regime
        orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        # Second call with different regime
        sig2 = orch.evaluate(0, "Bear", _uniform_proba(3, 0), high_uncertainty=False)
        assert sig2.should_rebalance is True
        assert "regime_change" in sig2.rebalance_reason

    def test_no_regime_change_no_rebalance_below_drift(self):
        orch = RegimeOrchestrator(
            tickers=["SPY"],
            rebalance_max_bars=999,   # disable staleness backstop
            drift_threshold=0.99,     # very high drift threshold — won't trigger
        )
        proba = _uniform_proba(3, 2)
        orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        # Same regime, no drift source, staleness not reached
        sig2 = orch.evaluate(
            2, "Bull", proba,
            high_uncertainty=False,
            current_weights={"SPY": REGIME_PARAMS[VolTier.LOW].allocation_pct},
        )
        assert sig2.should_rebalance is False

    def test_drift_triggers_rebalance(self):
        orch  = RegimeOrchestrator(
            tickers=["SPY"],
            rebalance_max_bars=999,
            drift_threshold=REBALANCE_DRIFT_THRESHOLD,
        )
        proba = _uniform_proba(3, 2)
        orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        sig2 = orch.evaluate(
            2, "Bull", proba,
            high_uncertainty=False,
            current_weights={"SPY": 0.0},   # drifted to 0 — huge gap
        )
        assert sig2.should_rebalance is True
        assert "drift" in sig2.rebalance_reason

    def test_staleness_backstop_triggers_rebalance(self):
        orch = RegimeOrchestrator(
            tickers=["SPY"],
            rebalance_max_bars=2,
            drift_threshold=0.99,
        )
        proba = _uniform_proba(3, 2)
        # Bar 1: initial rebalance, counter resets to 0.
        sig1 = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert sig1.should_rebalance is True
        # Bar 2: 0 bars since rebalance → below backstop.
        sig2 = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert sig2.should_rebalance is False
        # Bar 3: 1 bar since rebalance → still below backstop.
        sig3 = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert sig3.should_rebalance is False
        # Bar 4: 2 bars since rebalance → backstop (>= 2) fires.
        sig4 = orch.evaluate(2, "Bull", proba, high_uncertainty=False)
        assert sig4.should_rebalance is True
        assert "interval" in sig4.rebalance_reason

    def test_default_backstop_is_not_churny(self):
        """Guard: the default staleness backstop must be well above 1 bar."""
        assert REBALANCE_MAX_BARS >= 5


# ---------------------------------------------------------------------------
# 6b. Trend filter
# ---------------------------------------------------------------------------

class TestTrendFilter:

    def test_trend_block_forces_med_tier_to_cash(self):
        """MED tier requires trend confirmation → below SMA = no exposure."""
        orch = _make_orch()
        sig = orch.evaluate(
            1, "Neutral", _uniform_proba(3, 1),
            high_uncertainty=False, trend_confirmed=False,
        )
        assert sig.trend_blocked is True
        assert sig.effective_alloc == 0.0
        assert sig.target_weights == {}

    def test_trend_block_forces_high_tier_to_cash(self):
        orch = _make_orch()
        sig = orch.evaluate(
            0, "Bear", _uniform_proba(3, 0),
            high_uncertainty=False, trend_confirmed=False,
        )
        assert sig.trend_blocked is True
        assert sig.target_weights == {}

    def test_low_tier_ignores_trend_filter(self):
        """LOW tier has require_trend_confirmation=False — never blocked."""
        orch = _make_orch()
        sig = orch.evaluate(
            2, "Bull", _uniform_proba(3, 2),
            high_uncertainty=False, trend_confirmed=False,
        )
        assert sig.trend_blocked is False
        assert sig.effective_alloc > 0.0

    def test_confirmed_trend_keeps_allocation(self):
        orch = _make_orch()
        sig = orch.evaluate(
            1, "Neutral", _uniform_proba(3, 1),
            high_uncertainty=False, trend_confirmed=True,
        )
        assert sig.trend_blocked is False
        assert sig.effective_alloc > 0.0

    def test_none_skips_filter_during_warmup(self):
        """trend_confirmed=None (not enough history) must not block trading."""
        orch = _make_orch()
        sig = orch.evaluate(
            1, "Neutral", _uniform_proba(3, 1),
            high_uncertainty=False, trend_confirmed=None,
        )
        assert sig.trend_blocked is False
        assert sig.effective_alloc > 0.0

    def test_is_trend_confirmed_above_sma(self):
        closes = pd.Series(np.linspace(100, 200, TREND_FILTER_WINDOW + 10))
        assert is_trend_confirmed(closes) is True

    def test_is_trend_confirmed_below_sma(self):
        closes = pd.Series(np.linspace(200, 100, TREND_FILTER_WINDOW + 10))
        assert is_trend_confirmed(closes) is False

    def test_is_trend_confirmed_insufficient_history(self):
        closes = pd.Series(np.linspace(100, 110, TREND_FILTER_WINDOW - 1))
        assert is_trend_confirmed(closes) is None


# ---------------------------------------------------------------------------
# 6c. Volatility targeting
# ---------------------------------------------------------------------------

class TestVolTargeting:

    def test_disabled_by_default(self):
        orch = _make_orch()
        sig = orch.evaluate(
            2, "Bull", _uniform_proba(3, 2),
            high_uncertainty=False, current_vol=0.50,   # very high realised vol
        )
        # vol_target defaults to 0.0 → no scaling despite huge vol
        assert sig.effective_alloc == pytest.approx(
            REGIME_PARAMS[VolTier.LOW].allocation_pct, abs=1e-6
        )

    def test_high_vol_scales_allocation_down(self):
        orch = RegimeOrchestrator(tickers=["SPY", "QQQ", "IWM"], vol_target=0.10)
        sig = orch.evaluate(
            2, "Bull", _uniform_proba(3, 2),
            high_uncertainty=False, current_vol=0.20,   # 2× the target
        )
        expected = REGIME_PARAMS[VolTier.LOW].allocation_pct * 0.5
        assert sig.effective_alloc == pytest.approx(expected, abs=1e-6)

    def test_low_vol_never_levers_up(self):
        orch = RegimeOrchestrator(tickers=["SPY"], vol_target=0.10)
        sig = orch.evaluate(
            2, "Bull", _uniform_proba(3, 2),
            high_uncertainty=False, current_vol=0.05,   # half the target
        )
        # scale = min(1, 0.10/0.05) = 1 → unchanged, no leverage boost
        assert sig.effective_alloc == pytest.approx(
            REGIME_PARAMS[VolTier.LOW].allocation_pct, abs=1e-6
        )

    def test_missing_vol_skips_scaling(self):
        orch = RegimeOrchestrator(tickers=["SPY"], vol_target=0.10)
        sig = orch.evaluate(
            2, "Bull", _uniform_proba(3, 2),
            high_uncertainty=False, current_vol=None,
        )
        assert sig.effective_alloc == pytest.approx(
            REGIME_PARAMS[VolTier.LOW].allocation_pct, abs=1e-6
        )

    def test_realised_vol_from_close_positive(self):
        rng = np.random.default_rng(0)
        closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
        vol = realised_vol_from_close(closes)
        assert vol is not None
        assert 0.0 < vol < 1.0   # ~16% annualised for 1% daily moves

    def test_realised_vol_insufficient_history(self):
        assert realised_vol_from_close(pd.Series([100.0, 101.0])) is None


# ---------------------------------------------------------------------------
# 7. StrategySignal data class
# ---------------------------------------------------------------------------

class TestStrategySignalDataClass:

    def _make_signal(self, tier=VolTier.LOW, alloc=0.90, lev=1.0) -> StrategySignal:
        params = REGIME_PARAMS[tier]
        return StrategySignal(
            regime_index     = 2,
            regime_label     = "Bull",
            vol_tier         = tier,
            confidence       = 0.80,
            high_uncertainty = False,
            params           = params,
            effective_alloc  = alloc,
            effective_leverage = lev,
            target_weights   = {"SPY": 0.45, "QQQ": 0.45},
            should_rebalance = False,
            rebalance_reason = "",
            rationale        = "test",
        )

    def test_signal_fields_accessible(self):
        sig = self._make_signal()
        assert sig.regime_index == 2
        assert sig.regime_label == "Bull"
        assert sig.vol_tier == VolTier.LOW
        assert sig.confidence == 0.80
        assert sig.effective_alloc == 0.90

    def test_tier_label_property(self):
        sig = self._make_signal(VolTier.HIGH, 0.20)
        assert sig.tier_label == "HIGH"

    def test_is_invested_true(self):
        sig = self._make_signal()
        assert sig.is_invested is True

    def test_is_invested_false_when_no_weights(self):
        params = REGIME_PARAMS[VolTier.HIGH]
        sig = StrategySignal(
            regime_index=0, regime_label="Bear", vol_tier=VolTier.HIGH,
            confidence=0.8, high_uncertainty=False, params=params,
            effective_alloc=0.0, effective_leverage=0.0,
            target_weights={},
        )
        assert sig.is_invested is False

    def test_cash_weight_correct(self):
        sig = self._make_signal(alloc=0.90)
        # target_weights = {"SPY": 0.45, "QQQ": 0.45} → sum = 0.90
        assert sig.cash_weight == pytest.approx(0.10, abs=1e-6)

    def test_summary_contains_regime_label(self):
        sig = self._make_signal()
        assert "Bull" in sig.summary()

    def test_summary_contains_alloc(self):
        sig = self._make_signal(alloc=0.90)
        assert "90%" in sig.summary()


# ---------------------------------------------------------------------------
# 8. VolatilityRanker
# ---------------------------------------------------------------------------

class TestVolatilityRanker:

    def test_single_observation_returns_half(self):
        vr = VolatilityRanker()
        rank = vr.update(0.20)
        assert rank == 0.5     # only one sample → neutral

    def test_highest_vol_ranks_near_one(self):
        vr = VolatilityRanker()
        for v in [0.05, 0.10, 0.15]:
            vr.update(v)
        rank = vr.update(0.50)   # clearly the highest
        assert rank > 0.75

    def test_lowest_vol_ranks_near_zero(self):
        vr = VolatilityRanker()
        for v in [0.20, 0.25, 0.30]:
            vr.update(v)
        rank = vr.update(0.01)   # clearly the lowest
        assert rank == 0.0

    def test_window_bounded(self):
        vr = VolatilityRanker(window=5)
        for v in range(100):
            vr.update(float(v))
        assert vr.n_observations <= 5


# ---------------------------------------------------------------------------
# 9. Alias / display helpers
# ---------------------------------------------------------------------------

class TestAliasesAndLabels:

    def test_tier_display_all_tiers_present(self):
        for tier in VolTier:
            assert tier in TIER_DISPLAY

    def test_tier_colour_all_tiers_present(self):
        for tier in VolTier:
            assert tier in TIER_COLOUR

    def test_regime_short_keys_are_known_labels(self):
        for label in REGIME_SHORT:
            assert label in LABEL_TO_TIER, f"REGIME_SHORT key '{label}' not in LABEL_TO_TIER"

    def test_regime_display_label_contains_confidence(self):
        label = regime_display_label("Bull", 0.82)
        assert "82%" in label

    def test_regime_display_label_unknown_falls_back(self):
        label = regime_display_label("Quantum Bear", 0.50)
        assert "50%" in label


# ---------------------------------------------------------------------------
# Position sizing — target_weight IS the allocation (sizing overhaul)
# ---------------------------------------------------------------------------

class TestSharesForTargetWeight:
    """`shares_for_target_weight` must deploy the full target_weight of equity,
    not a discounted risk-based sliver (the pre-fix bug capped exposure ~2%)."""

    def test_full_allocation_deploys_full_notional(self):
        # 60% tier at $100, $100k equity → 600 shares = $60k = 60% of equity.
        shares = shares_for_target_weight(0.60, price=100.0, equity=100_000)
        assert shares == pytest.approx(600.0)

    def test_notional_matches_weight(self):
        for w in (0.20, 0.60, 0.95):
            shares = shares_for_target_weight(w, price=50.0, equity=200_000)
            notional = shares * 50.0
            assert notional == pytest.approx(w * 200_000)

    def test_zero_or_negative_weight_is_flat(self):
        assert shares_for_target_weight(0.0, 100.0, 100_000) == 0.0
        assert shares_for_target_weight(-0.3, 100.0, 100_000) == 0.0

    def test_bad_price_or_equity_is_flat(self):
        assert shares_for_target_weight(0.6, 0.0, 100_000) == 0.0
        assert shares_for_target_weight(0.6, 100.0, 0.0) == 0.0

    def test_max_exposure_ceiling_caps_allocation(self):
        # Weight above the ceiling is clamped to the ceiling.
        shares = shares_for_target_weight(
            0.95, price=100.0, equity=100_000, max_exposure=0.50
        )
        assert shares * 100.0 == pytest.approx(0.50 * 100_000)

    def test_circuit_breaker_scaling_halves_size(self):
        full = shares_for_target_weight(0.60, 100.0, 100_000, cb_scaling=1.0)
        halved = shares_for_target_weight(0.60, 100.0, 100_000, cb_scaling=0.5)
        assert halved == pytest.approx(full * 0.5)

    def test_default_ceiling_is_fully_invested(self):
        # Default max_exposure=1.0 allows a ~100% allocation (no leverage).
        shares = shares_for_target_weight(1.0, price=100.0, equity=100_000)
        assert shares * 100.0 == pytest.approx(100_000)
