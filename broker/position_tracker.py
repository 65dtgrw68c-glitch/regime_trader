"""
Position Tracker — real-time view of all open positions.

The `diff()` and exposure calculations are pure Python so they can be unit
tested without any Alpaca connection.  `refresh()` is the only method that
talks to the broker, and it accepts any object exposing `.trading`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """A single open position."""
    ticker:           str
    qty:              int
    avg_entry_price:  float
    current_price:    float = 0.0
    unrealised_pnl:   float = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def is_long(self) -> bool:
        return self.qty > 0


class PositionTracker:
    """
    Maintains an in-memory snapshot of open positions and computes the
    deltas required to reach a target portfolio.
    """

    def __init__(self, client: Optional[object] = None) -> None:
        self._client = client
        self._positions: dict[str, Position] = {}
        self._cash: float = 0.0
        # Tickers detected as closed externally on the most recent refresh.
        self._last_closed: list[str] = []

    # ------------------------------------------------------------------
    # Broker sync
    # ------------------------------------------------------------------

    def refresh(self) -> list[str]:
        """
        Pull the latest positions from Alpaca into the local snapshot.

        Returns the list of tickers that were open locally but no longer
        appear at the broker — i.e. closed externally (e.g. a broker-side
        stop-loss fired).  This lets the live loop react to surprise exits.
        """
        if self._client is None:
            raise RuntimeError("PositionTracker has no broker client to refresh from.")
        raw = self._client.trading.get_all_positions()
        snapshot: dict[str, Position] = {}
        for p in raw:
            ticker = str(getattr(p, "symbol"))
            snapshot[ticker] = Position(
                ticker=ticker,
                qty=int(float(getattr(p, "qty", 0))),
                avg_entry_price=float(getattr(p, "avg_entry_price", 0.0)),
                current_price=float(getattr(p, "current_price", 0.0)),
                unrealised_pnl=float(getattr(p, "unrealized_pl", 0.0)),
            )

        closed = self.detect_closed_positions(snapshot)
        if closed:
            logger.warning("Positions closed externally: %s", closed)
        self._last_closed = closed
        self._positions = snapshot
        logger.debug("PositionTracker refreshed: %d positions.", len(snapshot))
        return closed

    def detect_closed_positions(self, new_snapshot: dict[str, Position]) -> list[str]:
        """
        Compare the incoming snapshot against the current local state and
        return tickers that were open before but are now gone or flat.
        """
        closed = []
        for ticker, pos in self._positions.items():
            if pos.qty != 0 and (
                ticker not in new_snapshot or new_snapshot[ticker].qty == 0
            ):
                closed.append(ticker)
        return closed

    def last_closed(self) -> list[str]:
        """Tickers closed externally as of the most recent refresh()."""
        return list(self._last_closed)

    def set_positions(self, positions: dict[str, Position]) -> None:
        """Directly set the snapshot (used in tests / simulation)."""
        self._positions = dict(positions)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_portfolio_value(self) -> float:
        """Cash + market value of all positions."""
        return self._cash + sum(p.market_value for p in self._positions.values())

    def set_cash(self, cash: float) -> None:
        self._cash = float(cash)

    def get_exposure(self) -> dict:
        """Return gross, net, and per-ticker exposure."""
        gross = sum(abs(p.market_value) for p in self._positions.values())
        net   = sum(p.market_value for p in self._positions.values())
        per_ticker = {t: p.market_value for t, p in self._positions.items()}
        return {"gross": gross, "net": net, "per_ticker": per_ticker}

    # ------------------------------------------------------------------
    # Diff — the core of rebalancing
    # ------------------------------------------------------------------

    def diff(self, target: dict[str, int]) -> dict[str, int]:
        """
        Return share deltas needed to move from the current snapshot to the
        target.  Positive = buy, negative = sell.  Tickers held but absent
        from the target are fully closed (delta = -current_qty).
        """
        deltas: dict[str, int] = {}
        tickers = set(self._positions) | set(target)
        for t in tickers:
            current = self._positions[t].qty if t in self._positions else 0
            desired = int(target.get(t, 0))
            delta = desired - current
            if delta != 0:
                deltas[t] = delta
        return deltas
