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
from main import (
    TradingSystem, ShutdownReport, run_live, run_once_daily,
    EXIT_OK, EXIT_STARTUP_ERR, EXIT_HALTED, _halt_lock_present,
)
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


def _bars_after(n: int = 1, seed: int = 99, start: str = "2022-06-01") -> pd.DataFrame:
    """
    Bars dated AFTER the default 340-bar training window (which ends
    ~2022-04).  run_once skips bars whose timestamp already exists in the
    history ("stale_bar"), so live-loop tests must feed genuinely new bars.
    """
    df = _make_ohlcv(n, seed=seed)
    df.index = pd.bdate_range(start, periods=n)
    return df


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
        bar = _bars_after(1, seed=99).iloc[-1]
        decision = started_system.run_once("AAA", bar)
        assert decision["ticker"] == "AAA"
        assert "action" in decision

    def test_run_once_unknown_ticker(self, started_system):
        bar = _bars_after(1).iloc[-1]
        decision = started_system.run_once("ZZZ", bar)
        assert decision["action"] == "none"

    def test_run_once_increments_bar_count(self, started_system):
        before = started_system._bars_processed
        bar = _bars_after(1, seed=7).iloc[-1]
        started_system.run_once("AAA", bar)
        assert started_system._bars_processed == before + 1

    def test_run_with_finite_bar_source(self, started_system):
        feed = _bars_after(5, seed=5)
        bars = iter([{"AAA": feed.iloc[i]} for i in range(5)])

        def source():
            return next(bars, None)

        started_system.run(bar_source=source)
        assert started_system._bars_processed >= 5

    def test_run_respects_max_iterations(self, started_system):
        feed = _bars_after(50, seed=3)
        counter = {"i": 0}

        def source():
            i = counter["i"]
            counter["i"] += 1
            return {"AAA": feed.iloc[i % len(feed)]}

        started_system.run(bar_source=source, max_iterations=3)
        # 3 iterations × 1 ticker
        assert started_system._bars_processed == 3

    def test_run_once_produces_known_action(self, started_system):
        bar = _bars_after(1, seed=1).iloc[-1]
        decision = started_system.run_once("AAA", bar)
        assert decision["action"] in {
            "none", "waiting_for_stable_regime", "order_submitted",
            "rejected_by_risk", "halted", "flatten",
            "hold", "no_change", "skipped_market_closed", "stale_bar",
        }

    def test_run_once_diffs_against_existing_position(self, started_system, monkeypatch):
        """
        Regression guard for the position-accumulation bug: the loop must
        trade the DELTA to the target position, not resubmit the full size
        on every bar.  We simulate a filled book and require that the next
        bar never re-buys the whole target again.
        """
        bar = _bars_after(1, seed=13).iloc[-1]
        decision1 = started_system.run_once("AAA", bar)
        if decision1.get("action") != "order_submitted":
            pytest.skip("no entry signal for this seed — nothing to diff")
        target = decision1["qty"]
        assert target > 0

        # Simulate the fill: the book now holds exactly the target quantity.
        monkeypatch.setattr(started_system, "_position_qty", lambda t: target)

        bar2 = _bars_after(2, seed=13).iloc[-1]
        decision2 = started_system.run_once("AAA", bar2)
        # Already at target → at most a small delta may trade, never the
        # full size again (the old loop bought `target` shares EVERY bar).
        if decision2.get("action") == "order_submitted":
            assert decision2["qty"] < target


# ---------------------------------------------------------------------------
# 2b. Bar hygiene — the live loop must act once per NEW bar
# ---------------------------------------------------------------------------

class TestBarHygiene:

    def test_redelivered_bar_is_skipped(self, started_system):
        """
        The poll loop re-delivers the latest (possibly still-forming) bar
        many times per day.  Re-delivery must not re-run the decision
        pipeline (this once produced one duplicate order per minute).
        """
        bar = _bars_after(1, seed=11).iloc[-1]
        started_system.run_once("AAA", bar)
        before = started_system._bars_processed
        decision = started_system.run_once("AAA", bar)
        assert decision["action"] == "stale_bar"
        assert started_system._bars_processed == before

    def test_redelivered_bar_updates_row_in_place(self, started_system):
        """A re-delivered timestamp replaces the stored row (keep=last)."""
        bar = _bars_after(1, seed=11).iloc[-1].copy()
        started_system.run_once("AAA", bar)
        n = len(started_system._states["AAA"].history)
        bar["close"] = float(bar["close"]) * 1.01   # intraday update
        started_system.run_once("AAA", bar)
        hist = started_system._states["AAA"].history
        assert len(hist) == n
        assert float(hist["close"].iloc[-1]) == pytest.approx(float(bar["close"]))

    def test_stale_training_bar_is_skipped(self, started_system):
        """A bar whose timestamp already sits in the training history is stale."""
        stale = _make_ohlcv(1, seed=42).iloc[-1]   # 2021-01-01 — inside training
        decision = started_system.run_once("AAA", stale)
        assert decision["action"] == "stale_bar"

    def test_history_is_capped(self, started_system, monkeypatch):
        import main as main_mod
        monkeypatch.setattr(main_mod, "_MAX_HISTORY_BARS", 345)
        bars = _bars_after(10, seed=17)
        for i in range(10):
            started_system.run_once("AAA", bars.iloc[i])
        assert len(started_system._states["AAA"].history) <= 345


# ---------------------------------------------------------------------------
# 2c. Pause recovery
# ---------------------------------------------------------------------------

class TestPauseRecovery:

    def test_paused_system_resumes_when_probe_succeeds(self, tmp_path):
        sys_ = _make_system(tmp_path)
        sys_.startup()
        sys_._paused = True
        assert sys_._attempt_resume() is True
        assert sys_._paused is False

    def test_stays_paused_while_probe_fails(self, tmp_path):
        sys_ = _make_system(tmp_path)
        sys_.startup()
        sys_._paused = True
        sys_._client.get_clock = MagicMock(side_effect=RuntimeError("still down"))
        assert sys_._attempt_resume() is False
        assert sys_._paused is True

    def test_resume_probe_is_rate_limited(self, tmp_path):
        sys_ = _make_system(tmp_path)
        sys_.startup()
        sys_._paused = True
        sys_._client.get_clock = MagicMock(side_effect=RuntimeError("down"))
        sys_._attempt_resume()
        sys_._client.get_clock = MagicMock(return_value={"is_open": True})
        # Immediately after a probe the next attempt is throttled.
        assert sys_._attempt_resume() is False
        assert sys_._paused is True


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


# ---------------------------------------------------------------------------
# 2d. Periodic model refit
# ---------------------------------------------------------------------------

class TestModelRefit:

    def test_refit_replaces_models_and_stays_warm(self, started_system):
        state = started_system._states["AAA"]
        old_engine = state.engine
        old_fe = state.feature_engineer
        started_system._refit_models("AAA", state)
        assert state.engine is not old_engine
        assert state.feature_engineer is not old_fe
        # The replay must leave a confirmed regime — otherwise every refit
        # would block trading for the 3-bar stability warm-up.
        assert state.engine.current_regime() != -1

    def test_failed_refit_keeps_previous_models(self, started_system, monkeypatch):
        import main as main_mod
        state = started_system._states["AAA"]
        old_engine = state.engine
        old_fe = state.feature_engineer
        monkeypatch.setattr(
            main_mod, "FeatureEngineer",
            MagicMock(side_effect=RuntimeError("refit boom")),
        )
        started_system._refit_models("AAA", state)
        assert state.engine is old_engine
        assert state.feature_engineer is old_fe


# ---------------------------------------------------------------------------
# 6. run_live exit codes — a supervisor (systemd) must distinguish a
#    transient crash (restart) from a HALT lock (needs a human, NO restart).
# ---------------------------------------------------------------------------

class TestRunLiveExitCodes:

    def test_halt_lock_present_returns_exit_halted_without_startup(
        self, tmp_path, monkeypatch
    ):
        """A present HALT lock must short-circuit to EXIT_HALTED BEFORE any
        broker connection is attempted (so no network, no restart loop)."""
        import main as main_mod
        from settings import config

        lock = tmp_path / "RISK_HALT.lock"
        lock.write_text("{}")
        monkeypatch.setitem(config.RISK, "lock_file_path", str(lock))

        # If startup were reached it would try to build a real broker and hit
        # the network; assert it is never constructed.
        called = {"startup": False}
        monkeypatch.setattr(
            main_mod.TradingSystem, "startup",
            lambda self: called.__setitem__("startup", True) or True,
        )
        monkeypatch.setattr(main_mod, "configure_logging", lambda *a, **k: None)

        assert run_live() == EXIT_HALTED
        assert called["startup"] is False

    def test_startup_failure_without_lock_is_retryable(self, tmp_path, monkeypatch):
        """A plain startup failure (no lock) returns the retryable code so the
        supervisor DOES restart."""
        import main as main_mod
        from settings import config

        monkeypatch.setitem(config.RISK, "lock_file_path",
                            str(tmp_path / "absent.lock"))
        monkeypatch.setattr(main_mod, "configure_logging", lambda *a, **k: None)
        monkeypatch.setattr(main_mod.TradingSystem, "startup", lambda self: False)

        assert run_live() == EXIT_STARTUP_ERR

    def test_halt_lock_helper_reflects_disk(self, tmp_path, monkeypatch):
        from settings import config
        lock = tmp_path / "RISK_HALT.lock"
        monkeypatch.setitem(config.RISK, "lock_file_path", str(lock))
        assert _halt_lock_present() is False
        lock.write_text("{}")
        assert _halt_lock_present() is True


# ---------------------------------------------------------------------------
# 7. run_once_daily — the scheduled (systemd timer) entry point runs exactly
#    ONE poll cycle then exits, with the same halt-aware exit codes.
# ---------------------------------------------------------------------------

class TestRunOnceDaily:

    def test_runs_one_cycle_then_exits_ok(self, tmp_path, monkeypatch):
        import main as main_mod
        from settings import config

        monkeypatch.setitem(config.RISK, "lock_file_path",
                            str(tmp_path / "absent.lock"))
        monkeypatch.setattr(main_mod, "configure_logging", lambda *a, **k: None)

        # Build a fake system that records how run() was invoked.
        captured = {}
        fake = MagicMock()
        fake.startup.return_value = True
        def _run(**kw): captured.update(kw)
        fake.run.side_effect = _run
        monkeypatch.setattr(main_mod, "TradingSystem", lambda *a, **k: fake)

        assert run_once_daily() == EXIT_OK
        fake.startup.assert_called_once()
        # Exactly one poll cycle, no inter-iteration sleep.
        assert captured.get("max_iterations") == 1
        assert captured.get("poll_interval") == 0

    def test_halt_lock_skips_before_startup(self, tmp_path, monkeypatch):
        import main as main_mod
        from settings import config

        lock = tmp_path / "RISK_HALT.lock"
        lock.write_text("{}")
        monkeypatch.setitem(config.RISK, "lock_file_path", str(lock))
        monkeypatch.setattr(main_mod, "configure_logging", lambda *a, **k: None)

        built = {"n": 0}
        monkeypatch.setattr(main_mod, "TradingSystem",
                            lambda *a, **k: built.__setitem__("n", built["n"] + 1))
        assert run_once_daily() == EXIT_HALTED
        assert built["n"] == 0            # never constructed → no network

    def test_startup_failure_is_retryable(self, tmp_path, monkeypatch):
        import main as main_mod
        from settings import config

        monkeypatch.setitem(config.RISK, "lock_file_path",
                            str(tmp_path / "absent.lock"))
        monkeypatch.setattr(main_mod, "configure_logging", lambda *a, **k: None)
        fake = MagicMock()
        fake.startup.return_value = False
        monkeypatch.setattr(main_mod, "TradingSystem", lambda *a, **k: fake)
        assert run_once_daily() == EXIT_STARTUP_ERR


# ---------------------------------------------------------------------------
# 8. HMM warm-up — the scheduled once-a-day model must confirm a regime at
#    startup, or run_once bails at "waiting_for_stable_regime" every run.
# ---------------------------------------------------------------------------

class TestHMMWarmup:

    def test_startup_leaves_a_confirmed_regime(self, tmp_path):
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True
        engine = sys_._states["AAA"].engine
        # -1 would mean the stability filter never confirmed → the bot would
        # never get past the regime gate in run_once.
        assert engine.current_regime() >= 0

    def test_first_run_once_reaches_a_decision(self, started_system):
        """A single fresh bar must produce a real decision, not the
        'waiting_for_stable_regime' early-return (the scheduled-model bug)."""
        bar = _bars_after(1, seed=5).iloc[-1]
        decision = started_system.run_once("AAA", bar)
        assert decision["action"] != "waiting_for_stable_regime"
        assert "regime" in decision   # regime context was attached
