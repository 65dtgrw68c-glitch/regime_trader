"""
Integration tests for main.py — the orchestrator wiring every component.

Covers:
  * Full startup sequence end-to-end with simulated data
  * Main loop with simulated bars
  * All four error-handling paths
  * Graceful shutdown
  * AlertManager behaviour (new in this layer)

The broker boundary (client / executor / data feed) is faked; the core
analytics (FeatureEngineer, HMMEngine, RegimeOrchestrator, RiskManager) are
the REAL components, so this genuinely exercises the integration.

Run with:  pytest tests/test_main.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_manager import RiskManager
from main import TradingSystem, ShutdownReport
from monitoring.alerts import (
    AlertManager, SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_WARNING,
)


# ---------------------------------------------------------------------------
# Synthetic data + fakes
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 340, seed: int = 21) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n)
    vol = np.select(
        [np.arange(n) < n // 3, np.arange(n) < 2 * n // 3],
        [0.006, 0.018], default=0.011,
    )
    log_ret = rng.normal(0.0004, vol, n)
    close = 100 * np.exp(np.cumsum(log_ret))
    wig = rng.uniform(0.001, 0.004, n)
    return pd.DataFrame({
        "open":   close * (1 + rng.normal(0, 0.001, n)),
        "high":   close * (1 + wig),
        "low":    close * (1 - wig),
        "close":  close,
        "volume": rng.integers(1_000_000, 4_000_000, n).astype(float),
    }, index=dates)


class FakeClient:
    """Stand-in for AlpacaClient with no network."""
    def __init__(self, equity=100_000.0, is_open=True, status="ACTIVE"):
        self._equity = equity
        self._is_open = is_open
        self._status = status
        self.trading = MagicMock()
        self.trading.get_all_positions.return_value = []

    def verify_connection(self):
        return self._status == "ACTIVE"

    def get_account(self):
        return {
            "equity": self._equity, "buying_power": self._equity,
            "cash": self._equity, "portfolio_value": self._equity,
            "status": self._status,
        }

    def get_clock(self):
        return {"is_open": self._is_open, "next_open": None, "next_close": None}

    def is_market_open(self):
        return self._is_open


class FakeDataFeed:
    def __init__(self, training_data: pd.DataFrame):
        self._td = training_data

    def get_training_data(self, ticker, years=2.0):
        return self._td

    def get_latest_bar(self, ticker):
        return self._td.iloc[-1]

    def start_stream(self, *a, **k):
        pass


def _make_system(tmp_path, tickers=("AAA",), equity=100_000.0,
                 is_open=True, status="ACTIVE", training=None):
    """Build a TradingSystem with fakes at the broker boundary + real core."""
    client = FakeClient(equity=equity, is_open=is_open, status=status)
    feed = FakeDataFeed(training if training is not None else _make_ohlcv())
    risk = RiskManager(lock_file_path=str(tmp_path / "RISK_HALT.lock"))
    executor = MagicMock()
    executor.submit_order.return_value = "oid-1"
    alerts = MagicMock()
    sys_ = TradingSystem(
        tickers=list(tickers),
        client=client,
        data_feed=feed,
        order_executor=executor,
        risk_manager=risk,
        alert_manager=alerts,
        max_api_retries=2,
    )
    sys_._retry_delay = 0.0   # no real backoff sleeping in tests
    return sys_


@pytest.fixture
def started_system(tmp_path):
    sys_ = _make_system(tmp_path)
    assert sys_.startup() is True
    return sys_


# ---------------------------------------------------------------------------
# 1. Startup sequence
# ---------------------------------------------------------------------------

class TestStartup:

    def test_startup_success(self, tmp_path):
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True

    def test_startup_initialises_ticker_state(self, tmp_path):
        sys_ = _make_system(tmp_path)
        sys_.startup()
        assert "AAA" in sys_._states
        assert sys_._states["AAA"].engine is not None

    def test_startup_records_equity(self, tmp_path):
        sys_ = _make_system(tmp_path, equity=100_000.0)
        sys_.startup()
        assert sys_._equity == pytest.approx(100_000.0)

    def test_startup_sets_market_status(self, tmp_path):
        sys_ = _make_system(tmp_path, is_open=True)
        sys_.startup()
        assert sys_._market_status == "open"

    def test_startup_market_closed(self, tmp_path):
        sys_ = _make_system(tmp_path, is_open=False)
        sys_.startup()
        assert sys_._market_status == "closed"

    def test_startup_fails_with_no_tickers(self, tmp_path):
        sys_ = _make_system(tmp_path, tickers=())
        assert sys_.startup() is False

    def test_startup_fails_when_account_inactive(self, tmp_path):
        sys_ = _make_system(tmp_path, status="INACTIVE")
        assert sys_.startup() is False

    def test_startup_fails_when_unfunded(self, tmp_path):
        sys_ = _make_system(tmp_path, equity=0.0)
        assert sys_.startup() is False

    def test_startup_blocked_by_lock_file(self, tmp_path):
        lock = tmp_path / "RISK_HALT.lock"
        lock.write_text("{}")
        client = FakeClient()
        feed = FakeDataFeed(_make_ohlcv())
        risk = RiskManager(lock_file_path=str(lock))
        sys_ = TradingSystem(
            tickers=["AAA"], client=client, data_feed=feed,
            order_executor=MagicMock(), risk_manager=risk,
            alert_manager=MagicMock(),
        )
        assert sys_.startup() is False


# ---------------------------------------------------------------------------
# 2. Main loop
# ---------------------------------------------------------------------------

class TestMainLoop:

    def test_run_once_returns_decision(self, started_system):
        bar = _make_ohlcv(1, seed=99).iloc[-1]
        decision = started_system.run_once("AAA", bar)
        assert decision["ticker"] == "AAA"
        assert "action" in decision

    def test_run_once_unknown_ticker(self, started_system):
        bar = _make_ohlcv(1).iloc[-1]
        decision = started_system.run_once("ZZZ", bar)
        assert decision["action"] == "none"

    def test_run_once_increments_bar_count(self, started_system):
        before = started_system._bars_processed
        bar = _make_ohlcv(1, seed=7).iloc[-1]
        started_system.run_once("AAA", bar)
        assert started_system._bars_processed == before + 1

    def test_run_with_finite_bar_source(self, started_system):
        feed = _make_ohlcv(5, seed=5)
        bars = iter([{"AAA": feed.iloc[i]} for i in range(5)])

        def source():
            return next(bars, None)

        started_system.run(bar_source=source)
        assert started_system._bars_processed >= 5

    def test_run_respects_max_iterations(self, started_system):
        feed = _make_ohlcv(50, seed=3)
        counter = {"i": 0}

        def source():
            i = counter["i"]
            counter["i"] += 1
            return {"AAA": feed.iloc[i % len(feed)]}

        started_system.run(bar_source=source, max_iterations=3)
        # 3 iterations × 1 ticker
        assert started_system._bars_processed == 3

    def test_run_once_produces_known_action(self, started_system):
        bar = _make_ohlcv(1, seed=1).iloc[-1]
        decision = started_system.run_once("AAA", bar)
        assert decision["action"] in {
            "none", "waiting_for_stable_regime", "order_submitted",
            "rejected_by_risk", "halted", "flatten",
        }


# ---------------------------------------------------------------------------
# 3. Error handling — four failure modes
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_api_down_retries_then_pauses(self, tmp_path):
        """Failure mode 1: API unreachable → retry, then pause + alert."""
        sys_ = _make_system(tmp_path)
        sys_._retry_delay = 0.0
        boom = MagicMock(side_effect=RuntimeError("api down"))
        boom.__name__ = "boom"
        result = sys_._safe_call(boom)
        assert result is None
        assert sys_._paused is True
        assert boom.call_count == sys_._max_api_retries
        sys_._alerts.alert.assert_called()

    def test_hmm_error_falls_back_to_last_regime(self, started_system):
        """Failure mode 2: HMM error → fall back to last stable regime."""
        state = started_system._states["AAA"]
        state.last_stable_regime = 2
        state.last_stable_label = "Bull"
        # Force the engine to raise on update
        state.engine.update = MagicMock(side_effect=RuntimeError("hmm boom"))
        idx, label, proba, high_unc = started_system._safe_regime(state, np.zeros(10))
        assert idx == 2
        assert label == "Bull"
        assert high_unc is True
        assert abs(proba.sum() - 1.0) < 1e-9

    def test_data_drop_pauses_and_alerts(self, tmp_path):
        """Failure mode 3: data feed drop → reconnect fails → pause + alert."""
        sys_ = _make_system(tmp_path)
        sys_._retry_delay = 0.0
        # Make the connectivity probe fail
        sys_._client.get_clock = MagicMock(side_effect=RuntimeError("no clock"))
        sys_._handle_data_drop(RuntimeError("feed dropped"))
        assert sys_._paused is True
        sys_._alerts.alert.assert_called()

    def test_order_rejection_logs_and_alerts_no_retry(self, tmp_path):
        """Failure mode 4: order rejected → log + alert, no auto-retry."""
        sys_ = _make_system(tmp_path)
        sys_._handle_order_rejection("NVDA", "insufficient buying power")
        sys_._alerts.alert.assert_called_once()
        # Severity should be WARNING for a rejection (not CRITICAL)
        _, kwargs = sys_._alerts.alert.call_args
        args = sys_._alerts.alert.call_args.args
        assert SEVERITY_WARNING in args or kwargs.get("severity") == SEVERITY_WARNING

    def test_submit_empty_id_triggers_rejection_handler(self, tmp_path):
        sys_ = _make_system(tmp_path)
        sys_._executor.submit_order.return_value = ""   # broker returns nothing
        sys_._submit("NVDA", 5, 100.0, "Bull", 0.8)
        sys_._alerts.alert.assert_called()
        assert sys_._orders_submitted == 0


# ---------------------------------------------------------------------------
# 4. Graceful shutdown
# ---------------------------------------------------------------------------

class TestShutdown:

    def test_shutdown_cancels_orders(self, started_system):
        started_system.shutdown(reason="test")
        started_system._executor.cancel_all_open_orders.assert_called()

    def test_shutdown_returns_report(self, started_system):
        report = started_system.shutdown(reason="test")
        assert isinstance(report, ShutdownReport)
        assert report.reason == "test"

    def test_shutdown_report_has_fields(self, started_system):
        report = started_system.shutdown()
        text = report.to_text()
        assert "SHUTDOWN REPORT" in text
        assert "Bars processed" in text

    def test_shutdown_sets_running_false(self, started_system):
        started_system._running = True
        started_system.shutdown()
        assert started_system._running is False

    def test_shutdown_survives_executor_failure(self, started_system):
        started_system._executor.cancel_all_open_orders.side_effect = RuntimeError("x")
        # Should not raise
        report = started_system.shutdown()
        assert isinstance(report, ShutdownReport)


# ---------------------------------------------------------------------------
# 5. AlertManager (introduced with the orchestrator layer)
# ---------------------------------------------------------------------------

class TestAlertManager:

    def test_alert_sends_first_time(self):
        am = AlertManager(email_recipients=[], webhook_url="", cooldown_seconds=60)
        assert am.alert("hello", SEVERITY_INFO) is True

    def test_duplicate_alert_throttled(self):
        am = AlertManager(email_recipients=[], webhook_url="", cooldown_seconds=60)
        assert am.alert("dup", SEVERITY_WARNING, key="k") is True
        assert am.alert("dup", SEVERITY_WARNING, key="k") is False   # throttled

    def test_different_keys_not_throttled(self):
        am = AlertManager(cooldown_seconds=60)
        assert am.alert("a", key="k1") is True
        assert am.alert("b", key="k2") is True

    def test_cooldown_expires(self):
        am = AlertManager(cooldown_seconds=0)   # immediate re-fire allowed
        assert am.alert("x", key="k") is True
        assert am.alert("x", key="k") is True

    def test_format_body_contains_context(self):
        am = AlertManager()
        body = am._format_body("msg", SEVERITY_CRITICAL, {"drawdown": -0.12})
        assert "drawdown" in body
        assert "CRITICAL" in body
