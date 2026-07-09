"""Broker factory — build the configured broker client."""
from __future__ import annotations
from typing import Optional
from settings import config
from broker.base import BaseBroker


def create_broker(provider: Optional[str] = None, **kwargs) -> BaseBroker:
    """Instantiate the broker client for `provider` (default: config)."""
    name = (provider or config.BROKER.get("provider", "alpaca")).lower()
    if name == "alpaca":
        from broker.alpaca_client import AlpacaClient
        return AlpacaClient(**kwargs)
    if name in ("ibkr", "ib", "interactive_brokers"):
        raise NotImplementedError(
            "The Interactive Brokers client is not implemented yet (Phase 2). "
            "Set BROKER['provider'] = 'alpaca' for now."
        )
    raise ValueError(f"Unknown broker provider '{name}'. Valid: 'alpaca', 'ibkr'.")
