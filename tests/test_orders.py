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
        with pytest.raises(TimeoutError):
            ex.await_fills(["oid-1"], timeout=1)

    def test_await_fills_reports_partial(self):
        client = _make_client()
        client.trading.get_order_by_id.return_value = _FakeOrder(
            "oid-1", status="filled", filled_qty=3, symbol="NVDA"
        )
        ex = OrderExecutor(client, _tracker_with({}))
        result = ex.await_fills(["oid-1"], timeout=2)
        assert result["oid-1"]["filled_qty"] == 3
