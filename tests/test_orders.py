"""
Tests for broker/order_executor.py and broker/position_tracker.py.

All broker interaction is mocked — no real Alpaca API calls.

Run with:  pytest tests/test_orders.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker.order_executor import OrderExecutor
from broker.position_tracker import Position, PositionTracker


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _FakeOrder:
    def __init__(self, oid, status="accepted", filled_qty=0, symbol="X"):
        self.id = oid
        self.status = status
        self.filled_qty = filled_qty
        self.symbol = symbol
        self.filled_avg_price = 0.0


def _make_client():
    """A mock AlpacaClient whose `.trading` records submitted orders."""
    client = MagicMock()
    counter = {"n": 0}

    def _submit(_request):
        counter["n"] += 1
        return _FakeOrder(oid=f"oid-{counter['n']}", status="accepted")

    client.trading.submit_order.side_effect = _submit
    return client


def _tracker_with(positions: dict[str, int]) -> PositionTracker:
    t = PositionTracker(client=None)
    t.set_positions({
        tk: Position(ticker=tk, qty=q, avg_entry_price=100.0, current_price=100.0)
        for tk, q in positions.items()
    })
    return t


# Patch the lazy alpaca-py imports inside submit_order so no SDK is needed.
@pytest.fixture(autouse=True)
def _stub_alpaca(monkeypatch):
    import types
    fake = types.ModuleType("alpaca")
    trading = types.ModuleType("alpaca.trading")
    requests = types.ModuleType("alpaca.trading.requests")
    enums = types.ModuleType("alpaca.trading.enums")

    class _Req:
        def __init__(self, **kw): self.__dict__.update(kw)
    requests.MarketOrderRequest = _Req
    requests.LimitOrderRequest = _Req
    requests.StopOrderRequest = _Req
    requests.ReplaceOrderRequest = _Req

    class OrderSide:
        BUY = "buy"; SELL = "sell"
    class TimeInForce:
        DAY = "day"
    enums.OrderSide = OrderSide
    enums.TimeInForce = TimeInForce

    monkeypatch.setitem(sys.modules, "alpaca", fake)
    monkeypatch.setitem(sys.modules, "alpaca.trading", trading)
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests)
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums)
    yield


# ---------------------------------------------------------------------------
# PositionTracker.diff
# ---------------------------------------------------------------------------

class TestPositionTrackerDiff:

    def test_diff_buy_and_sell(self):
        tracker = _tracker_with({"A": 10})
        deltas = tracker.diff({"A": 5, "B": 8})
        assert deltas == {"A": -5, "B": 8}

    def test_diff_no_change(self):
        tracker = _tracker_with({"A": 10, "B": 5})
        assert tracker.diff({"A": 10, "B": 5}) == {}

    def test_diff_closes_removed_ticker(self):
        tracker = _tracker_with({"A": 10})
        assert tracker.diff({}) == {"A": -10}

    def test_diff_opens_new_ticker(self):
        tracker = _tracker_with({})
        assert tracker.diff({"C": 7}) == {"C": 7}


# ---------------------------------------------------------------------------
# PositionTracker external-close detection
# ---------------------------------------------------------------------------

class TestExternalClose:

    def test_detect_closed_when_ticker_disappears(self):
        tracker = _tracker_with({"A": 10, "B": 5})
        new = {"A": Position("A", 10, 100.0, 100.0)}   # B gone
        assert tracker.detect_closed_positions(new) == ["B"]

    def test_detect_closed_when_qty_zero(self):
        tracker = _tracker_with({"A": 10})
        new = {"A": Position("A", 0, 100.0, 100.0)}
        assert tracker.detect_closed_positions(new) == ["A"]

    def test_no_false_positive(self):
        tracker = _tracker_with({"A": 10})
        new = {"A": Position("A", 10, 100.0, 100.0)}
        assert tracker.detect_closed_positions(new) == []


# ---------------------------------------------------------------------------
# PositionTracker exposure / portfolio value
# ---------------------------------------------------------------------------

class TestExposure:

    def test_portfolio_value(self):
        tracker = _tracker_with({"A": 10})   # 10 * 100 = 1000
        tracker.set_cash(500)
        assert tracker.get_portfolio_value() == pytest.approx(1500)

    def test_exposure_gross_and_net(self):
        t = PositionTracker(client=None)
        t.set_positions({
            "A": Position("A", 10, 100.0, 100.0),    # +1000
            "B": Position("B", -4, 100.0, 100.0),    # -400
        })
        exp = t.get_exposure()
        assert exp["gross"] == pytest.approx(1400)
        assert exp["net"] == pytest.approx(600)


# ---------------------------------------------------------------------------
# OrderExecutor.rebalance
# ---------------------------------------------------------------------------

class TestRebalance:

    def test_rebalance_submits_correct_deltas(self):
        client = _make_client()
        tracker = _tracker_with({"A": 10})
        ex = OrderExecutor(client, tracker)
        ids = ex.rebalance({"A": 5, "B": 8})
        # Two orders: sell 5 A, buy 8 B
        assert len(ids) == 2
        assert client.trading.submit_order.call_count == 2

    def test_no_order_when_no_delta(self):
        client = _make_client()
        tracker = _tracker_with({"A": 10})
        ex = OrderExecutor(client, tracker)
        ids = ex.rebalance({"A": 10})
        assert ids == []
        assert client.trading.submit_order.call_count == 0

    def test_cancel_removed_ticker_generates_sell(self):
        client = _make_client()
        tracker = _tracker_with({"A": 10})
        ex = OrderExecutor(client, tracker)
        ids = ex.rebalance({})            # close A entirely
        assert len(ids) == 1
        assert client.trading.submit_order.call_count == 1

    def test_rebalance_records_expected_price_per_ticker(self):
        client = _make_client()
        tracker = _tracker_with({"A": 10})
        ex = OrderExecutor(client, tracker)
        ids = ex.rebalance({"A": 5, "B": 8}, prices={"A": 100.0, "B": 50.0})
        assert len(ids) == 2

        # PositionTracker.diff() iterates a set, so submission order between
        # A/B isn't guaranteed — match each order_id back to its ticker via
        # the actual submitted request instead of assuming a fixed order.
        submitted_symbols = [
            call.args[0].symbol for call in client.trading.submit_order.call_args_list
        ]
        expected_price_by_ticker = {"A": 100.0, "B": 50.0}
        for oid, symbol in zip(ids, submitted_symbols):
            assert ex._expected_prices[oid] == expected_price_by_ticker[symbol]


# ---------------------------------------------------------------------------
# OrderExecutor single-order operations
# ---------------------------------------------------------------------------

class TestOrderOps:

    def test_submit_zero_qty_returns_empty(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        assert ex.submit_order("A", 0, "buy") == ""
        assert client.trading.submit_order.call_count == 0

    def test_submit_market_order_returns_id(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        oid = ex.submit_order("NVDA", 1, "buy", order_type="market")
        assert oid.startswith("oid-")

    def test_rejected_order_returns_empty(self):
        client = _make_client()
        client.trading.submit_order.side_effect = lambda req: _FakeOrder(
            "oid-x", status="rejected"
        )
        ex = OrderExecutor(client, _tracker_with({}))
        assert ex.submit_order("NVDA", 1, "buy") == ""

    def test_submit_exception_returns_empty(self):
        client = _make_client()
        client.trading.submit_order.side_effect = RuntimeError("api down")
        ex = OrderExecutor(client, _tracker_with({}))
        assert ex.submit_order("NVDA", 1, "buy") == ""

    def test_stop_loss_uses_stop_order(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        oid = ex.submit_stop_loss("NVDA", 1, stop_price=90.0)
        assert oid.startswith("oid-")
        assert client.trading.submit_order.call_count == 1

    def test_stop_order_rejects_fractional_qty(self):
        """Alpaca only supports fractional qty on market/limit DAY orders —
        a fractional stop must fail fast instead of round-tripping to the broker."""
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        oid = ex.submit_stop_loss("NVDA", 1.5, stop_price=90.0)
        assert oid == ""
        assert client.trading.submit_order.call_count == 0

    def test_stop_order_accepts_whole_qty(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        oid = ex.submit_stop_loss("NVDA", 2.0, stop_price=90.0)
        assert oid.startswith("oid-")
        assert client.trading.submit_order.call_count == 1

    def test_cancel_order_calls_client(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        ex.cancel_order("oid-1")
        client.trading.cancel_order_by_id.assert_called_once_with("oid-1")

    def test_cancel_all_calls_client(self):
        client = _make_client()
        ex = OrderExecutor(client, _tracker_with({}))
        ex.cancel_all_open_orders()
        client.trading.cancel_orders.assert_called_once()


# ---------------------------------------------------------------------------
# OrderExecutor.await_fills
# ---------------------------------------------------------------------------

class TestAwaitFills:

    def test_await_fills_success(self):
        client = _make_client()
        client.trading.get_order_by_id.return_value = _FakeOrder(
            "oid-1", status="filled", filled_qty=5, symbol="NVDA"
        )
        ex = OrderExecutor(client, _tracker_with({}))
        result = ex.await_fills(["oid-1"], timeout=2)
        assert result["oid-1"]["status"] == "filled"

    def test_await_fills_timeout(self):
        client = _make_client()
        client.trading.get_order_by_id.return_value = _FakeOrder(
            "oid-1", status="accepted", filled_qty=0
        )
        ex = OrderExecutor(client, _tracker_with({}))

        result = ex.await_fills(["oid-1"], timeout=0)

        assert result["oid-1"]["status"] == "canceled_on_timeout"
        assert result["oid-1"]["filled_qty"] == 0.0
        client.trading.cancel_order_by_id.assert_called_once_with("oid-1")

    def test_await_fills_reports_partial(self):
        client = _make_client()
        client.trading.get_order_by_id.return_value = _FakeOrder(
            "oid-1", status="filled", filled_qty=3, symbol="NVDA"
        )
        ex = OrderExecutor(client, _tracker_with({}))
        result = ex.await_fills(["oid-1"], timeout=2)
        assert result["oid-1"]["filled_qty"] == 3

    def test_await_fills_logs_expected_price_for_slippage(self):
        """submit_order's expected_price must reach TradeLogger.log_fill so
        realised slippage can actually be measured from trades.csv."""
        client = _make_client()
        client.trading.get_order_by_id.return_value = _FakeOrder(
            "oid-1", status="filled", filled_qty=5, symbol="NVDA"
        )
        trade_logger = MagicMock()
        ex = OrderExecutor(client, _tracker_with({}), trade_logger)

        oid = ex.submit_order("NVDA", 5, "buy", expected_price=101.25)
        ex.await_fills([oid], timeout=2)

        trade_logger.log_fill.assert_called_once()
        logged = trade_logger.log_fill.call_args.args[0]
        assert logged["expected_price"] == 101.25
        assert oid not in ex._expected_prices  # popped after use


# ---------------------------------------------------------------------------
# OrderExecutor.submit_order — idempotency key
# ---------------------------------------------------------------------------

class TestClientOrderId:

    def test_client_order_id_forwarded_to_request(self):
        client = MagicMock()
        client.trading.submit_order.return_value = _FakeOrder("oid-1")
        ex = OrderExecutor(client, _tracker_with({}))
        ex.submit_order("SPY", 5, "buy", client_order_id="rt-SPY-2026-07-10-buy")
        request = client.trading.submit_order.call_args.args[0]
        assert request.client_order_id == "rt-SPY-2026-07-10-buy"

    def test_no_client_order_id_by_default(self):
        client = MagicMock()
        client.trading.submit_order.return_value = _FakeOrder("oid-1")
        ex = OrderExecutor(client, _tracker_with({}))
        ex.submit_order("SPY", 5, "buy")
        request = client.trading.submit_order.call_args.args[0]
        assert not hasattr(request, "client_order_id")


def test_await_fills_treats_already_filled_cancel_error_as_filled():
    from broker.order_executor import OrderExecutor

    class FakeOrder:
        def __init__(self, status="new", filled_qty=0):
            self.status = status
            self.filled_qty = filled_qty
            self.symbol = "SPY"
            self.filled_avg_price = 100.0

    class FakeTrading:
        def __init__(self):
            self.cancel_calls = 0

        def get_order_by_id(self, oid):
            return FakeOrder(status="new", filled_qty=0)

        def cancel_order_by_id(self, oid):
            self.cancel_calls += 1
            raise RuntimeError(
                '{"code":42210000,"message":"order is already in \\"filled\\" state"}'
            )

    class FakeClient:
        def __init__(self):
            self.trading = FakeTrading()

    class FakePositions:
        def diff(self, target):
            return {}

    client = FakeClient()
    executor = OrderExecutor(client=client, position_tracker=FakePositions())

    result = executor.await_fills(["oid-1"], timeout=0)

    assert result["oid-1"]["status"] == "filled"
    assert client.trading.cancel_calls == 1


def test_await_fills_logs_fill_found_during_timeout_recheck():
    """Regression: an order reported 'filled' on the recheck GET itself
    (before any cancel attempt) used to just `continue` without ever
    calling log_fill — a real fill silently missing from the trade log."""
    class FakeOrder:
        def __init__(self, status, filled_qty=0, filled_avg_price=0.0):
            self.status = status
            self.filled_qty = filled_qty
            self.symbol = "QQQ"
            self.filled_avg_price = filled_avg_price

    class FakeTrading:
        def get_order_by_id(self, oid):
            return FakeOrder(status="filled", filled_qty=3, filled_avg_price=55.0)

        def cancel_order_by_id(self, oid):
            raise AssertionError("should not cancel an already-terminal order")

    class FakeClient:
        def __init__(self):
            self.trading = FakeTrading()

    class FakePositions:
        def diff(self, target):
            return {}

    client = FakeClient()
    trade_logger = MagicMock()
    executor = OrderExecutor(client=client, position_tracker=FakePositions(), trade_logger=trade_logger)
    executor._expected_prices["oid-2"] = 54.5

    result = executor.await_fills(["oid-2"], timeout=0)

    assert result["oid-2"]["status"] == "filled"
    trade_logger.log_fill.assert_called_once()
    logged = trade_logger.log_fill.call_args.args[0]
    assert logged["ticker"] == "QQQ"
    assert logged["qty"] == 3
    assert logged["fill_price"] == 55.0
    assert logged["expected_price"] == 54.5


def test_await_fills_logs_fill_found_after_already_filled_cancel_error():
    """Regression: an order whose fill only surfaces via the 'already
    filled' cancel-rejection used to be dropped from the trade log
    entirely — this is exactly what happened to two real orders on
    2026-08-12 (SPY sell + QLD buy), leaving trades.csv with zero rows
    despite both trades actually filling."""
    class FakeOrder:
        def __init__(self, status, filled_qty=0, filled_avg_price=0.0):
            self.status = status
            self.filled_qty = filled_qty
            self.symbol = "SPY"
            self.filled_avg_price = filled_avg_price

    class FakeTrading:
        def __init__(self):
            self.calls = 0

        def get_order_by_id(self, oid):
            self.calls += 1
            # Recheck-before-cancel still sees it pending; by the time the
            # follow-up re-fetch (inside the fix) runs, it has filled.
            if self.calls == 1:
                return FakeOrder(status="new", filled_qty=0)
            return FakeOrder(status="filled", filled_qty=5, filled_avg_price=101.5)

        def cancel_order_by_id(self, oid):
            raise RuntimeError(
                '{"code":42210000,"message":"order is already in \\"filled\\" state"}'
            )

    class FakeClient:
        def __init__(self):
            self.trading = FakeTrading()

    class FakePositions:
        def diff(self, target):
            return {}

    client = FakeClient()
    trade_logger = MagicMock()
    executor = OrderExecutor(client=client, position_tracker=FakePositions(), trade_logger=trade_logger)
    executor._expected_prices["oid-1"] = 100.0

    result = executor.await_fills(["oid-1"], timeout=0)

    assert result["oid-1"]["status"] == "filled"
    assert result["oid-1"]["filled_qty"] == 5
    trade_logger.log_fill.assert_called_once()
    logged = trade_logger.log_fill.call_args.args[0]
    assert logged["qty"] == 5
    assert logged["fill_price"] == 101.5
    assert logged["expected_price"] == 100.0
