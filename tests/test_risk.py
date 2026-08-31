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

from core.risk_manager import CBLevel, RiskManager
from settings import config


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BASE_CFG = {
    "max_position_size":        0.10,
    "max_leverage":             1.0,
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
# 3. Leverage enforcement per regime
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
# 4. Order validation
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
# 5. Class-cap clipping for the single-asset live path (Befund 7)
# ---------------------------------------------------------------------------

class TestClipTargetWeight:
    """main.TradingSystem.run_once decides one ticker at a time and can only
    move THAT ticker's position; it used to reject the whole candidate book
    outright on a class/gross breach. With two same-class tickers, whichever
    was decided first durably won the shared budget: the second was zeroed
    every time even when a smaller position of its own would fit, since the
    lost budget never came back once a neighbour held it. clip_target_weight
    replaces the flat reject with "take whatever headroom is left," so a
    ticker decided later still gets its fair share instead of nothing."""

    def _rm(self, tmp_path, **over):
        # BASE_CFG's max_position_size (0.10) would itself clip every "other"
        # weight used below; raise it so per_name_cap is what actually binds
        # in the one test that means to exercise it.
        cfg = {**BASE_CFG, "max_position_size": 1.0, "per_name_cap": 1.0,
               "class_caps": {"equity": 0.70, "gold": 0.20}, **over}
        return RiskManager(cfg=cfg, lock_file_path=str(tmp_path / "l.lock"),
                           persist_state=False)

    def test_no_other_positions_passes_through_unclipped(self, tmp_path):
        rm = self._rm(tmp_path)
        assert rm.clip_target_weight("SPY", 0.30, {}) == pytest.approx(0.30)

    def test_clips_to_remaining_class_budget(self, tmp_path, monkeypatch):
        """SPY holds 0.50 of a 0.70 equity cap; QQQ wants 0.50 but only 0.20
        of headroom is left — the literal Befund 7 scenario, previously a
        flat rejection to zero regardless of processing order."""
        monkeypatch.setitem(config.UNIVERSE["assets"], "QQQ",
                            {"asset_class": "equity"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path)
        clipped = rm.clip_target_weight("QQQ", 0.50, {"SPY": 0.50})
        assert clipped == pytest.approx(0.20)

    def test_second_ticker_is_not_durably_zeroed(self, tmp_path, monkeypatch):
        """Symmetry check: swap which ticker is "other" — both get a
        positive share of the budget, neither is durably locked to zero."""
        monkeypatch.setitem(config.UNIVERSE["assets"], "QQQ",
                            {"asset_class": "equity"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path)
        spy_clip = rm.clip_target_weight("SPY", 0.50, {"QQQ": 0.50})
        assert spy_clip == pytest.approx(0.20)

    def test_other_ticker_in_a_different_class_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "GLD",
                            {"asset_class": "gold"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path)
        assert rm.clip_target_weight("SPY", 0.60, {"GLD": 0.20}) == pytest.approx(0.60)

    def test_per_name_cap_binds_independent_of_other_positions(self, tmp_path):
        rm = self._rm(tmp_path, per_name_cap=0.25)
        assert rm.clip_target_weight("SPY", 0.60, {}) == pytest.approx(0.25)

    def test_gross_cap_binds_across_all_classes(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "IEF",
                            {"asset_class": "bonds"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path, gross_cap=0.80, per_name_cap=1.0,
                      class_caps={"equity": 1.0, "bonds": 1.0})
        assert rm.clip_target_weight("SPY", 0.50, {"IEF": 0.50}) == pytest.approx(0.30)

    def test_economic_cap_scales_by_leverage(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "QLD",
                            {"asset_class": "levered_equity", "leverage": 2.0})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity", "leverage": 1.0})
        rm = self._rm(tmp_path, economic_gross_cap=1.20, per_name_cap=1.0,
                      class_caps={"equity": 1.0, "levered_equity": 1.0})
        # 0.60 economic already used by SPY; 0.60 left / leverage 2.0 = 0.30.
        clipped = rm.clip_target_weight("QLD", 0.50, {"SPY": 0.60})
        assert clipped == pytest.approx(0.30)

    def test_no_budget_left_clips_to_zero_not_a_rejection(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "QQQ",
                            {"asset_class": "equity"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path)
        assert rm.clip_target_weight("QQQ", 0.50, {"SPY": 0.70}) == pytest.approx(0.0)

    def test_zero_or_negative_target_clips_to_zero(self, tmp_path):
        rm = self._rm(tmp_path)
        assert rm.clip_target_weight("SPY", 0.0, {}) == 0.0
        assert rm.clip_target_weight("SPY", -0.10, {}) == 0.0

    def test_clipped_candidate_book_always_passes_validate_book(self, tmp_path, monkeypatch):
        """The two are meant to agree: clip first, and validate_book on the
        clipped result should never itself reject."""
        monkeypatch.setitem(config.UNIVERSE["assets"], "QQQ",
                            {"asset_class": "equity"})
        monkeypatch.setitem(config.UNIVERSE["assets"], "SPY",
                            {"asset_class": "equity"})
        rm = self._rm(tmp_path)
        others = {"SPY": 0.50}
        clipped = rm.clip_target_weight("QQQ", 0.50, others)
        book = {**others, "QQQ": clipped}
        assert rm.validate_book(book).approved


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
# 7. Config flags — daily-breaker switch & regime-cap switch
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


# ---------------------------------------------------------------------------
# 8. Cross-restart state — the deployed bot is a fresh process every day
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """`deploy/regime-trader.service` runs `main.py --once` as a oneshot, so
    every trading day gets a brand-new RiskManager.  With peak equity held only
    in memory, `startup()` re-anchored the peak to the CURRENT equity each
    morning: the measured drawdown was permanently 0.00% and NO drawdown
    breaker could ever fire.  Verified below against a -45% crash."""

    CFG = {**BASE_CFG, "cb_daily_enabled": False, "cb_max_drawdown_halt": 0.20}
    CRASH = [100_000, 97_000, 92_000, 88_000, 84_000, 79_000, 74_000, 66_000]

    def _daily_oneshot_run(self, tmp_path, equity, persist):
        """One process lifecycle: construct → start_new_day → update_equity."""
        rm = RiskManager(cfg=dict(self.CFG),
                         lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                         persist_state=persist)
        rm.start_new_day(equity)            # what TradingSystem.startup() does
        level = rm.update_equity(equity)    # what run_portfolio_once() does
        rm.end_of_day(equity)
        return rm, level

    def test_without_persistence_no_breaker_survives_a_crash(self, tmp_path):
        worst = CBLevel.NONE
        for equity in self.CRASH:
            rm, level = self._daily_oneshot_run(tmp_path, equity, persist=False)
            worst = max(worst, level)
            assert rm.state().drawdown == pytest.approx(0.0)
        assert worst == CBLevel.NONE            # the bug, pinned

    def test_with_persistence_the_halt_fires_across_restarts(self, tmp_path):
        levels = [self._daily_oneshot_run(tmp_path, e, persist=True)[1]
                  for e in self.CRASH]
        assert CBLevel.HALT in levels
        assert (tmp_path / "RISK_HALT.lock").exists()

    def test_peak_equity_survives_a_restart(self, tmp_path):
        self._daily_oneshot_run(tmp_path, 120_000, persist=True)
        rm, _ = self._daily_oneshot_run(tmp_path, 110_000, persist=True)
        assert rm.state().peak_equity == pytest.approx(120_000)
        assert rm.state().drawdown == pytest.approx(-1 / 12)

    def test_weekly_breaker_sees_history_from_previous_processes(self, tmp_path):
        for equity in (100_000, 99_500, 99_000, 98_500, 98_000, 97_500):
            self._daily_oneshot_run(tmp_path, equity, persist=True)
        rm = RiskManager(cfg=dict(self.CFG),
                         lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                         persist_state=True)
        rm.start_new_day(97_500)
        assert rm.update_equity(94_500) == CBLevel.WEEKLY_RESIZE

    def test_state_file_written_next_to_the_lock(self, tmp_path):
        rm, _ = self._daily_oneshot_run(tmp_path, 100_000, persist=True)
        assert rm.state_path.exists()
        assert json.loads(rm.state_path.read_text())["peak_equity"] == 100_000

    def test_corrupt_state_file_does_not_crash_startup(self, tmp_path):
        (tmp_path / "RISK_HALT_state.json").write_text("{not json")
        rm = RiskManager(cfg=dict(self.CFG),
                         lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                         persist_state=True)
        rm.start_new_day(100_000)
        assert rm.update_equity(100_000) == CBLevel.NONE

    def test_clear_lock_reanchors_the_peak_so_the_bot_can_resume(self, tmp_path):
        """Without re-anchoring, the restored peak would re-trigger the halt on
        the next bar and the operator could never restart the bot."""
        rm = RiskManager(cfg=dict(self.CFG),
                         lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                         persist_state=True)
        rm.start_new_day(100_000)
        rm.update_equity(100_000)
        assert rm.update_equity(75_000) == CBLevel.HALT

        assert rm.clear_lock() is True
        assert rm.state().peak_equity == pytest.approx(75_000)

        fresh = RiskManager(cfg=dict(self.CFG),
                            lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                            persist_state=True)
        fresh.start_new_day(75_000)
        assert fresh.update_equity(75_000) == CBLevel.NONE

    def test_backtests_do_not_write_state_files(self, tmp_path):
        rm, _ = self._daily_oneshot_run(tmp_path, 100_000, persist=False)
        assert not rm.state_path.exists()

    def test_state_readable_before_any_equity_update(self, tmp_path):
        """A caller that just wants to inspect a restored peak (e.g. an
        operator tool reviewing a halt before deciding whether to clear it)
        must be able to call .state() right after construction — before
        start_new_day()/update_equity() have ever run. _current_equity is
        only set by those, while _peak_equity is restored from disk, so
        _drawdown() used to divide against a None current equity."""
        self._daily_oneshot_run(tmp_path, 100_000, persist=True)
        fresh = RiskManager(cfg=dict(self.CFG),
                            lock_file_path=str(tmp_path / "RISK_HALT.lock"),
                            persist_state=True)
        state = fresh.state()
        assert state.peak_equity == pytest.approx(100_000)
        assert state.drawdown == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 9. Economic (leverage-adjusted) book exposure
# ---------------------------------------------------------------------------

class TestEconomicExposureCap:
    """Notional weights understate a levered ETF: 0.40 in a 2x fund is 0.80 of
    market exposure, which the notional gross cap cannot see."""

    def _rm(self, tmp_path, **over):
        cfg = {**BASE_CFG, "gross_cap": 1.0, "per_name_cap": 0.5,
               "max_position_size": 0.5,   # per-name cap is min() of both
               "class_caps": {}, "economic_gross_cap": 1.5, **over}
        return RiskManager(cfg=cfg, lock_file_path=str(tmp_path / "l.lock"),
                           persist_state=False)

    def test_levered_book_within_the_cap_is_approved(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "L2",
                            {"asset_class": "x", "validated": True,
                             "role": "sleeve", "leverage": 2.0})
        v = self._rm(tmp_path).validate_book({"SPY": 0.3, "L2": 0.4})   # 1.10
        assert v.approved, v.reason

    def test_levered_book_beyond_the_cap_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "L3",
                            {"asset_class": "x", "validated": True,
                             "role": "sleeve", "leverage": 3.0})
        v = self._rm(tmp_path).validate_book({"SPY": 0.2, "L3": 0.5})   # 1.70
        assert not v.approved
        assert "economic exposure" in v.reason

    def test_notional_gross_cap_alone_would_have_passed_it(self, tmp_path, monkeypatch):
        """Pins WHY the second cap exists: gross is only 0.70 here."""
        monkeypatch.setitem(config.UNIVERSE["assets"], "L3",
                            {"asset_class": "x", "validated": True,
                             "role": "sleeve", "leverage": 3.0})
        book = {"SPY": 0.2, "L3": 0.5}
        assert sum(book.values()) <= 1.0
        rm = self._rm(tmp_path, economic_gross_cap=None)
        assert rm.validate_book(book).approved

    def test_unlevered_book_is_unaffected(self, tmp_path):
        v = self._rm(tmp_path).validate_book({"SPY": 0.5, "GLD": 0.2})
        assert v.approved, v.reason


# ---------------------------------------------------------------------------
# Book vol target — the realised-vol window that feeds
# core.sleeves.vol_target_scale (owner decision 2026-08-31, finding K1)
# ---------------------------------------------------------------------------

class TestBookReturnWindow:

    def test_end_of_day_records_book_returns(self, rm):
        rm.start_new_day(100_000.0)
        rm.end_of_day(100_000.0)
        rm.end_of_day(101_000.0)
        rm.end_of_day(99_990.0)

        returns = rm.book_returns()
        assert len(returns) == 2
        assert returns[0] == pytest.approx(0.01)
        assert returns[1] == pytest.approx(99_990.0 / 101_000.0 - 1.0)

    def test_first_close_produces_no_return(self, rm):
        rm.end_of_day(100_000.0)
        assert rm.book_returns() == []

    def test_window_outlives_the_weekly_breaker_history(self, rm):
        """The weekly breaker keeps 6 closes; the vol target needs 21 returns.

        Before 2026-08-31 end_of_day() trimmed to weekly_lookback+1, so a
        21-day realised-vol estimate could never fill and the vol target would
        have been a permanent no-op in live trading.
        """
        for i in range(40):
            rm.end_of_day(100_000.0 * (1.0 + 0.001 * i))

        assert len(rm._equity_history) == BASE_CFG["weekly_lookback_days"] + 1
        assert len(rm.book_returns()) >= 21

    def test_cash_transfer_is_not_recorded_as_a_book_return(self, rm):
        """A deposit doubles equity overnight. The book cannot do that, and
        letting it into the window would crush exposure for a full lookback."""
        rm.end_of_day(100_000.0)
        rm.end_of_day(101_000.0)
        rm.end_of_day(500_000.0)      # deposit, not a +395% day
        rm.end_of_day(505_000.0)

        returns = rm.book_returns()
        assert all(abs(r) < 0.5 for r in returns)
        assert returns[-1] == pytest.approx(505_000.0 / 500_000.0 - 1.0)

    def test_window_survives_clear_halt(self, rm):
        """clear_lock() wipes the weekly breaker's baseline on purpose. It must
        NOT wipe the realised-vol window: resuming after a crash with an empty
        window would run the book at full exposure on the most volatile days
        in years — the precise scenario the target exists for."""
        rm.start_new_day(100_000.0)
        for i in range(30):
            rm.end_of_day(100_000.0 * (1.0 - 0.01 * i))
        before = rm.book_returns()
        assert len(before) >= 21

        rm.update_equity(50_000.0)          # deep drawdown -> HALT + lock file
        assert rm.is_halted()
        rm.clear_lock()

        assert rm._equity_history == []
        assert rm.book_returns() == before

    def test_window_is_a_copy_not_the_live_list(self, rm):
        rm.end_of_day(100_000.0)
        rm.end_of_day(101_000.0)
        rm.book_returns().append(99.0)
        assert all(abs(r) < 1.0 for r in rm.book_returns())

    def test_book_vol_scale_is_one_before_the_window_fills(self, rm):
        rm.end_of_day(100_000.0)
        rm.end_of_day(101_000.0)
        assert rm.book_vol_scale() == 1.0

    def test_book_vol_scale_shrinks_a_volatile_book(self, rm):
        rng = np.random.default_rng(5)
        equity = 100_000.0
        rm.end_of_day(equity)
        for r in rng.normal(0.0, 0.45 / (252 ** 0.5), 60):
            equity *= 1.0 + float(r)
            rm.end_of_day(equity)

        scale = rm.book_vol_scale()
        assert 0.0 < scale < 1.0

    def test_book_vol_scale_matches_the_shared_helper(self, rm):
        """Live scaling and backtest scaling must be the same function."""
        from core import sleeves

        rng = np.random.default_rng(9)
        equity = 100_000.0
        rm.end_of_day(equity)
        for r in rng.normal(0.0, 0.30 / (252 ** 0.5), 40):
            equity *= 1.0 + float(r)
            rm.end_of_day(equity)

        assert rm.book_vol_scale() == pytest.approx(
            sleeves.vol_target_scale(rm.book_returns())
        )

    def test_window_survives_a_process_restart(self, tmp_path):
        """The bot is deployed as a daily --once oneshot: without persistence
        the realised-vol window would reset to empty every morning and the
        target would never bind, exactly like the drawdown breaker before its
        peak equity was persisted."""
        lock = tmp_path / "RISK_HALT.lock"

        first = RiskManager(cfg=dict(BASE_CFG), lock_file_path=str(lock))
        equity = 100_000.0
        first.end_of_day(equity)
        for i in range(30):
            equity *= 1.0 + (0.01 if i % 2 else -0.012)
            first.end_of_day(equity)
        expected = first.book_returns()

        second = RiskManager(cfg=dict(BASE_CFG), lock_file_path=str(lock))
        assert second.book_returns() == pytest.approx(expected)
        assert second.book_vol_scale() == pytest.approx(first.book_vol_scale())
