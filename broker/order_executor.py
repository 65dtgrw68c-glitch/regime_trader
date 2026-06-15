"""
Order Executor — placing, modifying, and cancelling orders via Alpaca.

Capabilities (Prompt 6)
-----------------------
* Market orders (buy / sell)
* Limit orders with a specified price
* Stop-loss orders
* Modify the stop price on an existing stop order (replace)
* Cancel individual orders / cancel all open orders
* Handle order rejection and partial fills

Takes target share counts (already risk-approved by RiskManager), diffs them
against current positions via PositionTracker, and submits the resulting
orders.  The alpaca-py enums/requests are imported lazily so the module is
import-safe and unit-testable with a mock client.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# Order statuses that mean "no further fills are coming".
_TERMINAL_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "closed"}


class OrderExecutor:
    """
    Translates target positions into broker orders and manages their lifecycle.

    Parameters
    ----------
    client          : AlpacaClient (or mock exposing `.trading`)
    position_tracker: PositionTracker used to compute deltas
    trade_logger    : optional TradeLogger for CSV audit trail
    """

    def __init__(
        self,
        client: object,
        position_tracker: object,
        trade_logger: Optional[object] = None,
    ) -> None:
        self._client = client
        self._positions = position_tracker
        self._trade_logger = trade_logger
        self._submitted_ids: list[str] = []

    # ------------------------------------------------------------------
    # Rebalancing
    # ------------------------------------------------------------------

    def rebalance(self, target_positions: dict[str, int]) -> list[str]:
        """
        Compute deltas vs. current positions and submit market orders for
        each.  Returns the list of submitted order IDs.
        """
        deltas = self._positions.diff(target_positions)
        order_ids: list[str] = []
        for ticker, delta in deltas.items():
            side = "buy" if delta > 0 else "sell"
            oid = self.submit_order(ticker, abs(delta), side, order_type="market")
            if oid:
                order_ids.append(oid)
        self._submitted_ids.extend(order_ids)
        return order_ids

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_order(
        self,
        ticker: str,
        qty: int,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> str:
        """
        Submit a market, limit, or stop order and return its broker order ID.
        Returns "" on zero qty or a rejected submission.
        """
        if qty <= 0:
            return ""

        from alpaca.trading.requests import (
            MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY

        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit order requires limit_price")
            request = LimitOrderRequest(
                symbol=ticker, qty=qty, side=order_side,
                time_in_force=tif, limit_price=limit_price,
            )
        elif order_type == "stop":
            if stop_price is None:
                raise ValueError("stop order requires stop_price")
            request = StopOrderRequest(
                symbol=ticker, qty=qty, side=order_side,
                time_in_force=tif, stop_price=stop_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=ticker, qty=qty, side=order_side, time_in_force=tif,
            )

        try:
            order = self._client.trading.submit_order(request)
        except Exception as exc:
            logger.error("Order REJECTED for %s %s x%d: %s", side, ticker, qty, exc)
            return ""

        status = str(getattr(order, "status", "")).lower()
        oid = str(getattr(order, "id", ""))
        if status == "rejected":
            logger.error("Order rejected by broker: %s %s x%d", side, ticker, qty)
            return ""

        logger.info("Submitted %s %s %s x%d (id=%s, status=%s)",
                    order_type, side, ticker, qty, oid, status)
        if self._trade_logger:
            self._trade_logger.log_order({
                "ticker": ticker, "side": side, "qty": qty,
                "price": limit_price or stop_price, "order_id": oid,
            })
        return oid

    def submit_stop_loss(self, ticker: str, qty: int, stop_price: float,
                         side: str = "sell") -> str:
        """Convenience wrapper to attach a protective stop to a position."""
        return self.submit_order(ticker, qty, side, order_type="stop",
                                 stop_price=stop_price)

    # ------------------------------------------------------------------
    # Modify existing stop
    # ------------------------------------------------------------------

    def modify_stop(self, order_id: str, new_stop_price: float) -> str:
        """
        Replace an existing stop order's stop price (e.g. trailing the stop
        up as price rises).  Returns the new/updated order ID.
        """
        from alpaca.trading.requests import ReplaceOrderRequest

        try:
            updated = self._client.trading.replace_order_by_id(
                order_id, ReplaceOrderRequest(stop_price=new_stop_price)
            )
        except Exception as exc:
            logger.error("Failed to modify stop on %s: %s", order_id, exc)
            return ""
        new_id = str(getattr(updated, "id", order_id))
        logger.info("Modified stop on %s → %.4f (id=%s)", order_id, new_stop_price, new_id)
        return new_id

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> None:
        self._client.trading.cancel_order_by_id(order_id)
        logger.info("Cancelled order %s", order_id)

    def cancel_all_open_orders(self) -> None:
        self._client.trading.cancel_orders()
        logger.info("Cancelled all open orders.")

    # ------------------------------------------------------------------
    # Fill confirmation / partial-fill handling
    # ------------------------------------------------------------------

    def await_fills(self, order_ids: list[str], timeout: int = 30) -> dict:
        """
        Poll until every order reaches a terminal status or `timeout`
        seconds elapse.  Returns a dict of order_id -> {status, filled_qty}.
        Raises TimeoutError if any order is still working at the deadline.

        Partial fills are surfaced via `filled_qty` so the caller can decide
        whether to chase the remainder.
        """
        deadline = time.time() + timeout
        results: dict[str, dict] = {}
        pending = set(order_ids)
        while pending and time.time() < deadline:
            for oid in list(pending):
                order = self._client.trading.get_order_by_id(oid)
                status = str(getattr(order, "status", "")).lower()
                filled_qty = float(getattr(order, "filled_qty", 0) or 0)
                results[oid] = {"status": status, "filled_qty": filled_qty}
                if status in _TERMINAL_STATUSES:
                    pending.discard(oid)
                    if status == "filled" and self._trade_logger:
                        self._trade_logger.log_fill({
                            "ticker": str(getattr(order, "symbol", "")),
                            "qty": filled_qty,
                            "fill_price": float(getattr(order, "filled_avg_price", 0) or 0),
                            "order_id": oid,
                        })
            if pending:
                time.sleep(1)

        if pending:
            raise TimeoutError(
                f"{len(pending)} order(s) not terminal within {timeout}s: {pending}"
            )
        return results
