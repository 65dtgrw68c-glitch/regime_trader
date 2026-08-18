"""Broker interface — the provider-agnostic contract every broker client must fulfil."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

# HTTP statuses that mean "this request will never succeed, no matter how
# many times you retry it" — bad/expired credentials or a permissions
# problem, not a transient network blip. Every retry helper in broker/
# checks this before backing off, so a 401 fails fast as the config error it
# is instead of burning 3 rounds of exponential backoff first.
_NON_TRANSIENT_STATUS_CODES = frozenset({401, 403})


def is_non_transient_error(exc: Exception) -> bool:
    """True if `exc` carries an HTTP status that retrying cannot fix.

    Handles alpaca-py's `APIError` (exposes `.status_code` directly) and the
    more general `requests`-style shape (`.response.status_code`), without
    importing either SDK — this is called from hot retry loops that must
    stay import-light.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in _NON_TRANSIENT_STATUS_CODES


class BaseBroker(ABC):
    """Abstract base every concrete broker client inherits from."""

    @abstractmethod
    def connect(self) -> None:
        """Establish the underlying broker session (may be lazy)."""
        raise NotImplementedError

    @abstractmethod
    def verify_connection(self) -> bool:
        """Return True if the session works and the account is active."""
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> dict:
        """dict with keys: buying_power, cash, equity, portfolio_value, status."""
        raise NotImplementedError

    @abstractmethod
    def get_clock(self) -> dict:
        """dict with keys: is_open (bool), next_open, next_close."""
        raise NotImplementedError

    def is_market_open(self) -> bool:
        return bool(self.get_clock().get("is_open", False))

    @property
    def trading(self) -> Any:  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} exposes no trading handle.")

    @property
    def data(self) -> Any:  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} exposes no data handle.")
