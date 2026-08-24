"""
Alpaca Client — authenticated API wrapper for Alpaca Markets with built-in retry resilience.
"""

from __future__ import annotations

import logging
import os
import random
import time
from functools import wraps
from typing import Any, Optional

from settings import config
from broker.base import BaseBroker, is_non_transient_error

logger = logging.getLogger(__name__)


def _resolve_paper_flag(raw: str) -> bool:
    """Fail-closed parse of the PAPER env var.

    python-dotenv and systemd's EnvironmentFile= disagree on inline
    comments: dotenv strips them, systemd takes the line literally
    (`PAPER=true   # comment` becomes the value `"true   # comment"`,
    which is neither of dotenv's `"true"` nor `"false"` and flipped a
    naive `== "true"` check to Live). Taking only the first whitespace
    token keeps both loaders' explicit "true"/"false" intent intact,
    and treats anything unrecognized as paper (safe default) rather than
    live (fail-open into real money).
    """
    token = raw.strip().split()[0].lower() if raw.strip() else ""
    return token not in ("false", "0", "no", "off", "live")


def _infer_paper_from_key(key: str) -> Optional[bool]:
    """Best-effort read of Alpaca's own paper/live key-prefix convention."""
    prefix = key[:2].upper()
    if prefix == "PK":
        return True
    if prefix == "AK":
        return False
    return None


def with_retry(max_retries: int = 3, delay: float = 1.0):
    """Decorator für automatisches Retry bei temporären Netzwerkausfällen oder API-Rate-Limits.

    A 401/403 is a credentials/permissions problem, not a network blip —
    retrying it with backoff just delays reporting a config error that will
    never resolve itself, so those fail immediately instead.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if is_non_transient_error(exc):
                        logger.error(
                            "API Call %s failed with a non-retryable auth/permission "
                            "error: %s", func.__name__, exc,
                        )
                        raise
                    last_exc = exc
                    logger.warning("API Call %s fehlgeschlagen (Versuch %d/%d): %s", func.__name__, attempt, max_retries, exc)
                    if attempt < max_retries:
                        # +/-20% jitter so concurrent callers hitting the
                        # same transient error don't retry in lockstep.
                        wait = delay * (2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
                        time.sleep(wait)
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
        paper: Optional[bool] = None,
    ) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        self.api_key    = api_key    or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        env_paper = _resolve_paper_flag(os.getenv("PAPER", "true"))
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

        # K1 hardening: `self.paper` is the ONLY switch between simulated and
        # real money, so refuse to connect on ANY ambiguity instead of
        # silently picking a side. Two independent cross-checks:
        expected_mode = "paper" if self.paper else "live"

        # 1) settings/config.py's declared mode must agree with the env flag
        #    — catches a stale/forgotten config edit or a corrupted env var.
        configured_mode = str(config.BROKER.get("mode", "paper")).lower()
        if configured_mode != expected_mode:
            raise RuntimeError(
                f"Refusing to connect: PAPER env resolves to '{expected_mode}' "
                f"but config.BROKER['mode'] is '{configured_mode}'. Fix "
                f"whichever one is wrong before trading — this mismatch is "
                f"exactly the kind of ambiguity that must never decide "
                f"between paper and live money."
            )

        # 2) Alpaca's own key-prefix convention (PK.. = paper, AK.. = live)
        #    must agree with the resolved mode — catches paper keys pasted
        #    into a live env or vice versa.
        inferred = _infer_paper_from_key(self.api_key)
        if inferred is not None and inferred != self.paper:
            raise RuntimeError(
                f"Refusing to connect: ALPACA_API_KEY looks like a "
                f"{'PAPER' if inferred else 'LIVE'} key but PAPER env "
                f"resolves to {expected_mode.upper()} trading. Check "
                f"which credentials and PAPER value belong together in .env."
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
            "account_number": str(getattr(acct, "account_number", "")),
            "buying_power":   float(getattr(acct, "buying_power", 0.0)),
            "cash":           float(getattr(acct, "cash", 0.0)),
            "equity":         float(getattr(acct, "equity", 0.0)),
            "portfolio_value": float(getattr(acct, "portfolio_value", 0.0)),
            "status":         str(getattr(acct, "status", "")),
        }

    @with_retry(max_retries=3, delay=1.0)
    def get_account_activities(self, activity_type: str) -> list[dict]:
        """Return raw account activity entries (e.g. "INT" for cash interest).

        alpaca-py's TradingClient has no typed wrapper for this endpoint, so
        this goes through its generic `.get()` escape hatch (same auth/base
        URL handling as every other call here) and returns whatever JSON the
        API gives back.
        """
        result = self.trading.get(f"/account/activities/{activity_type}")
        return result if isinstance(result, list) else []

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
