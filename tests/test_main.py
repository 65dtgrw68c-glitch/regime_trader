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
from types import SimpleNamespace
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

    def test_startup_aborts_when_one_ticker_has_insufficient_history(self, tmp_path):
        # A single missing ticker must not "trade the rest": the deployed
        # portfolio path would read it as target weight 0 and liquidate any
        # existing position with no signal behind it (K2). Startup must
        # abort instead of silently dropping the ticker.
        good = _make_ohlcv()
        short = good.iloc[-5:]

        class PerTickerFeed:
            def get_training_data(self, ticker, years=2.0):
                return short if ticker == "BBB" else good

        client = FakeClient()
        risk = RiskManager(lock_file_path=str(tmp_path / "RISK_HALT.lock"))
        sys_ = TradingSystem(
            tickers=["AAA", "BBB"], client=client, data_feed=PerTickerFeed(),
            order_executor=MagicMock(), risk_manager=risk,
            alert_manager=MagicMock(),
        )
        # The whole run must abort — the caller never proceeds to trade a
        # partially-initialised book (whichever tickers happened to be
        # processed before the missing one is irrelevant once startup()
        # returns False, since the process exits before any target book
        # gets built).
        assert sys_.startup() is False

    def test_startup_keeps_ticker_when_hmm_training_fails(self, tmp_path, monkeypatch):
        # A training exception must not disqualify the ticker from the book
        # it actually trades on — the deployed path never reads state.engine
        # (K2/M1).
        import main as main_mod

        def _broken_fit(self, feats):
            raise RuntimeError("model is not converging")

        monkeypatch.setattr(main_mod.HMMEngine, "fit", _broken_fit)

        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"))
        assert sys_.startup() is True
        assert "AAA" in sys_._states
        assert "BBB" in sys_._states
        assert sys_._states["AAA"].engine is None
        assert sys_._states["BBB"].engine is None


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


class TestFractionalShares:
    """The live path no longer truncates share counts to whole numbers —
    _position_qty, target sizing, and the order/decision qty must all carry
    fractional precision through untouched."""

    def test_position_qty_preserves_fractional_holding(self, started_system, monkeypatch):
        class LivePosition:
            qty = 12.375

        monkeypatch.setattr(
            started_system._positions, "get_positions",
            lambda: {"AAA": LivePosition()},
        )
        qty = started_system._position_qty("AAA")
        assert qty == pytest.approx(12.375)
        assert not float(qty).is_integer()

    def test_target_positions_from_weights_not_truncated(self, started_system):
        target_positions = started_system._target_positions_from_weights(
            {"AAA": 0.37}, {"AAA": 101.0}, 100_000.0,
        )
        # 0.37 * 100_000 / 101.0 = 366.34... — must not be floored to 366.
        assert target_positions["AAA"] == pytest.approx(366.336633, rel=1e-4)
        assert not float(target_positions["AAA"]).is_integer()


class TestSingleAssetClassCapClipping:
    """Befund 7: run_once decides one ticker at a time, and a class-cap breach
    used to reject the WHOLE candidate book to zero — durably favouring
    whichever same-class ticker was decided first, since the loser's request
    is zeroed even when a smaller position of its own would fit. RiskManager
    .clip_target_weight() replaces that flat reject with "take whatever
    headroom is left"; this drives it end-to-end through run_once."""

    @staticmethod
    def _fixed_evaluate(target_weight):
        """Bypass the HMM/trend machinery: always ask for `target_weight` in
        whichever single ticker this call's current_weights key names."""
        from core.regime_strategies import StrategyParams, StrategySignal, VolTier

        params = StrategyParams(
            allocation_pct=1.0, max_leverage=1.0,
            require_trend_confirmation=False, cash_buffer_pct=0.0,
            allow_shorts=False, rationale="test fixture",
        )

        def _evaluate(self, *, regime_index, regime_label, proba,
                     high_uncertainty, volume_zscore, current_weights,
                     current_vol, trend_confirmed):
            ticker = next(iter(current_weights))
            return StrategySignal(
                regime_index=regime_index, regime_label=regime_label,
                vol_tier=VolTier.MED, confidence=1.0, high_uncertainty=False,
                params=params, effective_alloc=target_weight,
                effective_leverage=1.0,
                target_weights={ticker: target_weight},
                should_rebalance=True, rebalance_reason="test fixture",
            )

        return _evaluate

    def test_second_same_class_ticker_gets_its_fair_share_not_zero(
        self, tmp_path, monkeypatch,
    ):
        """SPY already holds 0.50 of the 0.70 equity class cap (production
        settings/config.py). QQQ — same class — now wants 0.50 too. Before
        clip_target_weight this was `rejected_by_risk` outright; QQQ must now
        receive an order sized to its remaining 0.20 headroom instead."""
        from core.regime_strategies import RegimeOrchestrator

        sys_ = _make_system(tmp_path, tickers=("SPY", "QQQ"), equity=100_000.0)
        assert sys_.startup() is True
        monkeypatch.setattr(
            RegimeOrchestrator, "evaluate", self._fixed_evaluate(0.50),
        )

        spy_price = float(sys_._states["SPY"].history["close"].iloc[-1])
        spy_qty = 0.50 * 100_000.0 / spy_price
        monkeypatch.setattr(
            sys_, "_position_qty",
            lambda t: spy_qty if t == "SPY" else 0.0,
        )

        bar = _bars_after(1, seed=201, start="2022-06-01").iloc[-1]
        decision = sys_.run_once("QQQ", bar)

        assert decision["action"] != "rejected_by_risk", decision
        assert decision["action"] == "order_submitted"
        qty_price = float(bar["close"] if "open" not in bar else bar["open"])
        implied_weight = decision["qty"] * qty_price / 100_000.0
        assert implied_weight == pytest.approx(0.20, abs=0.01)

    def test_ticker_with_no_class_neighbour_is_unaffected(self, tmp_path, monkeypatch):
        """Sanity check: with no competing same-class position, the ticker
        gets its full requested weight — clipping must not shrink it."""
        from core.regime_strategies import RegimeOrchestrator

        sys_ = _make_system(tmp_path, tickers=("SPY",), equity=100_000.0)
        assert sys_.startup() is True
        monkeypatch.setattr(
            RegimeOrchestrator, "evaluate", self._fixed_evaluate(0.50),
        )

        bar = _bars_after(1, seed=202, start="2022-06-01").iloc[-1]
        decision = sys_.run_once("SPY", bar)

        assert decision["action"] == "order_submitted"
        price = float(bar["close"])
        implied_weight = decision["qty"] * price / 100_000.0
        assert implied_weight == pytest.approx(0.50, abs=0.01)


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

    def test_newest_completed_bar_is_decided_on_by_a_fresh_process(self, started_system):
        """
        The deployed bot is a daily `--once` oneshot: startup refetches
        history, so the newest COMPLETED bar — the one it must decide on — is
        already in it. Staleness is therefore per process, not "did the
        history grow": the latter called every single run stale and the bot
        would never trade once it stopped deciding on the unfinished bar.
        """
        state = started_system._states["AAA"]
        newest = state.history.iloc[-1]
        before = started_system._bars_processed

        decision = started_system.run_once("AAA", newest)

        assert decision["action"] != "stale_bar"
        assert started_system._bars_processed == before + 1
        # ...and only once per process, however often the poll re-delivers it.
        assert started_system.run_once("AAA", newest)["action"] == "stale_bar"

    def test_sizing_uses_the_current_price_not_the_decision_close(self, started_system):
        """
        Decisions run on the completed bar, but the order fills now. Sizing
        and the logged expected_price must use the live price, or both are
        wrong by the whole overnight gap.
        """
        bar = _bars_after(1, seed=11).iloc[-1]
        decision_close = float(bar["close"])
        # A plausible overnight gap (well within the K3a sanity band), not
        # the decision close, so the assertion distinguishes the two.
        live_price = decision_close * 1.05
        started_system._data_feed.get_latest_price = lambda ticker: live_price
        assert started_system._execution_price("AAA", decision_close) == live_price

    @pytest.mark.parametrize("answer", [None, 0.0, -5.0, "nope"])
    def test_unusable_live_price_falls_back_to_the_decision_close(
        self, started_system, answer,
    ):
        started_system._data_feed.get_latest_price = lambda ticker: answer
        assert started_system._execution_price("AAA", 42.0) == 42.0

    def test_implausible_live_price_falls_back_and_alerts(self, started_system):
        # A single bad IEX print must not size the order directly (K3a):
        # anything outside the sanity band around the decision close is
        # rejected in favour of the close, with an alert raised.
        started_system._data_feed.get_latest_price = lambda ticker: 4200.0
        assert started_system._execution_price("AAA", 42.0) == 42.0
        started_system._alerts.alert.assert_called_once()
        assert "price_sanity_AAA" == started_system._alerts.alert.call_args.kwargs["key"]

    def test_feed_without_a_price_endpoint_still_works(self, started_system):
        """FakeDataFeed and any older feed simply have no get_latest_price."""
        assert not hasattr(started_system._data_feed, "get_latest_price")
        assert started_system._execution_price("AAA", 42.0) == 42.0

    def test_history_is_capped(self, started_system, monkeypatch):
        import main as main_mod
        monkeypatch.setattr(main_mod, "_MAX_HISTORY_BARS", 345)
        bars = _bars_after(10, seed=17)
        for i in range(10):
            started_system.run_once("AAA", bars.iloc[i])
        assert len(started_system._states["AAA"].history) <= 345



# ---------------------------------------------------------------------------
# 2d. Portfolio batch loop safety
# ---------------------------------------------------------------------------

class TestPortfolioBatchLoop:

    def test_run_portfolio_once_skips_stale_batch_without_rebalance(self, tmp_path):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"))
        assert sys_.startup() is True

        bars = _bars_after(1, seed=101).iloc[-1]
        batch = {"AAA": bars, "BBB": bars}

        # First delivery may update histories and may rebalance.
        sys_.run_portfolio_once(batch)
        sys_._executor.rebalance.reset_mock()
        before = sys_._bars_processed

        # Same timestamps again must be stale and must not rebalance.
        decisions = sys_.run_portfolio_once(batch)

        assert decisions["AAA"]["action"] == "stale_bar"
        assert decisions["BBB"]["action"] == "stale_bar"
        assert sys_._bars_processed == before
        sys_._executor.rebalance.assert_not_called()

    def test_run_portfolio_once_submits_one_shared_rebalance(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        # Keep this test focused on the portfolio batch loop itself:
        # target-book calculation, market gate and book validation are covered elsewhere.
        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.50},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 20},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        sys_._executor.rebalance.return_value = ["oid-aaa", "oid-bbb"]

        bars = _bars_after(1, seed=202).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        # rebalance also receives the per-ticker decision prices (for
        # expected-price/slippage tracking); pin down target_positions only.
        assert sys_._executor.rebalance.call_args.args[0] == {"AAA": 10, "BBB": 20}

        assert decisions["AAA"]["action"] == "portfolio_rebalance_submitted"
        assert decisions["BBB"]["action"] == "portfolio_rebalance_submitted"
        assert decisions["AAA"]["target_position"] == 10
        assert decisions["BBB"]["target_position"] == 20

    def test_run_portfolio_once_skips_rebalance_when_buying_power_insufficient(
        self, tmp_path, monkeypatch,
    ):
        # validate_book() only checks target WEIGHTS against equity; it
        # cannot see broker-side buying power. Without this guard (K3) a
        # buying-power reject would silently drop the order instead of
        # alerting (broker/order_executor.py's submit_order swallows the
        # exception and returns "").
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True, equity=100_000.0)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_, "_compute_live_target_book", lambda: {"AAA": 0.50, "BBB": 0.50},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 20},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        # Real account equity/status stay intact; only buying power is cut
        # far below what 10 AAA + 20 BBB actually costs.
        real_get_account = sys_._client.get_account
        def _tight_buying_power():
            acct = dict(real_get_account())
            acct["buying_power"] = 1.0
            return acct
        monkeypatch.setattr(sys_._client, "get_account", _tight_buying_power)

        sys_._executor.rebalance.reset_mock()

        bars = _bars_after(1, seed=606).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert decisions["AAA"]["action"] == "skipped_insufficient_buying_power"
        assert decisions["BBB"]["action"] == "skipped_insufficient_buying_power"
        sys_._executor.rebalance.assert_not_called()
        sys_._alerts.alert.assert_called()

    def test_run_portfolio_once_rejects_invalid_book_without_rebalance(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.75, "BBB": 0.75},
        )

        rejected = MagicMock()
        rejected.approved = False
        rejected.reason = "gross exposure exceeded"
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: rejected)

        sys_._executor.rebalance.reset_mock()

        bars = _bars_after(1, seed=303).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert decisions["AAA"]["action"] == "rejected_by_risk"
        assert decisions["BBB"]["action"] == "rejected_by_risk"
        assert decisions["AAA"]["reason"] == "gross exposure exceeded"
        assert decisions["BBB"]["reason"] == "gross exposure exceeded"

        sys_._executor.rebalance.assert_not_called()



    def test_run_portfolio_once_halts_without_rebalance(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        from core.risk_manager import CBLevel

        monkeypatch.setattr(
            sys_._risk,
            "update_equity",
            lambda equity, regime_label=None, open_positions=None: CBLevel.HALT,
        )
        monkeypatch.setattr(sys_._risk, "is_halted", lambda: True)

        flatten = MagicMock()
        monkeypatch.setattr(sys_, "_flatten_all", flatten)

        sys_._executor.rebalance.reset_mock()

        bars = _bars_after(1, seed=404).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert decisions["AAA"]["action"] == "halted"
        assert decisions["BBB"]["action"] == "halted"

        flatten.assert_called_once()
        sys_._executor.rebalance.assert_not_called()



    def test_run_portfolio_once_skips_rebalance_when_market_closed(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=False)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.50},
        )

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        monkeypatch.setattr(sys_, "_market_is_open", lambda: False)

        sys_._executor.rebalance.reset_mock()

        bars = _bars_after(1, seed=505).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert decisions["AAA"]["action"] == "skipped_market_closed"
        assert decisions["BBB"]["action"] == "skipped_market_closed"

        sys_._executor.rebalance.assert_not_called()



    def test_run_portfolio_once_handles_rebalance_failure_without_submitted_action(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.50},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 20},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        # Simulate executor failure / safe-call fallback: no order IDs returned.
        sys_._executor.rebalance.return_value = None

        bars = _bars_after(1, seed=606).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        # rebalance also receives the per-ticker decision prices (for
        # expected-price/slippage tracking); pin down target_positions only.
        assert sys_._executor.rebalance.call_args.args[0] == {"AAA": 10, "BBB": 20}

        assert decisions["AAA"]["action"] == "no_change"
        assert decisions["BBB"]["action"] == "no_change"
        assert decisions["AAA"]["target_position"] == 10
        assert decisions["BBB"]["target_position"] == 20



    def test_run_portfolio_once_awaits_rebalance_fills(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.50},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 20},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        sys_._executor.rebalance.return_value = ["oid-aaa", "oid-bbb"]

        bars = _bars_after(1, seed=707).iloc[-1]
        sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        sys_._executor.await_fills.assert_called_once_with(
            ["oid-aaa", "oid-bbb"],
            timeout=90,
        )



    def test_shutdown_preserves_pending_portfolio_rebalance_orders(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.50},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 20},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        sys_._executor.rebalance.return_value = ["oid-aaa", "oid-bbb"]
        sys_._executor.await_fills.side_effect = TimeoutError

        bars = _bars_after(1, seed=808).iloc[-1]
        sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert getattr(sys_, "_preserve_open_orders_on_shutdown") is True

        sys_._executor.cancel_all_open_orders.reset_mock()
        sys_.shutdown(reason="test shutdown")

        sys_._executor.cancel_all_open_orders.assert_not_called()


    def test_run_portfolio_once_records_post_rebalance_drift(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path, tickers=("AAA", "BBB"), is_open=True)
        assert sys_.startup() is True

        monkeypatch.setattr(
            sys_,
            "_compute_live_target_book",
            lambda: {"AAA": 0.50, "BBB": 0.00},
        )
        monkeypatch.setattr(
            sys_,
            "_target_positions_from_weights",
            lambda target_weights, prices, equity: {"AAA": 10, "BBB": 0},
        )
        monkeypatch.setattr(sys_, "_market_is_open", lambda: True)

        approved = MagicMock()
        approved.approved = True
        approved.reason = ""
        monkeypatch.setattr(sys_._risk, "validate_book", lambda target_weights: approved)

        sys_._executor.rebalance.return_value = ["oid-aaa", "oid-bbb"]
        sys_._executor.await_fills.return_value = {
            "oid-aaa": {"status": "filled", "filled_qty": 10},
            "oid-bbb": {"status": "filled", "filled_qty": 0},
        }

        class LivePosition:
            def __init__(self, qty):
                self.qty = qty

        monkeypatch.setattr(sys_._positions, "refresh", MagicMock())
        monkeypatch.setattr(
            sys_._positions,
            "get_positions",
            lambda: {"AAA": LivePosition(10), "BBB": LivePosition(20)},
        )

        bars = _bars_after(1, seed=909).iloc[-1]
        decisions = sys_.run_portfolio_once({"AAA": bars, "BBB": bars})

        assert decisions["AAA"].get("position_drift") is None
        assert decisions["BBB"]["position_drift"]["target"] == pytest.approx(0.0)
        assert decisions["BBB"]["position_drift"]["actual"] == pytest.approx(20.0)
        assert decisions["BBB"]["position_drift"]["diff"] == pytest.approx(20.0)

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

    def test_submit_cancels_stale_open_orders_before_new_order(self, tmp_path):
        """Single-asset live path must not leave an orphaned order behind a
        fresh submission — same protection the portfolio rebalance path has
        via OrderExecutor.rebalance()."""
        sys_ = _make_system(tmp_path)
        manager = MagicMock()
        manager.attach_mock(sys_._executor.cancel_open_orders_for_ticker, "cancel")
        manager.attach_mock(sys_._executor.submit_order, "submit")

        sys_._submit("NVDA", 5, 100.0, "Bull", 0.8)

        sys_._executor.cancel_open_orders_for_ticker.assert_called_once_with("NVDA")
        assert [c[0] for c in manager.mock_calls] == ["cancel", "submit"]


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


# ---------------------------------------------------------------------------
# Crash mid-rebalance, then restart — Phase 4 of the 2026-08-24 audit.
#
# The audit's Go/No-Go list requires a simulated crash mid-rebalance with a
# documented recovery before live capital. The mechanism that has to hold is
# the client_order_id: a process that dies after submitting some of a
# rebalance leaves those orders live at the broker, and the NEXT process must
# not re-submit them as new orders on top.
# ---------------------------------------------------------------------------

class _CoidEnforcingBroker:
    """Fake broker that rejects a duplicate client_order_id like Alpaca does.

    Also models the part that makes this dangerous: an order that was
    accepted before the crash is still live, and fills afterwards.
    """

    def __init__(self):
        self.submitted = []          # every accepted order
        self.rejected_duplicates = []
        self._by_coid = {}
        self._n = 0
        self.trading = self

    # --- trading surface used by OrderExecutor ---
    def get_orders(self):
        return []

    def cancel_order_by_id(self, oid):
        pass

    def get_order_by_id(self, oid):
        return self._by_coid_lookup_by_id(oid)

    def _by_coid_lookup_by_id(self, oid):
        for o in self.submitted:
            if o["id"] == oid:
                return SimpleNamespace(
                    id=oid, status=o["status"], filled_qty=o["filled_qty"],
                    symbol=o["symbol"], filled_avg_price=100.0,
                )
        return SimpleNamespace(id=oid, status="filled", filled_qty=0.0,
                               symbol="", filled_avg_price=100.0)

    def get_order_by_client_id(self, coid):
        o = self._by_coid[coid]
        return SimpleNamespace(
            id=o["id"], status=o["status"], filled_qty=o["filled_qty"],
            symbol=o["symbol"], filled_avg_price=100.0,
        )

    def submit_order(self, request):
        coid = getattr(request, "client_order_id", None)
        if coid is not None and coid in self._by_coid:
            self.rejected_duplicates.append(coid)
            raise RuntimeError(
                '{"code":40010001,"message":"client_order_id must be unique"}'
            )
        self._n += 1
        order = {
            "id": f"oid-{self._n}",
            "symbol": request.symbol,
            "qty": float(request.qty),
            "coid": coid,
            "status": "filled",
            "filled_qty": float(request.qty),
        }
        self.submitted.append(order)
        if coid is not None:
            self._by_coid[coid] = order
        return SimpleNamespace(id=order["id"], status="accepted",
                               symbol=request.symbol)


class TestCrashDuringRebalanceRecovery:

    @staticmethod
    def _executor(broker, tracker):
        from broker.order_executor import OrderExecutor
        return OrderExecutor(broker, tracker)

    @staticmethod
    def _tracker(positions):
        from broker.position_tracker import Position, PositionTracker
        t = PositionTracker(client=None)
        t.set_positions({
            tk: Position(ticker=tk, qty=q, avg_entry_price=100.0, current_price=100.0)
            for tk, q in positions.items()
        })
        return t

    def test_restart_after_partial_rebalance_does_not_double_the_position(self):
        """Process A submits AAA then dies. Process B restarts the same day
        against a position snapshot that has NOT yet caught up. The COID must
        stop the second submission from doubling AAA."""
        broker = _CoidEnforcingBroker()
        run_tag = "2026-08-25"

        # --- process A: submits AAA, then "crashes" before BBB ---
        tracker_a = self._tracker({})
        ex_a = self._executor(broker, tracker_a)
        ex_a.rebalance({"AAA": 25.0}, prices={"AAA": 100.0}, run_tag=run_tag)

        assert len(broker.submitted) == 1
        assert broker.submitted[0]["symbol"] == "AAA"
        assert broker.submitted[0]["qty"] == pytest.approx(25.0)

        # --- process B: restarts, still sees the stale (empty) snapshot ---
        # This is the dangerous case: it recomputes the SAME decision and
        # would submit AAA a second time without idempotency protection.
        tracker_b = self._tracker({})
        ex_b = self._executor(broker, tracker_b)
        ex_b.rebalance({"AAA": 25.0}, prices={"AAA": 100.0}, run_tag=run_tag)

        aaa_orders = [o for o in broker.submitted if o["symbol"] == "AAA"]
        assert len(aaa_orders) == 1, "AAA was submitted twice — position doubled"
        assert broker.rejected_duplicates, "the duplicate COID was never exercised"
        assert sum(o["qty"] for o in aaa_orders) == pytest.approx(25.0)

    def test_restart_completes_the_unfinished_leg(self):
        """The flip side: recovery must still finish what process A did not.
        BBB was never submitted, so process B must submit it."""
        broker = _CoidEnforcingBroker()
        run_tag = "2026-08-25"

        tracker_a = self._tracker({})
        self._executor(broker, tracker_a).rebalance(
            {"AAA": 25.0}, prices={"AAA": 100.0}, run_tag=run_tag,
        )

        # Process B sees AAA filled (refresh picked it up) and the full book.
        tracker_b = self._tracker({"AAA": 25.0})
        self._executor(broker, tracker_b).rebalance(
            {"AAA": 25.0, "BBB": 40.0},
            prices={"AAA": 100.0, "BBB": 50.0}, run_tag=run_tag,
        )

        symbols = [o["symbol"] for o in broker.submitted]
        assert symbols.count("AAA") == 1, "AAA re-ordered after it already filled"
        assert symbols.count("BBB") == 1, "BBB never recovered"
        bbb = next(o for o in broker.submitted if o["symbol"] == "BBB")
        assert bbb["qty"] == pytest.approx(40.0)

    def test_partial_fill_remainder_is_reorderable_the_same_day(self):
        """H2b: the COID carries the TARGET quantity, so 'the rest of this
        decision' is a different key than 'this decision again' and is not
        rejected as a duplicate."""
        broker = _CoidEnforcingBroker()
        run_tag = "2026-08-25"

        # First attempt targets 25 and (say) only 10 end up held.
        self._executor(broker, self._tracker({})).rebalance(
            {"AAA": 25.0}, prices={"AAA": 100.0}, run_tag=run_tag,
        )
        first_coid = broker.submitted[0]["coid"]

        # Same day, same target, but 10 are now held: delta is 15, and the
        # COID still encodes target 25 — so it collides and is refused.
        self._executor(broker, self._tracker({"AAA": 10.0})).rebalance(
            {"AAA": 25.0}, prices={"AAA": 100.0}, run_tag=run_tag,
        )
        assert first_coid in broker.rejected_duplicates

        # A genuinely NEW decision (different target) gets a new key and goes
        # through — the book is never stuck unable to trade for the rest of
        # the day.
        self._executor(broker, self._tracker({"AAA": 10.0})).rebalance(
            {"AAA": 30.0}, prices={"AAA": 100.0}, run_tag=run_tag,
        )
        coids = [o["coid"] for o in broker.submitted]
        assert len(set(coids)) == len(coids), "COIDs collided across decisions"
        assert any(c.endswith("30.0000") for c in coids)

    def test_duplicate_reject_returns_the_live_order_id_not_empty(self):
        """A crash between submit and response leaves a live order. On retry
        the broker refuses the duplicate; the executor must hand back the
        EXISTING order id so the caller can await it, rather than "" (which
        would leave a live order untracked)."""
        broker = _CoidEnforcingBroker()
        tracker = self._tracker({})
        ex = self._executor(broker, tracker)

        first = ex.submit_order("AAA", 25.0, "buy",
                                client_order_id="rt-AAA-2026-08-25-buy-25.0000")
        assert first == "oid-1"

        recovered = ex.submit_order("AAA", 25.0, "buy",
                                    client_order_id="rt-AAA-2026-08-25-buy-25.0000")
        assert recovered == "oid-1", "live order was dropped instead of recovered"
        assert len(broker.submitted) == 1


# ---------------------------------------------------------------------------
# Book vol target in the LIVE path (owner decision 2026-08-31, finding K1)
# ---------------------------------------------------------------------------

class TestLiveBookVolTarget:
    """settings.config.BOOK_VOL_TARGET must actually reach the submitted book.

    Before 2026-08-31 the vol target existed only inside
    core/portfolio_backtester.py, explicitly documented as "an evaluation
    knob, not a deployed one". Adopting it means the live loop applies it too
    — these tests are what make that true rather than intended.
    """

    def _feed_returns(self, sys_, returns):
        equity = 100_000.0
        sys_._risk.end_of_day(equity)
        for r in returns:
            equity *= 1.0 + r
            sys_._risk.end_of_day(equity)

    def test_quiet_book_is_not_scaled(self, tmp_path):
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True
        self._feed_returns(sys_, [0.0002, -0.0002] * 20)

        assert sys_._risk.book_vol_scale() == 1.0

    def test_volatile_book_is_scaled_down_before_submission(self, tmp_path, monkeypatch):
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True

        from core import sleeves

        monkeypatch.setattr(
            sleeves, "compose_book", lambda core, trends: {"AAA": 0.80},
        )
        self._feed_returns(sys_, [0.03, -0.03] * 20)   # ~48% annualised

        scale = sys_._risk.book_vol_scale()
        assert 0.0 < scale < 1.0

        book = sys_._compute_live_target_book()
        assert book["AAA"] == pytest.approx(0.80 * scale)

    def test_cold_start_ships_the_unscaled_book(self, tmp_path, monkeypatch):
        """Matches the backtester's own cold start: no window, no scaling."""
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True

        from core import sleeves

        monkeypatch.setattr(
            sleeves, "compose_book", lambda core, trends: {"AAA": 0.80},
        )
        assert sys_._risk.book_vol_scale() == 1.0
        assert sys_._compute_live_target_book()["AAA"] == pytest.approx(0.80)

    def test_scaling_lowers_gross_exposure_the_risk_layer_sees(self, tmp_path, monkeypatch):
        """The scale is applied BEFORE validate_book, so risk validates the
        book that is actually submitted — not a larger one."""
        sys_ = _make_system(tmp_path)
        assert sys_.startup() is True

        from core import sleeves

        monkeypatch.setattr(
            sleeves, "compose_book", lambda core, trends: {"AAA": 0.60, "BBB": 0.40},
        )
        self._feed_returns(sys_, [0.03, -0.03] * 20)

        book = sys_._compute_live_target_book()
        gross = sum(abs(w) for w in book.values())
        assert gross < 1.0 - 1e-9
