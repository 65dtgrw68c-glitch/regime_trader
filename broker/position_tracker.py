from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from broker.base import is_non_transient_error

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """A single open position supporting float quantities."""
    ticker:           str
    qty:              float
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
    def __init__(self, client: Optional[object] = None) -> None:
        self._client = client
        self._positions: dict[str, Position] = {}
        self._cash: float = 0.0
        self._last_closed: list[str] = []

    def _call_with_retry(self, fn, *args, max_retries: int = 3, delay: float = 1.0, **kwargs):
        """Retry transient broker API calls with exponential backoff.

        A 401/403 is a credentials/permissions problem, not transient, so it
        raises immediately instead of burning the full backoff first.
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
                    # +/-20% jitter: several tickers hitting a transient
                    # error in the same cycle would otherwise retry in
                    # lockstep and re-collide on the same second.
                    wait = delay * (2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
                    time.sleep(wait)
        raise last_exc

    def refresh(self) -> list[str]:
        if self._client is None:
            raise RuntimeError("PositionTracker has no broker client to refresh from.")
        raw = self._call_with_retry(self._client.trading.get_all_positions)
        snapshot: dict[str, Position] = {}
        for p in raw:
            ticker = str(getattr(p, "symbol"))
            snapshot[ticker] = Position(
                ticker=ticker,
                qty=float(getattr(p, "qty", 0.0)),
                avg_entry_price=float(getattr(p, "avg_entry_price", 0.0)),
                current_price=float(getattr(p, "current_price", 0.0)),
                unrealised_pnl=float(getattr(p, "unrealized_pl", 0.0)),
            )

        closed = self.detect_closed_positions(snapshot)
        if closed:
            logger.warning("Positions closed externally: %s", closed)
        self._last_closed = closed
        self._positions = snapshot
        return closed

    def detect_closed_positions(self, new_snapshot: dict[str, Position]) -> list[str]:
        closed = []
        for ticker, pos in self._positions.items():
            if pos.qty != 0 and (
                ticker not in new_snapshot or new_snapshot[ticker].qty == 0
            ):
                closed.append(ticker)
        return closed

    def last_closed(self) -> list[str]:
        return list(self._last_closed)

    def set_positions(self, positions: dict[str, Position]) -> None:
        self._positions = dict(positions)

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_portfolio_value(self) -> float:
        return self._cash + sum(p.market_value for p in self._positions.values())

    def set_cash(self, cash: float) -> None:
        self._cash = float(cash)

    def get_exposure(self) -> dict:
        gross = sum(abs(p.market_value) for p in self._positions.values())
        net   = sum(p.market_value for p in self._positions.values())
        per_ticker = {t: p.market_value for t, p in self._positions.items()}
        return {"gross": gross, "net": net, "per_ticker": per_ticker}

    def diff(self, target: dict[str, float]) -> dict[str, float]:
        """Berechnet die Differenz zwischen Ist und Soll (Unterstützt float)."""
        deltas: dict[str, float] = {}
        tickers = set(self._positions) | set(target)
        for t in tickers:
            current = self._positions[t].qty if t in self._positions else 0.0
            desired = float(target.get(t, 0.0))
            delta = desired - current
            if abs(delta) <= 1e-5:
                continue
            if delta < 0:
                # Rounding a sell UP in magnitude can oversell past the
                # current holding (e.g. current=10.00005, target=0 rounds
                # to -10.0001, a qty the broker doesn't have). Floor the
                # sell size instead so it never exceeds what's held.
                deltas[t] = -math.floor(abs(delta) * 10_000) / 10_000
            else:
                deltas[t] = round(delta, 4)
        return deltas
