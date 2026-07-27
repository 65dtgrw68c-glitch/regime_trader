from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "closed"}


class OrderExecutor:
    """
    Translates target positions into broker orders and manages their lifecycle.
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

    def cancel_open_orders_for_ticker(self, ticker: str) -> None:
        """Storniert alle offenen Orders für ein Symbol vor dem Rebalancing (Schutz vor verwaisten Stops)."""
        try:
            orders = self._client.trading.get_orders()
            for o in orders:
                if str(getattr(o, "symbol", "")) == ticker:
                    oid = str(getattr(o, "id", ""))
                    self._client.trading.cancel_order_by_id(oid)
                    logger.info("Cancelled open order %s for %s prior to rebalance", oid, ticker)
        except Exception as exc:
            logger.error("Failed to cancel open orders for %s: %s", ticker, exc)

    def rebalance(self, target_positions: dict[str, float]) -> list[str]:
        """Berechnet Deltas und führt Orders aus. Löscht vorher alte Protective Stops."""
        deltas = self._positions.diff(target_positions)
        order_ids: list[str] = []

        for ticker, delta in deltas.items():
            if abs(delta) < 1e-5:
                continue
            
            # Alte offene Orders vor der Positionsanpassung löschen
            self.cancel_open_orders_for_ticker(ticker)

            side = "buy" if delta > 0 else "sell"
            oid = self.submit_order(ticker, abs(delta), side, order_type="market")
            if oid:
                order_ids.append(oid)

        self._submitted_ids.extend(order_ids)
        return order_ids

    def submit_order(
        self,
        ticker: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> str:
        if qty <= 0:
            return ""

        from alpaca.trading.requests import (
            MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY
        extra = {"client_order_id": client_order_id} if client_order_id else {}

        rounded_qty = round(qty, 4)

        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit order requires limit_price")
            request = LimitOrderRequest(
                symbol=ticker, qty=rounded_qty, side=order_side,
                time_in_force=tif, limit_price=limit_price, **extra,
            )
        elif order_type == "stop":
            if stop_price is None:
                raise ValueError("stop order requires stop_price")
            request = StopOrderRequest(
                symbol=ticker, qty=rounded_qty, side=order_side,
                time_in_force=tif, stop_price=stop_price, **extra,
            )
        else:
            request = MarketOrderRequest(
                symbol=ticker, qty=rounded_qty, side=order_side, time_in_force=tif,
                **extra,
            )

        try:
            order = self._client.trading.submit_order(request)
        except Exception as exc:
            logger.error("Order REJECTED for %s %s x%.4f: %s", side, ticker, qty, exc)
            return ""

        status = str(getattr(order, "status", "")).lower()
        oid = str(getattr(order, "id", ""))

        if status == "rejected":
            logger.error("Order rejected for %s %s x%.4f (id=%s)", side, ticker, qty, oid)
            return ""

        logger.info("Submitted %s %s %s x%.4f (id=%s, status=%s)",
                    order_type, side, ticker, rounded_qty, oid, status)
        return oid

    def submit_stop_loss(self, ticker: str, qty: float, stop_price: float, side: str = "sell") -> str:
        return self.submit_order(ticker, qty, side, order_type="stop", stop_price=stop_price)

    def modify_stop(self, order_id: str, new_stop_price: float) -> str:
        from alpaca.trading.requests import ReplaceOrderRequest
        try:
            updated = self._client.trading.replace_order_by_id(
                order_id, ReplaceOrderRequest(stop_price=new_stop_price)
            )
        except Exception as exc:
            logger.error("Failed to modify stop on %s: %s", order_id, exc)
            return ""
        new_id = str(getattr(updated, "id", order_id))
        logger.info("Modified stop on %s -> %.4f (id=%s)", order_id, new_stop_price, new_id)
        return new_id

    def cancel_order(self, order_id: str) -> None:
        self._client.trading.cancel_order_by_id(order_id)
        logger.info("Cancelled order %s", order_id)

    def cancel_all_open_orders(self) -> None:
        self._client.trading.cancel_orders()
        logger.info("Cancelled all open orders.")

    def await_fills(self, order_ids: list[str], timeout: int = 30) -> dict:
        """Wartet auf Fills. Bei Timeout werden hängende Orders automatisch storniert."""
        deadline = time.time() + timeout
        results: dict[str, dict] = {}
        pending = set(order_ids)

        while pending and time.time() < deadline:
            for oid in list(pending):
                try:
                    order = self._client.trading.get_order_by_id(oid)
                    status = str(getattr(order, "status", "")).lower()
                    filled_qty = float(getattr(order, "filled_qty", 0) or 0)
                    results[oid] = {"status": status, "filled_qty": filled_qty}
                    if status in _TERMINAL_STATUSES:
                        pending.discard(oid)
                except Exception as exc:
                    logger.error("Error checking status for order %s: %s", oid, exc)
            if pending:
                time.sleep(1)

        if pending:
            logger.warning("Timeout bei %d Order(s). Storniere verbleibende Orders...", len(pending))
            for oid in pending:
                try:
                    self._client.trading.cancel_order_by_id(oid)
                    logger.info("Order %s wegen Timeout storniert", oid)
                    results[oid] = {"status": "canceled_on_timeout", "filled_qty": results.get(oid, {}).get("filled_qty", 0.0)}
                except Exception as exc:
                    logger.error("Fehler beim Stornieren der Timeout-Order %s: %s", oid, exc)
            
            raise TimeoutError(f"{len(pending)} order(s) timed out and were cancelled: {pending}")

        return results
