"""
Tests for data/market_data.py — bar completeness at the live decision point.

The scheduled run fires 09:35 New York.  At that moment the API already
serves today's DAILY bar: five minutes of trading shaped like a finished
day.  Feeding it to the SMA-200, the vol estimate and the HMM made the live
system decide on a quantity that was never backtested.

Run with:  pytest tests/test_market_data.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import market_data
from data.market_data import MarketDataFeed

NY = ZoneInfo("America/New_York")


def _daily_bars(dates, tz=NY) -> pd.DataFrame:
    """Daily OHLCV frame indexed by session timestamp."""
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=tz) for d in dates])
    n = len(idx)
    return pd.DataFrame(
        {
            "open":   [100.0 + i for i in range(n)],
            "high":   [101.0 + i for i in range(n)],
            "low":    [99.0 + i for i in range(n)],
            "close":  [100.5 + i for i in range(n)],
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def _feed(df: pd.DataFrame, tmp_path) -> MarketDataFeed:
    """A feed whose network layer is replaced by a fixed frame."""
    feed = MarketDataFeed(client=object(), cache_dir=str(tmp_path))
    feed._fetch_bars_with_retry = lambda *a, **k: df.copy()   # type: ignore[assignment]
    return feed


# ---------------------------------------------------------------------------
# 1. Which session counts as finished
# ---------------------------------------------------------------------------

class TestDropInProgressSession:

    def test_todays_bar_is_dropped_while_the_session_runs(self):
        df = _daily_bars(["2026-08-05", "2026-08-06", "2026-08-07"])
        now = datetime(2026, 8, 7, 9, 35, tzinfo=NY)     # timer fire time
        out = MarketDataFeed._drop_in_progress_session(df, now=now)
        assert list(out.index) == list(df.index[:-1])

    def test_todays_bar_is_kept_after_the_close(self):
        df = _daily_bars(["2026-08-05", "2026-08-06", "2026-08-07"])
        now = datetime(2026, 8, 7, 16, 30, tzinfo=NY)
        out = MarketDataFeed._drop_in_progress_session(df, now=now)
        assert list(out.index) == list(df.index)

    def test_yesterdays_last_bar_is_already_complete(self):
        """Nothing to drop when the API has not opened today's bar yet."""
        df = _daily_bars(["2026-08-05", "2026-08-06"])
        now = datetime(2026, 8, 7, 9, 35, tzinfo=NY)
        out = MarketDataFeed._drop_in_progress_session(df, now=now)
        assert list(out.index) == list(df.index)

    def test_utc_now_is_converted_to_exchange_time(self):
        """13:35 UTC is 09:35 in New York — still mid-session, not next day."""
        df = _daily_bars(["2026-08-06", "2026-08-07"])
        now = datetime(2026, 8, 7, 13, 35, tzinfo=timezone.utc)
        out = MarketDataFeed._drop_in_progress_session(df, now=now)
        assert list(out.index) == list(df.index[:-1])

    def test_naive_timestamps_are_read_as_exchange_dates(self):
        """The parquet cache stores dates without a timezone."""
        df = _daily_bars(["2026-08-06", "2026-08-07"])
        df.index = df.index.tz_localize(None)
        now = datetime(2026, 8, 7, 9, 35, tzinfo=NY)
        out = MarketDataFeed._drop_in_progress_session(df, now=now)
        assert len(out) == 1

    def test_empty_frame_is_returned_unchanged(self):
        empty = pd.DataFrame()
        assert MarketDataFeed._drop_in_progress_session(empty).empty


# ---------------------------------------------------------------------------
# 2. get_latest_bar / get_latest_price
# ---------------------------------------------------------------------------

class TestLatestBar:
    """`now` inside get_latest_bar is the wall clock, so these drive the branch
    through _SESSION_CLOSE_HOUR instead of freezing time: 24 means the session
    never counts as closed, 0 means it always does."""

    @staticmethod
    def _today_and_yesterday() -> pd.DataFrame:
        today = datetime.now(timezone.utc).astimezone(NY).date()
        return _daily_bars([today - timedelta(days=4), today])

    def test_in_progress_bar_is_not_a_decision_bar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 24)
        df = self._today_and_yesterday()
        bar = _feed(df, tmp_path).get_latest_bar("SPY")
        assert bar.name == df.index[0]
        assert float(bar["close"]) == pytest.approx(float(df["close"].iloc[0]))

    def test_completed_bar_is_returned_once_the_session_is_over(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 0)
        df = self._today_and_yesterday()
        assert _feed(df, tmp_path).get_latest_bar("SPY").name == df.index[-1]

    def test_completed_only_false_returns_the_forming_bar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 24)
        df = self._today_and_yesterday()
        bar = _feed(df, tmp_path).get_latest_bar("SPY", completed_only=False)
        assert bar.name == df.index[-1]

    def test_intraday_timeframes_are_untouched(self, tmp_path, monkeypatch):
        """Only DAILY bars have a session-completeness question."""
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 24)
        df = self._today_and_yesterday()
        bar = _feed(df, tmp_path).get_latest_bar("SPY", timeframe="5Min")
        assert bar.name == df.index[-1]

    def test_no_completed_bar_raises_rather_than_guessing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 24)
        today = datetime.now(timezone.utc).astimezone(NY).date()
        with pytest.raises(RuntimeError, match="completed daily bar"):
            _feed(_daily_bars([today]), tmp_path).get_latest_bar("SPY")

    def test_empty_response_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="No recent bar"):
            _feed(pd.DataFrame(), tmp_path).get_latest_bar("SPY")

    def test_latest_price_is_the_current_session(self, tmp_path, monkeypatch):
        """Orders fill now, so sizing must not use the decision bar's close."""
        monkeypatch.setattr(market_data, "_SESSION_CLOSE_HOUR", 24)
        df = self._today_and_yesterday()
        feed = _feed(df, tmp_path)
        assert feed.get_latest_price("SPY") == pytest.approx(float(df["close"].iloc[-1]))
        assert feed.get_latest_price("SPY") != pytest.approx(
            float(feed.get_latest_bar("SPY")["close"])
        )


# ---------------------------------------------------------------------------
# 3. Cache tail gap (H1)
# ---------------------------------------------------------------------------

class TestCacheTail:
    """A cache accepted up to _CACHE_END_GRACE_DAYS stale must not be served
    with a hole in it — the sessions between its last bar and `end` are
    missing from SMA-200, the vol window and the correlation selector, so
    the live decision would run on a different series than any backtest."""

    def test_stale_cache_has_its_tail_fetched_and_merged(self, tmp_path):
        feed = MarketDataFeed(client=object(), cache_dir=str(tmp_path))
        cached = _daily_bars(["2026-08-01", "2026-08-02", "2026-08-03"])
        feed._save_cache("AAA", "1Day", cached)

        tail = _daily_bars(["2026-08-04", "2026-08-05"])
        calls = []
        def _fake_fetch(ticker, start, end, timeframe):
            calls.append((ticker, start, end, timeframe))
            return tail.copy()
        feed._fetch_bars_with_retry = _fake_fetch

        result = feed.get_historical_bars(["AAA"], "2026-08-01", "2026-08-06")
        result = result.xs("AAA", level="ticker")

        assert len(calls) == 1, "only the missing tail should be fetched, not a full refetch"
        got_dates = {pd.Timestamp(ts).date() for ts in result.index}
        assert pd.Timestamp("2026-08-04").date() in got_dates
        assert pd.Timestamp("2026-08-05").date() in got_dates

        # The merged frame is persisted so the next run's cache is complete.
        reloaded = pd.read_parquet(feed._cache_path("AAA", "1Day"))
        assert len(reloaded) == 5

    def test_empty_tail_leaves_cache_untouched(self, tmp_path):
        feed = MarketDataFeed(client=object(), cache_dir=str(tmp_path))
        cached = _daily_bars(["2026-08-01", "2026-08-02"])
        feed._save_cache("AAA", "1Day", cached)
        feed._fetch_bars_with_retry = lambda *a, **k: pd.DataFrame()

        result = feed.get_historical_bars(["AAA"], "2026-08-01", "2026-08-03")
        result = result.xs("AAA", level="ticker")
        assert len(result) == 2
