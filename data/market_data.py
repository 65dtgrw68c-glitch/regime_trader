"""
Market Data — real-time and historical OHLCV feeds via Alpaca.

Capabilities (Prompt 6)
-----------------------
* Real-time bar data at the configured interval (default 5 minutes).
* Historical daily bars for HMM training (~2 years).
* Gap handling and basic rate-limit back-off.
* Local parquet cache to avoid redundant historical requests.

The alpaca-py data SDK is imported lazily so this module is import-safe and
unit-testable without the SDK or network access.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from settings import config

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(config.MONITORING["log_dir"]).parent / "data_cache"


class MarketDataFeed:
    """Historical + real-time market data access."""

    def __init__(
        self,
        client: Optional[object] = None,
        cache_dir: Optional[str] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ) -> None:
        self._client = client
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_retries = max_retries if max_retries is not None else config.BROKER["max_retries"]
        self._retry_delay = retry_delay if retry_delay is not None else config.BROKER["retry_delay"]
        self._stream = None

    # ------------------------------------------------------------------
    # Historical bars
    # ------------------------------------------------------------------

    def get_historical_bars(
        self,
        tickers: list[str],
        start: str,
        end: Optional[str] = None,
        timeframe: str = "1Day",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch historical bars for one or more tickers.

        Returns a tidy DataFrame indexed by (timestamp, ticker) with
        open/high/low/close/volume columns.  Uses a parquet cache keyed by
        ticker+timeframe when `use_cache` is True.
        """
        frames = []
        for ticker in tickers:
            df = None
            if use_cache:
                df = self._load_cache(ticker, timeframe)
            if df is None:
                df = self._fetch_bars_with_retry(ticker, start, end, timeframe)
                if use_cache and not df.empty:
                    self._save_cache(ticker, timeframe, df)
            df = self._handle_gaps(df)
            df["ticker"] = ticker
            frames.append(df)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames)
        combined = combined.set_index("ticker", append=True)
        return combined

    def get_training_data(self, ticker: str, years: float = 2.0) -> pd.DataFrame:
        """Fetch ~`years` of daily bars for HMM training (single ticker)."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(365 * years))
        df = self.get_historical_bars(
            [ticker], start.date().isoformat(), end.date().isoformat(), "1Day",
        )
        # Drop the ticker level for single-ticker training convenience.
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="ticker")
        return df

    # ------------------------------------------------------------------
    # Latest / real-time bars
    # ------------------------------------------------------------------

    def get_latest_bar(self, ticker: str, timeframe: str = "5Min") -> pd.Series:
        """Fetch the most recent bar for `ticker` at the given interval."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=5)   # enough lookback to guarantee a bar
        df = self._fetch_bars_with_retry(
            ticker, start.isoformat(), end.isoformat(), timeframe,
        )
        if df.empty:
            raise RuntimeError(f"No recent bar available for {ticker}.")
        return df.iloc[-1]

    def start_stream(self, tickers: list[str], callback: Callable) -> None:
        """
        Subscribe to the real-time bar stream and invoke `callback(bar)` for
        each new bar.  Lazy-imports the live data SDK.
        """
        from alpaca.data.live import StockDataStream

        api_key = getattr(self._client, "api_key", "")
        secret  = getattr(self._client, "secret_key", "")
        self._stream = StockDataStream(api_key, secret)

        async def _handler(bar):
            callback(bar)

        for t in tickers:
            self._stream.subscribe_bars(_handler, t)
        logger.info("Starting real-time bar stream for %s", tickers)
        self._stream.run()

    def stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            logger.info("Stopped real-time bar stream.")

    # ------------------------------------------------------------------
    # Internal: fetch with retry / rate-limit back-off
    # ------------------------------------------------------------------

    def _fetch_bars_with_retry(
        self, ticker: str, start: str, end: Optional[str], timeframe: str,
    ) -> pd.DataFrame:
        """Call the Alpaca data API with simple exponential back-off."""
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                return self._fetch_bars(ticker, start, end, timeframe)
            except Exception as exc:                # pragma: no cover - network
                last_exc = exc
                wait = self._retry_delay * (2 ** attempt)
                logger.warning(
                    "Bar fetch failed for %s (attempt %d/%d): %s — retrying in %.1fs",
                    ticker, attempt + 1, self._max_retries, exc, wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"Failed to fetch bars for {ticker}: {last_exc}")

    def _fetch_bars(
        self, ticker: str, start: str, end: Optional[str], timeframe: str,
    ) -> pd.DataFrame:
        """Single Alpaca data request → tidy OHLCV DataFrame."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        data_client = getattr(self._client, "data", None) or StockHistoricalDataClient(
            getattr(self._client, "api_key", ""),
            getattr(self._client, "secret_key", ""),
        )
        tf = self._parse_timeframe(timeframe, TimeFrame, TimeFrameUnit)
        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=tf,
            start=pd.Timestamp(start).to_pydatetime(),
            end=pd.Timestamp(end).to_pydatetime() if end else None,
        )
        bars = data_client.get_stock_bars(request)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")
        return df[["open", "high", "low", "close", "volume"]].copy()

    @staticmethod
    def _parse_timeframe(timeframe: str, TimeFrame, TimeFrameUnit):
        """Map a string like '5Min' / '1Day' to an alpaca TimeFrame."""
        mapping = {
            "1Min":  (1, TimeFrameUnit.Minute),
            "5Min":  (5, TimeFrameUnit.Minute),
            "15Min": (15, TimeFrameUnit.Minute),
            "1Hour": (1, TimeFrameUnit.Hour),
            "1Day":  (1, TimeFrameUnit.Day),
        }
        amount, unit = mapping.get(timeframe, (1, TimeFrameUnit.Day))
        return TimeFrame(amount, unit)

    # ------------------------------------------------------------------
    # Gap handling & cache
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_gaps(df: pd.DataFrame) -> pd.DataFrame:
        """Sort, drop duplicate timestamps, and forward-fill price gaps."""
        if df.empty:
            return df
        df = df[~df.index.duplicated(keep="last")].sort_index()
        # Forward-fill prices across small gaps; volume gaps fill with 0.
        price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
        df[price_cols] = df[price_cols].ffill()
        if "volume" in df.columns:
            df["volume"] = df["volume"].fillna(0.0)
        return df.dropna(subset=price_cols)

    def _cache_path(self, ticker: str, timeframe: str) -> Path:
        return self._cache_dir / f"{ticker}_{timeframe}.parquet"

    def _load_cache(self, ticker: str, timeframe: str = "1Day") -> Optional[pd.DataFrame]:
        path = self._cache_path(ticker, timeframe)
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception as exc:            # pragma: no cover
                logger.warning("Failed to read cache %s: %s", path, exc)
        return None

    def _save_cache(self, ticker: str, timeframe: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(ticker, timeframe))
        except Exception as exc:                # pragma: no cover
            logger.warning("Failed to write cache for %s: %s", ticker, exc)
