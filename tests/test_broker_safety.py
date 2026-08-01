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


class _FakePosition:
    def __init__(self, symbol="SPY", qty=1.0):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = 100.0
        self.current_price = 100.0
        self.unrealized_pl = 0.0


class _FlakyPositionsClient:
    """get_all_positions fails `fail_times` times, then succeeds."""

    def __init__(self, fail_times):
        self._fail_times = fail_times
        self.calls = 0
        self.trading = self

    def get_all_positions(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ConnectionError("transient broker error")
        return [_FakePosition("SPY", 3.0)]


def test_refresh_retries_transient_failures_then_succeeds(monkeypatch):
    client = _FlakyPositionsClient(fail_times=2)
    tracker = PositionTracker(client=client)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    tracker.refresh()

    assert client.calls == 3
    assert tracker.get_positions()["SPY"].qty == 3.0


def test_refresh_raises_after_exhausting_retries(monkeypatch):
    client = _FlakyPositionsClient(fail_times=5)
    tracker = PositionTracker(client=client)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    with pytest.raises(ConnectionError):
        tracker.refresh()

    assert client.calls == 3  # default max_retries


class _FlakyCancelAllClient:
    def __init__(self, fail_times):
        self._fail_times = fail_times
        self.calls = 0
        self.trading = self

    def cancel_orders(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ConnectionError("transient broker error")


def test_cancel_all_open_orders_retries_transient_failures(monkeypatch):
    client = _FlakyCancelAllClient(fail_times=2)
    tracker = PositionTracker()
    executor = OrderExecutor(client, tracker)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    executor.cancel_all_open_orders()

    assert client.calls == 3
