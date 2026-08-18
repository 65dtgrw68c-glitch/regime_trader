from __future__ import annotations

import logging
import time
from typing import Optional

from broker.base import is_non_transient_error

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
        self._expected_prices: dict[str, float] = {}

    def _call_with_retry(self, fn, *args, max_retries: int = 3, delay: float = 1.0, **kwargs):
        """Retry transient broker API calls with exponential backoff.

        Used for status/cancel calls and only for idempotent order submissions
        where a stable client_order_id is provided. A 401/403 is a
        credentials/permissions problem, not transient, so it raises
        immediately instead of burning the full backoff first.
        """
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if is_non_transient_error(exc):
                    logger.error(
                        "Broker call %s failed with a non-retryable auth/permission "
                        "error: %s", getattr(fn, "__name__", str(fn)), exc,
                    )
                    raise
                last_exc = exc
                logger.warning(
                    "Broker call %s failed attempt %d/%d: %s",
                    getattr(fn, "__name__", str(fn)),
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(delay * (2 ** (attempt - 1)))
        raise last_exc

    def cancel_open_orders_for_ticker(self, ticker: str) -> None:
        """Storniert alle offenen Orders für ein Symbol vor dem Rebalancing (Schutz vor verwaisten Stops)."""
        try:
            orders = self._call_with_retry(self._client.trading.get_orders)
            for o in orders:
                if str(getattr(o, "symbol", "")) == ticker:
                    oid = str(getattr(o, "id", ""))
                    self._call_with_retry(self._client.trading.cancel_order_by_id, oid)
                    logger.info("Cancelled open order %s for %s prior to rebalance", oid, ticker)
        except Exception as exc:
            logger.error("Failed to cancel open orders for %s: %s", ticker, exc)

    def rebalance(
        self,
        target_positions: dict[str, float],
        prices: Optional[dict[str, float]] = None,
        run_tag: Optional[str] = None,
    ) -> list[str]:
        """Berechnet Deltas und führt Orders aus. Löscht vorher alte Protective Stops.

        `prices` (decision-time price per ticker) is optional and, when given,
        recorded as each order's expected fill price for slippage tracking.

        `run_tag` (typically the bar date, e.g. "2026-08-17", or "flatten-…")
        seeds a stable client_order_id per ticker+side, the same idempotency
        mechanism `_submit()` already uses on the single-asset path. Two
        overlapping runs (a manual start racing the timer, a catch-up fire
        after reboot) diff against the same stale snapshot and would compute
        the same delta — without this, both submit; with it, the broker
        rejects the second as a duplicate instead of doubling the position.
        """
        deltas = self._positions.diff(target_positions)
        order_ids: list[str] = []

        for ticker, delta in deltas.items():
            if abs(delta) < 1e-5:
                continue

            # Alte offene Orders vor der Positionsanpassung löschen
            self.cancel_open_orders_for_ticker(ticker)

            side = "buy" if delta > 0 else "sell"
            expected_price = prices.get(ticker) if prices else None
            coid = f"rt-{ticker}-{run_tag}-{side}" if run_tag is not None else None
            oid = self.submit_order(
                ticker, abs(delta), side, order_type="market",
                client_order_id=coid, expected_price=expected_price,
            )
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
        expected_price: Optional[float] = None,
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
            if not float(rounded_qty).is_integer():
                # Alpaca rejects stop/stop-limit orders on fractional qty
                # (fractional trading only supports market/limit DAY orders).
                # Fail here instead of round-tripping a doomed request to the broker.
                logger.error(
                    "Stop order rejected for %s: fractional qty %.4f not "
                    "supported by broker for stop orders.", ticker, rounded_qty,
                )
                return ""
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
            if client_order_id:
                order = self._call_with_retry(self._client.trading.submit_order, request)
            else:
                order = self._client.trading.submit_order(request)
        except Exception as exc:
            logger.error("Order REJECTED for %s %s x%.4f: %s", side, ticker, qty, exc)
            return ""

        status = str(getattr(order, "status", "")).lower()
        oid = str(getattr(order, "id", ""))

        if status == "rejected":
            logger.error("Order rejected for %s %s x%.4f (id=%s)", side, ticker, qty, oid)
            return ""

        if oid and expected_price is not None:
            self._expected_prices[oid] = float(expected_price)

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
        self._call_with_retry(self._client.trading.cancel_order_by_id, order_id)
        logger.info("Cancelled order %s", order_id)

    def cancel_all_open_orders(self) -> None:
        self._call_with_retry(self._client.trading.cancel_orders)
        logger.info("Cancelled all open orders.")

    def _record_fill(self, oid: str, order: object) -> None:
        """Log a fill to the trade log, if there's a logger and the order is
        actually filled. Reads (not pops) `_expected_prices` — callers share
        one bulk cleanup pass at the end of `await_fills`."""
        if not self._trade_logger:
            return
        if str(getattr(order, "status", "")).lower() != "filled":
            return
        self._trade_logger.log_fill({
            "ticker": str(getattr(order, "symbol", "")),
            "qty": float(getattr(order, "filled_qty", 0) or 0),
            "fill_price": float(getattr(order, "filled_avg_price", 0) or 0),
            "expected_price": self._expected_prices.get(oid),
            "order_id": oid,
        })

    def await_fills(self, order_ids: list[str], timeout: int = 30) -> dict:
        """
        Poll until every order reaches a terminal status or `timeout`
        seconds elapse. Returns a dict of order_id -> {status, filled_qty}.

        Timeout handling:
        - recheck each still-pending order before cancelling;
        - cancel only orders that are still non-terminal;
        - treat "already filled" cancel errors as filled instead of failure.
        """
        deadline = time.time() + timeout
        results: dict[str, dict] = {}
        pending = set(order_ids)

        while pending and time.time() < deadline:
            for oid in list(pending):
                order = self._call_with_retry(self._client.trading.get_order_by_id, oid)
                status = str(getattr(order, "status", "")).lower()
                filled_qty = float(getattr(order, "filled_qty", 0) or 0)
                results[oid] = {"status": status, "filled_qty": filled_qty}

                if status in _TERMINAL_STATUSES:
                    pending.discard(oid)
                    self._record_fill(oid, order)

            if pending:
                time.sleep(1)

        if pending:
            logger.warning(
                "Timeout reached with %d pending order(s). Rechecking before cancel...",
                len(pending),
            )

            still_pending = set()

            for oid in list(pending):
                try:
                    order = self._call_with_retry(self._client.trading.get_order_by_id, oid)
                    status = str(getattr(order, "status", "")).lower()
                    filled_qty = float(getattr(order, "filled_qty", 0) or 0)
                    results[oid] = {"status": status, "filled_qty": filled_qty}

                    if status in _TERMINAL_STATUSES:
                        logger.info(
                            "Order %s reached terminal status after timeout: %s",
                            oid,
                            status,
                        )
                        self._record_fill(oid, order)
                        continue

                    still_pending.add(oid)
                except Exception as exc:
                    logger.warning(
                        "Could not recheck timed-out order %s before cancel: %s",
                        oid,
                        exc,
                    )
                    still_pending.add(oid)

            for oid in list(still_pending):
                try:
                    # Do not retry this timeout-cleanup cancel. Certain broker
                    # responses such as "already filled" are terminal, not
                    # transient, and should be classified immediately.
                    self._client.trading.cancel_order_by_id(oid)
                    logger.info("Cancelled timed-out order %s", oid)
                    results[oid] = {
                        "status": "canceled_on_timeout",
                        "filled_qty": results.get(oid, {}).get("filled_qty", 0.0),
                    }
                except Exception as exc:
                    msg = str(exc).lower()
                    if "already" in msg and "filled" in msg:
                        logger.info(
                            "Timed-out order %s was already filled when cancel was attempted.",
                            oid,
                        )
                        # The pre-cancel snapshot in `results` predates the
                        # fill (that's exactly why cancel was rejected) — its
                        # qty/price are stale, so re-fetch before logging.
                        try:
                            filled_order = self._call_with_retry(
                                self._client.trading.get_order_by_id, oid
                            )
                        except Exception:
                            filled_order = None
                        if filled_order is not None:
                            self._record_fill(oid, filled_order)
                            filled_qty = float(getattr(filled_order, "filled_qty", 0) or 0)
                        else:
                            filled_qty = results.get(oid, {}).get("filled_qty", 0.0)
                        results[oid] = {"status": "filled", "filled_qty": filled_qty}
                        still_pending.discard(oid)
                    else:
                        logger.error("Failed to cancel timed-out order %s: %s", oid, exc)

            unresolved = {
                oid
                for oid in still_pending
                if results.get(oid, {}).get("status") not in _TERMINAL_STATUSES
                and results.get(oid, {}).get("status") != "canceled_on_timeout"
            }

            if unresolved:
                raise TimeoutError(
                    f"{len(unresolved)} order(s) not terminal after timeout/cancel: {unresolved}"
                )

        # _record_fill only reads _expected_prices; clear entries for every
        # order_id passed in here so the dict doesn't grow unbounded over
        # uptime (covers orders that never reached a logged fill too, e.g.
        # canceled/rejected).
        for oid in order_ids:
            self._expected_prices.pop(oid, None)

        return results
