"""
Alpaca Client — authenticated API wrapper for Alpaca Markets with built-in retry resilience.
"""

from __future__ import annotations

import logging
import os
import time
from functools import wraps
from typing import Any, Optional

from settings import config
from broker.base import BaseBroker

logger = logging.getLogger(__name__)


def with_retry(max_retries: int = 3, delay: float = 1.0):
    """Decorator für automatisches Retry bei temporären Netzwerkausfällen oder API-Rate-Limits."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    logger.warning("API Call %s fehlgeschlagen (Versuch %d/%d): %s", func.__name__, attempt, max_retries, exc)
                    if attempt < max_retries:
                        time.sleep(delay * (2 ** (attempt - 1)))
            logger.error("API Call %s nach %d Versuchen fehlgeschlagen.", func.__name__, max_retries)
            raise last_exc
        return wrapper
    return decorator


class AlpacaClient(BaseBroker):
    """
    Thin wrapper around alpaca-py's trading + historical data clients.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        paper: Optional[bool] = None,
    ) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        self.api_key    = api_key    or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url   = base_url   or os.getenv("ALPACA_BASE_URL", "")
        env_paper = os.getenv("PAPER", "true").lower() == "true"
        self.paper = env_paper if paper is None else paper

        self._trading: Any = None
        self._data: Any = None
        self._max_retries = config.BROKER.get("max_retries", 3)
        self._retry_delay = config.BROKER.get("retry_delay", 1.0)

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
        """Confirm the connection works and the account is active."""
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

    @with_retry(max_retries=3, delay=1.0)
    def get_account(self) -> dict:
        """Return account info as a plain dict (mit Retry-Schutz)."""
        acct = self.trading.get_account()
        return {
            "buying_power":   float(getattr(acct, "buying_power", 0.0)),
            "cash":           float(getattr(acct, "cash", 0.0)),
            "equity":         float(getattr(acct, "equity", 0.0)),
            "portfolio_value": float(getattr(acct, "portfolio_value", 0.0)),
            "status":         str(getattr(acct, "status", "")),
        }

    @with_retry(max_retries=3, delay=1.0)
    def get_clock(self) -> dict:
        """Return the market clock as a plain dict (mit Retry-Schutz)."""
        clock = self.trading.get_clock()
        return {
            "is_open":    bool(getattr(clock, "is_open", False)),
            "next_open":  getattr(clock, "next_open", None),
            "next_close": getattr(clock, "next_close", None),
        }

    def is_market_open(self) -> bool:
        return self.get_clock()["is_open"]
