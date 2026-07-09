"""Broker interface — the provider-agnostic contract every broker client must fulfil."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


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
