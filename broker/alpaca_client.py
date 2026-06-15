"""
Alpaca Client — authenticated API wrapper for Alpaca Markets.

Import-safety
-------------
The `alpaca-py` SDK is imported lazily inside `connect()` so that this module
(and everything that depends on it) imports cleanly in environments where the
SDK is not installed — e.g. unit tests that inject a mock client.

Credentials are read from the environment (.env) via python-dotenv:
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, PAPER
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from settings import config

logger = logging.getLogger(__name__)


class AlpacaClient:
    """
    Thin wrapper around alpaca-py's trading + historical data clients.

    Usage:
        client = AlpacaClient()
        client.connect()                 # lazily builds the SDK clients
        if client.is_market_open(): ...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        paper: Optional[bool] = None,
    ) -> None:
        # Load .env if python-dotenv is available (optional dependency).
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:                # pragma: no cover - env dependent
            pass

        self.api_key    = api_key    or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url   = base_url   or os.getenv("ALPACA_BASE_URL", "")
        env_paper = os.getenv("PAPER", "true").lower() == "true"
        self.paper = env_paper if paper is None else paper

        self._trading: Any = None
        self._data: Any = None
        self._max_retries = config.BROKER["max_retries"]
        self._retry_delay = config.BROKER["retry_delay"]

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Instantiate the alpaca-py clients (lazy SDK import)."""
        if not self.api_key or not self.secret_key:
            raise RuntimeError(
                "Alpaca credentials missing. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY in your .env file."
            )
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        self._trading = TradingClient(self.api_key, self.secret_key, paper=self.paper)
        self._data = StockHistoricalDataClient(self.api_key, self.secret_key)
        logger.info("AlpacaClient connected (paper=%s).", self.paper)

    def verify_connection(self) -> bool:
        """
        Confirm the connection works and the account is active.
        Returns True on success; logs and returns False on auth/network
        failure instead of raising, so callers can degrade gracefully.
        """
        try:
            if self._trading is None:
                self.connect()
            acct = self.get_account()
        except Exception as exc:
            logger.error("Alpaca connection verification failed: %s", exc)
            return False
        status = acct.get("status", "")
        ok = "ACTIVE" in status.upper()
        if not ok:
            logger.warning("Alpaca account status is '%s' (expected ACTIVE).", status)
        return ok

    # ------------------------------------------------------------------
    # Client accessors
    # ------------------------------------------------------------------

    @property
    def trading(self) -> Any:
        if self._trading is None:
            self.connect()
        return self._trading

    @property
    def data(self) -> Any:
        if self._data is None:
            self.connect()
        return self._data

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        """Return account info as a plain dict."""
        acct = self.trading.get_account()
        return {
            "buying_power":   float(getattr(acct, "buying_power", 0.0)),
            "cash":           float(getattr(acct, "cash", 0.0)),
            "equity":         float(getattr(acct, "equity", 0.0)),
            "portfolio_value": float(getattr(acct, "portfolio_value", 0.0)),
            "status":         str(getattr(acct, "status", "")),
        }

    def get_clock(self) -> dict:
        """Return the market clock as a plain dict."""
        clock = self.trading.get_clock()
        return {
            "is_open":    bool(getattr(clock, "is_open", False)),
            "next_open":  getattr(clock, "next_open", None),
            "next_close": getattr(clock, "next_close", None),
        }

    def is_market_open(self) -> bool:
        return self.get_clock()["is_open"]
