import pytest

from broker.position_tracker import Position, PositionTracker
from broker.order_executor import OrderExecutor


class FakeOrder:
    def __init__(self, oid, symbol="SPY", status="new", filled_qty=0):
        self.id = oid
        self.symbol = symbol
        self.status = status
        self.filled_qty = filled_qty


class FakeTrading:
    def __init__(self):
        self.cancelled = []
        self.submitted = []
        self.open_orders = [FakeOrder("stop-1", symbol="SPY")]
        self.orders_by_id = {
            "pending-1": FakeOrder("pending-1", symbol="SPY", status="new", filled_qty=0)
        }

    def get_orders(self):
        return self.open_orders

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def get_order_by_id(self, oid):
        return self.orders_by_id[oid]

    def submit_order(self, request):
        order = FakeOrder("submitted-1", symbol=getattr(request, "symbol", "SPY"), status="accepted")
        self.submitted.append(order)
        return order


class FakeClient:
    def __init__(self):
        self.trading = FakeTrading()


def test_position_tracker_supports_fractional_deltas():
    tracker = PositionTracker()
    tracker.set_positions({
        "SPY": Position(
            ticker="SPY",
            qty=10.5,
            avg_entry_price=400.0,
            current_price=410.0,
        )
    })

    deltas = tracker.diff({"SPY": 5.2})

    assert deltas["SPY"] == pytest.approx(-5.3)


def test_rebalance_cancels_existing_open_orders_before_new_order(monkeypatch):
    client = FakeClient()
    tracker = PositionTracker()
    tracker.set_positions({
        "SPY": Position(
            ticker="SPY",
            qty=10.0,
            avg_entry_price=400.0,
            current_price=410.0,
        )
    })

    executor = OrderExecutor(client, tracker)

    submitted = []

    def fake_submit_order(ticker, qty, side, order_type="market", **kwargs):
        submitted.append((ticker, qty, side, order_type))
        return "new-order-1"

    monkeypatch.setattr(executor, "submit_order", fake_submit_order)

    order_ids = executor.rebalance({"SPY": 0.0})

    assert "stop-1" in client.trading.cancelled
    assert submitted == [("SPY", 10.0, "sell", "market")]
    assert order_ids == ["new-order-1"]


def test_await_fills_cancels_pending_orders_on_timeout():
    client = FakeClient()
    tracker = PositionTracker()
    executor = OrderExecutor(client, tracker)

    result = executor.await_fills(["pending-1"], timeout=0)

    assert result["pending-1"]["status"] == "canceled_on_timeout"
    assert "pending-1" in client.trading.cancelled
