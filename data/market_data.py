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
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from settings import config

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(config.MONITORING["log_dir"]).parent / "data_cache"

# Exchange calendar reference for deciding whether a daily bar is finished.
_EXCHANGE_TZ = ZoneInfo("America/New_York")
_SESSION_CLOSE_HOUR = 16          # 16:00 New York


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
                df = self._load_cache(ticker, timeframe, start=start, end=end)
            if df is not None and not df.empty:
                df = self._fill_cache_tail(ticker, timeframe, df, end, use_cache)
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

    def get_latest_bar(
        self,
        ticker: str,
        timeframe: str = "1Day",
        completed_only: bool = True,
    ) -> pd.Series:
        """
        Fetch the most recent bar for `ticker` at the given interval.

        Default is 1Day: this system's features, HMM and trend rule are all
        defined on DAILY bars.  (The previous 5Min default fed intraday bars
        into the daily pipeline — every rolling feature and the SMA-200
        became a mix of daily and 5-minute data.)

        `completed_only` (daily bars only) drops a trailing bar whose session
        is still open.  The scheduled run fires 09:35 New York, when the API
        already serves TODAY's daily bar — five minutes of trading presented
        as a finished day.  Feeding that to the SMA-200, the vol estimate and
        the HMM meant the live system decided on a different quantity than
        anything ever backtested, which decides on the completed close and
        fills at the next open.  With the in-progress bar dropped, the newest
        completed bar IS yesterday's close and execution happens just after
        today's open — exactly the backtested model.

        Pass completed_only=False to get the current, still-forming bar; that
        is a price source (see get_latest_price), not a decision input.
        """
        end = datetime.now(timezone.utc)
        # 10 days: a long weekend plus a holiday can leave very few sessions,
        # and completed_only discards one of whatever comes back.
        start = end - timedelta(days=10)
        df = self._fetch_bars_with_retry(
            ticker, start.isoformat(), end.isoformat(), timeframe,
        )
        if df.empty:
            raise RuntimeError(f"No recent bar available for {ticker}.")
        if completed_only and timeframe == "1Day":
            df = self._drop_in_progress_session(df, now=end)
            if df.empty:
                raise RuntimeError(
                    f"No completed daily bar available for {ticker}."
                )
        return df.iloc[-1]

    def get_latest_price(self, ticker: str) -> float:
        """
        Most recent traded price, INCLUDING the still-forming session.

        Decisions run on completed bars, but orders fill now: sizing a target
        weight on yesterday's close would be wrong by the whole overnight gap,
        and the expected_price recorded for slippage measurement would carry
        that gap into every fill it logs.
        """
        bar = self.get_latest_bar(ticker, "1Day", completed_only=False)
        return float(bar["close"])

    @staticmethod
    def _drop_in_progress_session(
        df: pd.DataFrame, now: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Drop a trailing daily bar belonging to a session that has not closed.

        A bar dated today is final only once the exchange has closed; before
        16:00 New York it is a partial day whose "close" is simply the last
        print.  Timestamps without a timezone are taken to be exchange dates
        already, which is how the parquet cache stores them.
        """
        if df.empty:
            return df
        now_ny = (now or datetime.now(timezone.utc)).astimezone(_EXCHANGE_TZ)
        if now_ny.hour >= _SESSION_CLOSE_HOUR:
            return df           # today's session is over; its bar is final

        last_ts = pd.Timestamp(df.index[-1])
        if last_ts.tzinfo is not None:
            last_ts = last_ts.tz_convert(_EXCHANGE_TZ)
        if last_ts.date() == now_ny.date():
            return df.iloc[:-1]
        return df

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
        from alpaca.data.enums import Adjustment, DataFeed

        api_key = getattr(self._client, "api_key", "") or os.getenv("ALPACA_API_KEY", "")
        secret_key = getattr(self._client, "secret_key", "") or os.getenv("ALPACA_SECRET_KEY", "")

        data_client = getattr(self._client, "data", None) or StockHistoricalDataClient(
            api_key,
            secret_key,
        )
        tf = self._parse_timeframe(timeframe, TimeFrame, TimeFrameUnit)
        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=tf,
            start=pd.Timestamp(start).to_pydatetime(),
            end=pd.Timestamp(end).to_pydatetime() if end else None,
            feed=DataFeed.IEX,
            # Split+dividend adjusted (total-return) prices: raw prices drop
            # ~0.3-0.4% at every ex-dividend date, which both distorts trend
            # signals near the SMA and understates every return comparison
            # by the ETF's ~1.3%/yr dividend yield.
            adjustment=Adjustment.ALL,
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
        # "_adj" suffix: caches written before the switch to split+dividend
        # adjusted prices hold RAW prices and must not be served.
        return self._cache_dir / f"{ticker}_{timeframe}_adj.parquet"

    # Cached daily data may lag the requested end by up to this many calendar
    # days (weekends/holidays/settlement) before it counts as stale.
    _CACHE_END_GRACE_DAYS = 5
    # ... and may start up to this many days after the requested start
    # (listing date, source depth) before it counts as incomplete.
    _CACHE_START_GRACE_DAYS = 10

    def _load_cache(
        self,
        ticker: str,
        timeframe: str = "1Day",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Return the cached frame ONLY if it covers the requested [start, end]
        range (within a small grace window).  A stale or too-short cache
        returns None so the caller re-fetches and overwrites it.  (The cache
        previously ignored the requested range entirely — a bot restarted
        weeks later silently trained on data frozen at the last fetch.)
        """
        path = self._cache_path(ticker, timeframe)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
        except Exception as exc:            # pragma: no cover
            logger.warning("Failed to read cache %s: %s", path, exc)
            return None
        if df.empty:
            return None
        try:
            cache_first = pd.Timestamp(df.index.min()).date()
            cache_last  = pd.Timestamp(df.index.max()).date()
            if start is not None:
                want_start = pd.Timestamp(start).date()
                if (cache_first - want_start).days > self._CACHE_START_GRACE_DAYS:
                    logger.info("Cache for %s starts %s but %s requested — refetching.",
                                ticker, cache_first, want_start)
                    return None
            want_end = pd.Timestamp(end).date() if end else datetime.now(timezone.utc).date()
            if (want_end - cache_last).days > self._CACHE_END_GRACE_DAYS:
                logger.info("Cache for %s ends %s but %s requested — refetching.",
                            ticker, cache_last, want_end)
                return None
        except (TypeError, ValueError) as exc:
            logger.warning("Cache range check failed for %s: %s — refetching.", ticker, exc)
            return None
        return df

    def _fill_cache_tail(
        self,
        ticker: str,
        timeframe: str,
        df: pd.DataFrame,
        end: Optional[str],
        use_cache: bool,
    ) -> pd.DataFrame:
        """
        Fetch and merge the sessions between the cache's last bar and `end`.

        `_load_cache` accepts a cache up to `_CACHE_END_GRACE_DAYS` stale,
        and the scheduled --once process appends exactly one bar on top of
        it. Every session strictly between the two is then missing from
        SMA-200, the 63-bar vol window and the correlation selector — the
        series the live decision runs on is not the series any backtest
        evaluates on the same dates. One small tail request per ticker per
        run closes that gap; the fetch itself is not wrapped in extra
        retry/fallback here (`_fetch_bars_with_retry` already retries, and
        its caller already treats a failure as "trading paused" rather than
        silently serving the gapped series).
        """
        tail_start = pd.Timestamp(df.index.max()) - pd.Timedelta(days=1)
        tail = self._fetch_bars_with_retry(
            ticker, tail_start.date().isoformat(), end, timeframe,
        )
        if tail.empty:
            return df
        merged = pd.concat([df, tail])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        if use_cache:
            self._save_cache(ticker, timeframe, merged)
        return merged

    def _save_cache(self, ticker: str, timeframe: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(ticker, timeframe))
        except Exception as exc:                # pragma: no cover
            logger.warning("Failed to write cache for %s: %s", ticker, exc)
