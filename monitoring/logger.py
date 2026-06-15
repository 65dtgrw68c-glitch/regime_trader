"""
Logger — structured logging for all system events and trades (Prompt 8).

* Logs to BOTH console and a ROTATING log file.
* `structlog` is used for JSON/contextual logs when available, with a clean
  standard-library fallback so the package imports and tests run without it.
* `TradeLogger` writes a separate CSV event log capturing every monitored
  event with a UTC timestamp and full context:
    - regime changes (new, previous, confidence)
    - trade signals (ticker, direction, regime, allocation %, confidence)
    - orders placed (id, ticker, qty, price, type)
    - order fills (fill price, slippage vs expected)
    - circuit-breaker checks / triggers
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from settings import config

# structlog is optional — degrade gracefully if missing.
try:
    import structlog
    _HAVE_STRUCTLOG = True
except Exception:                       # pragma: no cover - env dependent
    _HAVE_STRUCTLOG = False


_CONFIGURED = False


# ===========================================================================
# Configuration
# ===========================================================================

def configure_logging(
    level: Optional[str] = None,
    log_dir: Optional[str] = None,
) -> None:
    """
    Configure logging once at startup (call from main.py).

    Console output is human-readable; the file handler is a
    RotatingFileHandler (logs/app.log) so logs never grow unbounded.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or config.MONITORING["log_level"]).upper()
    level_value = getattr(logging, level_name, logging.INFO)
    directory = Path(log_dir or config.MONITORING["log_dir"])
    directory.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler()
    rotating = RotatingFileHandler(
        directory / "app.log",
        maxBytes=config.MONITORING.get("log_max_bytes", 5_000_000),
        backupCount=config.MONITORING.get("log_backup_count", 5),
        encoding="utf-8",
    )

    logging.basicConfig(
        level=level_value,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        handlers=[console, rotating],
        force=True,
    )

    if _HAVE_STRUCTLOG:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level_value),
            logger_factory=structlog.PrintLoggerFactory(
                file=open(directory / "structured.jsonl", "a", encoding="utf-8")
            ),
            cache_logger_on_first_use=True,
        )

    _CONFIGURED = True


def get_logger(name: str) -> Any:
    """Return a logger bound to `name` (structlog if available, else stdlib)."""
    if _HAVE_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)


# ===========================================================================
# Trade / event logger (CSV)
# ===========================================================================

class TradeLogger:
    """
    Append-only CSV event log.  Each method writes one row with a UTC
    timestamp and an event type so the full decision history can be
    reconstructed independently of the application log.
    """

    _FIELDS = [
        "timestamp_utc", "event", "ticker", "direction", "regime",
        "prev_regime", "allocation_pct", "confidence", "qty",
        "price", "expected_price", "slippage", "order_id",
        "order_type", "cb_level", "cb_triggered", "detail",
    ]

    def __init__(self, path: Optional[str] = None) -> None:
        log_dir = Path(config.MONITORING["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path = Path(path) if path else log_dir / "trades.csv"
        if not self._path.exists():
            self._write_header()

    def _write_header(self) -> None:
        with self._path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self._FIELDS).writeheader()

    def _append(self, row: dict) -> None:
        full = {k: row.get(k, "") for k in self._FIELDS}
        full["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        with self._path.open("a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self._FIELDS).writerow(full)

    # ------------------------------------------------------------------
    # Event methods (one per monitored event type)
    # ------------------------------------------------------------------

    def log_regime_change(self, old: Any, new: Any,
                          confidence: Optional[float] = None,
                          proba: Optional[list] = None) -> None:
        self._append({
            "event": "REGIME_CHANGE",
            "regime": new,
            "prev_regime": old,
            "confidence": confidence,
            "detail": f"{old}->{new} proba={proba}" if proba is not None else f"{old}->{new}",
        })

    def log_signal(self, ticker: str, direction: str, regime: Any,
                   allocation_pct: float, confidence: float) -> None:
        self._append({
            "event": "SIGNAL",
            "ticker": ticker,
            "direction": direction,
            "regime": regime,
            "allocation_pct": allocation_pct,
            "confidence": confidence,
        })

    def log_order(self, order: dict) -> None:
        self._append({
            "event": "ORDER",
            "ticker": order.get("ticker"),
            "direction": order.get("side", order.get("direction")),
            "qty": order.get("qty"),
            "price": order.get("price"),
            "order_id": order.get("order_id"),
            "order_type": order.get("order_type", "market"),
            "regime": order.get("regime"),
            "confidence": order.get("confidence"),
        })

    def log_fill(self, fill: dict) -> None:
        """Log a fill and compute slippage vs the expected price if provided."""
        fill_price = fill.get("fill_price", fill.get("price"))
        expected = fill.get("expected_price")
        slippage = ""
        if expected not in (None, "") and fill_price not in (None, ""):
            slippage = float(fill_price) - float(expected)
        self._append({
            "event": "FILL",
            "ticker": fill.get("ticker"),
            "direction": fill.get("side", fill.get("direction")),
            "qty": fill.get("qty"),
            "price": fill_price,
            "expected_price": expected,
            "slippage": slippage,
            "order_id": fill.get("order_id"),
        })

    def log_circuit_breaker(self, level: Any, triggered: bool,
                            context: Optional[dict] = None) -> None:
        self._append({
            "event": "CIRCUIT_BREAKER",
            "cb_level": level,
            "cb_triggered": triggered,
            "detail": str(context) if context else "",
        })

    def log_error(self, message: str, context: Optional[dict] = None) -> None:
        self._append({
            "event": "ERROR",
            "detail": f"{message} | {context}" if context else message,
        })

    @property
    def path(self) -> Path:
        return self._path
