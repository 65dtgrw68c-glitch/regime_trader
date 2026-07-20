"""
Tests for core/risk_manager.py — the hardcoded risk-control layer.

Run with:  pytest tests/test_risk.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_manager import CBLevel, OrderValidation, RiskManager


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BASE_CFG = {
    "max_position_size":        0.10,
    "max_leverage":             1.0,
    "max_risk_per_trade":       0.01,
    "per_name_cap":             0.40,
    "gross_cap":                1.00,
    "class_caps":               {"equity": 0.65, "gold": 0.20},
    "cb_daily_halve_loss":      0.02,
    "cb_daily_flatten_loss":    0.03,
    "cb_weekly_resize_loss":    0.05,
    "cb_max_drawdown_halt":     0.10,
    "cb_halve_factor":          0.50,
    "cb_weekly_resize_factor":  0.50,
    "weekly_lookback_days":     5,
    "max_position_correlation": 0.80,
    "correlation_lookback":     60,
    "lock_file_path":           "ignored_overridden_by_fixture",
}

REGIME_CAPS = {
    "Bull":    1.25,
    "Neutral": 1.00,
    "Bear":    0.00,
    "Crash":   0.00,
}


@pytest.fixture
def rm(tmp_path) -> RiskManager:
    """Fresh RiskManager with an isolated lock-file path per test."""
    lock = tmp_path / "RISK_HALT.lock"
    return RiskManager(
        cfg=dict(BASE_CFG),
        regime_leverage_caps=dict(REGIME_CAPS),
        lock_file_path=str(lock),
    )


# ---------------------------------------------------------------------------
# 1. Circuit breakers — each trigger level individually
# ---------------------------------------------------------------------------

class TestCircuitBreakers:

    def test_no_breaker_when_flat(self, rm):
        rm.start_new_day(100_000)
        level = rm.update_equity(100_000)
        assert level == CBLevel.NONE
        assert rm.circuit_breaker_active() is False

    def test_minus_2pct_day_halves(self, rm):
        rm.start_new_day(100_000)
        level = rm.update_equity(98_000)   # -2.0%
        assert level == CBLevel.HALVE
        assert rm.size_scaling_factor() == pytest.approx(0.50)

    def test_minus_3pct_day_flattens(self, rm):
        rm.start_new_day(100_000)
        level = rm.update_equity(97_000)   # -3.0%
        assert level == CBLevel.FLATTEN
        assert rm.should_flatten() is True
        assert rm.size_scaling_factor() == 0.0

    def test_minus_5pct_week_resizes(self, rm):
        # Build a week of declining closes ending -5% below the window start.
        rm.start_new_day(100_000)
        for eq in [100_000, 100_000, 100_000, 100_000, 100_000]:
            rm.end_of_day(eq)
        # Current equity 95_000 vs window-start 100_000 = -5%
        level = rm.update_equity(95_000)
        assert level >= CBLevel.WEEKLY_RESIZE
        assert rm.size_scaling_factor() <= 0.50

    def test_minus_10pct_drawdown_halts(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)          # establish peak
        rm.start_new_day(100_000)
        level = rm.update_equity(90_000)   # -10% from peak
        assert level == CBLevel.HALT
        assert rm.is_halted() is True

    def test_breaker_severity_ordering(self):
        assert CBLevel.NONE < CBLevel.HALVE < CBLevel.WEEKLY_RESIZE < CBLevel.FLATTEN < CBLevel.HALT


# ---------------------------------------------------------------------------
# 2. Lock file mechanism
# ---------------------------------------------------------------------------

class TestLockFile:

    def test_lock_file_written_on_halt(self, rm, tmp_path):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(
            89_000,
            open_positions={"SPY": {"unrealised_pnl": -8000.0, "qty": 100}},
            regime_label="Crash",
            market_note="synthetic crash test",
        )
        assert rm._lock_path.exists()

    def test_lock_file_contents(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(
            88_000,
            open_positions={"TSLA": {"unrealised_pnl": -9000.0}},
            regime_label="Bear",
            market_note="vol spike",
        )
        data = json.loads(rm._lock_path.read_text())
        assert data["event"] == "MAX_DRAWDOWN_HALT"
        assert "what_happened" in data
        assert data["regime_at_halt"] == "Bear"
        assert data["market_conditions"] == "vol spike"
        assert data["positions_at_halt"]            # culprit positions recorded
        assert "how_to_resume" in data

    def test_halt_prevents_restart(self, rm, tmp_path):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(85_000)            # halt
        assert rm.is_halted() is True

        # A brand-new RiskManager pointing at the same lock file starts halted.
        rm2 = RiskManager(
            cfg=dict(BASE_CFG),
            regime_leverage_caps=dict(REGIME_CAPS),
            lock_file_path=str(rm._lock_path),
        )
        assert rm2.is_halted() is True

    def test_clear_lock_allows_resume(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(85_000)
        assert rm.is_halted() is True
        assert rm.clear_lock() is True
        assert rm.is_halted() is False
        assert rm.circuit_breaker_level() == CBLevel.NONE

    def test_halt_is_sticky_until_cleared(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(85_000)            # halt
        # Even if equity recovers, stays halted while lock exists.
        level = rm.update_equity(101_000)
        assert level == CBLevel.HALT
        assert rm.is_halted() is True


# ---------------------------------------------------------------------------
# 3. Position sizing (1% risk rule)
# ---------------------------------------------------------------------------

class TestPositionSizing:

    def test_basic_one_percent_risk(self, rm):
        # portfolio 100k, risk 1% = $1000; stop distance $2 → 500 shares,
        # but capped by max_position_size 10% = $10k / $100 = 100 shares.
        qty = rm.size_position(entry_price=100.0, stop_price=98.0, portfolio_value=100_000)
        assert qty == 100   # position cap binds

    def test_risk_limit_binds_when_below_cap(self, rm):
        # Wide stop so the 1% risk limit is the binding constraint.
        # risk $1000 / stop distance $50 = 20 shares; cap = $10k/$100 = 100.
        qty = rm.size_position(entry_price=100.0, stop_price=50.0, portfolio_value=100_000)
        assert qty == 20

    def test_scales_with_portfolio_value(self, rm):
        small = rm.size_position(100.0, 50.0, 100_000)   # 20 shares
        big   = rm.size_position(100.0, 50.0, 200_000)   # risk $2000/$50 = 40
        assert big == 2 * small

    def test_zero_stop_distance_returns_zero(self, rm):
        assert rm.size_position(100.0, 100.0, 100_000) == 0

    def test_halve_breaker_reduces_size(self, rm):
        base = rm.size_position(100.0, 50.0, 100_000)    # 20 shares
        rm.start_new_day(100_000)
        rm.update_equity(98_000)                          # HALVE active
        reduced = rm.size_position(100.0, 50.0, 100_000)
        assert reduced == base // 2

    def test_flatten_breaker_zero_size(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(97_000)                          # FLATTEN
        assert rm.size_position(100.0, 50.0, 100_000) == 0


# ---------------------------------------------------------------------------
# 4. Leverage enforcement per regime
# ---------------------------------------------------------------------------

class TestLeverageEnforcement:

    def test_bull_allows_higher_leverage(self, rm):
        assert rm.max_leverage_for_regime("Bull") == pytest.approx(1.25)

    def test_neutral_caps_at_one(self, rm):
        assert rm.max_leverage_for_regime("Neutral") == pytest.approx(1.00)

    def test_bear_zero_leverage(self, rm):
        assert rm.max_leverage_for_regime("Bear") == pytest.approx(0.00)

    def test_unknown_regime_uses_global_cap(self, rm):
        assert rm.max_leverage_for_regime("Mystery") == pytest.approx(BASE_CFG["max_leverage"])

    def test_partial_breaker_reduces_leverage(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(98_000)                          # HALVE
        # Bull cap 1.25 × 0.5 scaling = 0.625
        assert rm.max_leverage_for_regime("Bull") == pytest.approx(0.625)


# ---------------------------------------------------------------------------
# 5. Order validation
# ---------------------------------------------------------------------------

class TestOrderValidation:

    def test_validate_book_rejects_gross_exposure(self, rm):
        validation = rm.validate_book({"SPY": 0.60, "QQQ": 0.50})
        assert validation.approved is False
        assert "gross" in validation.reason.lower()

    def test_validate_book_rejects_per_name_exposure(self, rm):
        validation = rm.validate_book({"SPY": 0.50})
        assert validation.approved is False
        assert "per-name" in validation.reason.lower()

    def test_valid_order_approved(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        v = rm.validate_order(
            ticker="SPY", qty=50, price=100.0,
            portfolio_value=100_000, buying_power=100_000,
            proposed_leverage=1.0, regime_label="Neutral",
        )
        assert v.approved is True
        assert v.approved_qty == 50

    def test_oversized_position_rejected(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        # 200 shares * $100 = $20k > 10% cap ($10k)
        v = rm.validate_order(
            "SPY", 200, 100.0, 100_000, 100_000, 1.0, "Neutral",
        )
        assert v.approved is False
        assert "exceeds cap" in v.reason

    def test_excess_leverage_rejected(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        v = rm.validate_order(
            "SPY", 50, 100.0, 100_000, 100_000,
            proposed_leverage=2.0, regime_label="Neutral",   # cap is 1.0
        )
        assert v.approved is False
        assert "leverage" in v.reason

    def test_insufficient_buying_power_rejected(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        v = rm.validate_order(
            "SPY", 50, 100.0, 100_000,
            buying_power=1_000,                # only $1k, need $5k
            proposed_leverage=1.0, regime_label="Neutral",
        )
        assert v.approved is False
        assert "buying power" in v.reason

    def test_order_rejected_when_halted(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rm.start_new_day(100_000)
        rm.update_equity(85_000)              # HALT
        v = rm.validate_order(
            "SPY", 10, 100.0, 100_000, 100_000, 1.0, "Neutral",
        )
        assert v.approved is False
        assert "circuit breaker" in v.reason


# ---------------------------------------------------------------------------
# 6. Correlation checks
# ---------------------------------------------------------------------------

class TestCorrelationChecks:

    def test_highly_correlated_position_rejected(self, rm):
        rm._cfg["enable_correlation_check"] = True
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        base = np.linspace(0, 1, 60) + np.random.default_rng(0).normal(0, 0.001, 60)
        existing = {"AAA": base}
        new = base * 1.0001          # almost identical → corr ~1.0
        v = rm.validate_order(
            "BBB", 10, 100.0, 100_000, 100_000, 1.0, "Neutral",
            existing_returns=existing, new_returns=new,
        )
        assert v.approved is False
        assert "correlation" in v.reason

    def test_uncorrelated_position_approved(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        rng = np.random.default_rng(1)
        existing = {"AAA": rng.normal(0, 1, 60)}
        new = rng.normal(0, 1, 60)   # independent → low corr
        v = rm.validate_order(
            "BBB", 10, 100.0, 100_000, 100_000, 1.0, "Neutral",
            existing_returns=existing, new_returns=new,
        )
        assert v.approved is True

    def test_correlation_skipped_when_no_existing(self, rm):
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        v = rm.validate_order(
            "BBB", 10, 100.0, 100_000, 100_000, 1.0, "Neutral",
            existing_returns=None, new_returns=None,
        )
        assert v.approved is True


# ---------------------------------------------------------------------------
# 8. Config flags — daily-breaker switch & regime-cap switch
# ---------------------------------------------------------------------------

class TestRiskConfigFlags:

    def _rm_with(self, tmp_path, **extra) -> RiskManager:
        cfg = dict(BASE_CFG)
        cfg.update(extra)
        return RiskManager(
            cfg=cfg,
            regime_leverage_caps=dict(REGIME_CAPS),
            lock_file_path=str(tmp_path / "lock"),
        )

    def test_daily_breakers_disabled_no_halve(self, tmp_path):
        rm = self._rm_with(tmp_path, cb_daily_enabled=False)
        rm.start_new_day(100_000)
        assert rm.update_equity(98_000) == CBLevel.NONE      # -2% → no HALVE

    def test_daily_breakers_disabled_no_flatten(self, tmp_path):
        rm = self._rm_with(tmp_path, cb_daily_enabled=False)
        rm.start_new_day(100_000)
        assert rm.update_equity(96_500) == CBLevel.NONE      # -3.5% → no FLATTEN

    def test_weekly_breaker_unaffected_by_daily_flag(self, tmp_path):
        rm = self._rm_with(tmp_path, cb_daily_enabled=False)
        rm.start_new_day(100_000)
        for eq in (100_000, 99_500, 99_000, 98_500, 98_000, 97_500):
            rm.end_of_day(eq)
        # New day so the -2.6% move is not a daily loss; weekly is -5.5%.
        rm.start_new_day(97_500)
        level = rm.update_equity(94_500)
        assert level == CBLevel.WEEKLY_RESIZE

    def test_halt_breaker_unaffected_by_daily_flag(self, tmp_path):
        rm = self._rm_with(tmp_path, cb_daily_enabled=False)
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        assert rm.update_equity(89_000) == CBLevel.HALT      # -11% drawdown

    def test_daily_breakers_enabled_by_default(self, tmp_path):
        rm = self._rm_with(tmp_path)          # no flag in cfg → enabled
        rm.start_new_day(100_000)
        assert rm.update_equity(98_000) == CBLevel.HALVE

    def test_regime_caps_ignored_when_flag_off(self, tmp_path):
        rm = self._rm_with(tmp_path, use_regime_leverage_caps=False)
        # "Bear" caps at 0.0 when enabled; with the flag off the global
        # max_leverage (1.0) applies to every regime label.
        assert rm.max_leverage_for_regime("Bear") == pytest.approx(1.0)
        assert rm.max_leverage_for_regime("Bull") == pytest.approx(1.0)

    def test_regime_caps_apply_by_default(self, tmp_path):
        rm = self._rm_with(tmp_path)
        assert rm.max_leverage_for_regime("Bear") == pytest.approx(0.0)
