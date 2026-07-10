"""
Tests for core/backtester.py and core/performance.py.

Run with:  pytest tests/test_backtester.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtester import Backtester, BacktestResult, WalkForwardSplit
from core.performance import (
    PerformanceAnalyser,
    annualised_return,
    max_drawdown,
    sharpe_ratio,
    total_return,
    win_rate,
    num_trades,
    _trade_pnls,
)


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 700, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    vol = np.select(
        [np.arange(n) < n // 3, np.arange(n) < 2 * n // 3],
        [0.006, 0.018], default=0.012,
    )
    log_ret = rng.normal(0.0003, vol, n)
    close = 100 * np.exp(np.cumsum(log_ret))
    wig = rng.uniform(0.001, 0.004, n)
    return pd.DataFrame({
        "open":   close * (1 + rng.normal(0, 0.001, n)),
        "high":   close * (1 + wig),
        "low":    close * (1 - wig),
        "close":  close,
        "volume": rng.integers(1_000_000, 4_000_000, n).astype(float),
    }, index=dates)


@pytest.fixture
def small_backtester() -> Backtester:
    # Small windows so the test runs fast but still exercises >=2 splits.
    return Backtester(
        ticker="TEST",
        train_window=120,
        test_window=60,
        random_seed=7,
    )


# ---------------------------------------------------------------------------
# 1. Walk-forward split correctness
# ---------------------------------------------------------------------------

class TestWalkForwardSplits:

    def test_splits_have_correct_window_sizes(self, small_backtester):
        splits = small_backtester.walk_forward_splits(600)
        for s in splits:
            assert s.train_end - s.train_start == 120
            assert s.test_start == s.train_end          # OOS begins after train
            assert s.test_end - s.test_start <= 60

    def test_oos_windows_no_overlap_no_gap(self, small_backtester):
        splits = small_backtester.walk_forward_splits(600)
        # OOS windows should tile contiguously
        for prev, nxt in zip(splits[:-1], splits[1:]):
            assert nxt.test_start == prev.test_end, "gap or overlap between OOS windows"

    def test_first_split_starts_after_training(self, small_backtester):
        splits = small_backtester.walk_forward_splits(600)
        assert splits[0].train_start == 0
        assert splits[0].test_start == 120

    def test_no_splits_when_insufficient_data(self, small_backtester):
        # Fewer bars than one training window → no splits
        assert small_backtester.walk_forward_splits(100) == []

    def test_splits_cover_full_oos_range(self, small_backtester):
        n = 600
        splits = small_backtester.walk_forward_splits(n)
        covered = set()
        for s in splits:
            covered.update(range(s.test_start, s.test_end))
        # Everything from first test_start to the last bar is covered
        expected = set(range(splits[0].test_start, splits[-1].test_end))
        assert covered == expected


# ---------------------------------------------------------------------------
# 2. No look-ahead bias
# ---------------------------------------------------------------------------

class TestNoLookAhead:

    def test_train_window_strictly_before_test(self, small_backtester):
        """Every training window must end exactly at (or before) its OOS start."""
        splits = small_backtester.walk_forward_splits(600)
        for s in splits:
            assert s.train_end <= s.test_start, "training data leaks into OOS window"

    def test_future_data_does_not_change_past_oos_returns(self):
        """
        Corrupting bars AFTER a cutoff must not alter the OOS returns produced
        for bars BEFORE that cutoff.  This is the core look-ahead guard.
        """
        data = _make_ohlcv(420, seed=3)
        bt = Backtester(ticker="T", train_window=120, test_window=60, random_seed=3)

        res_clean = bt.run(data)
        cutoff = res_clean.returns.index[100]   # an early OOS timestamp

        # Corrupt everything strictly AFTER the cutoff timestamp
        corrupted = data.copy()
        mask = corrupted.index > cutoff
        corrupted.loc[mask, ["open", "high", "low", "close"]] *= 1.5

        bt2 = Backtester(ticker="T", train_window=120, test_window=60, random_seed=3)
        res_corrupt = bt2.run(corrupted)

        # Compare the OOS returns up to and including the cutoff
        clean_head   = res_clean.returns.loc[:cutoff]
        corrupt_head = res_corrupt.returns.loc[:cutoff]
        common = clean_head.index.intersection(corrupt_head.index)
        np.testing.assert_allclose(
            clean_head.loc[common].values,
            corrupt_head.loc[common].values,
            rtol=1e-9, atol=1e-9,
            err_msg="Future data leaked into past OOS returns (look-ahead bias).",
        )

    def test_reproducibility(self):
        data = _make_ohlcv(420, seed=11)
        bt1 = Backtester(ticker="T", train_window=120, test_window=60, random_seed=5)
        bt2 = Backtester(ticker="T", train_window=120, test_window=60, random_seed=5)
        r1 = bt1.run(data).returns
        r2 = bt2.run(data).returns
        pd.testing.assert_series_equal(r1, r2)


# ---------------------------------------------------------------------------
# 3. Backtest run produces a coherent result
# ---------------------------------------------------------------------------

class TestBacktestRun:

    def test_run_returns_result(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        assert isinstance(res, BacktestResult)

    def test_returns_length_matches_oos_bars(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        total_oos = sum(s.test_end - s.test_start for s in res.splits)
        assert len(res.returns) == total_oos

    def test_equity_curve_aligned_with_returns(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        assert len(res.equity_curve) == len(res.returns)

    def test_regime_and_confidence_series_aligned(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        assert len(res.regime_labels) == len(res.returns)
        assert len(res.confidence) == len(res.returns)

    def test_run_raises_on_too_little_data(self, small_backtester):
        with pytest.raises(ValueError):
            small_backtester.run(_make_ohlcv(100))


# ---------------------------------------------------------------------------
# 4. Performance metric calculations
# ---------------------------------------------------------------------------

class TestPerformanceMetrics:

    def test_total_return_simple(self):
        r = pd.Series([0.10, -0.05, 0.02])
        expected = (1.10 * 0.95 * 1.02) - 1.0
        assert total_return(r) == pytest.approx(expected)

    def test_total_return_empty(self):
        assert total_return(pd.Series([], dtype=float)) == 0.0

    def test_sharpe_zero_for_constant(self):
        r = pd.Series([0.001] * 50)        # zero variance
        assert sharpe_ratio(r) == 0.0

    def test_sharpe_positive_for_uptrend(self):
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.001, 0.005, 252))
        assert sharpe_ratio(r) > 0

    def test_max_drawdown_known_path(self):
        # +10% then -50% → equity 1.1 then 0.55; drawdown = (0.55-1.1)/1.1
        r = pd.Series([0.10, -0.50])
        assert max_drawdown(r) == pytest.approx((0.55 - 1.1) / 1.1)

    def test_max_drawdown_monotonic_up_is_zero(self):
        r = pd.Series([0.01, 0.01, 0.01])
        assert max_drawdown(r) == pytest.approx(0.0)

    def test_annualised_return_one_year(self):
        # 252 bars each +0 → 0% annualised
        r = pd.Series([0.0] * 252)
        assert annualised_return(r) == pytest.approx(0.0)

    def test_win_rate_from_pnl_column(self):
        log = pd.DataFrame({"pnl": [10.0, -5.0, 3.0, -1.0]})
        assert win_rate(log) == pytest.approx(0.5)

    def test_win_rate_from_fifo_fills(self):
        # Buy 10@100, sell 10@110 → +profit (1 winning trade)
        log = pd.DataFrame({
            "ticker":     ["X", "X"],
            "side":       ["BUY", "SELL"],
            "qty":        [10, 10],
            "fill_price": [100.0, 110.0],
        })
        assert win_rate(log) == pytest.approx(1.0)

    def test_num_trades_counts_fills(self):
        log = pd.DataFrame({"pnl": [1.0, 2.0, 3.0]})
        assert num_trades(log) == 3

    def test_trade_pnls_fifo_pairing(self):
        log = pd.DataFrame({
            "ticker":     ["X", "X"],
            "side":       ["BUY", "SELL"],
            "qty":        [10, 10],
            "fill_price": [100.0, 105.0],
        })
        pnls = _trade_pnls(log)
        assert pnls == pytest.approx([50.0])   # (105-100)*10


# ---------------------------------------------------------------------------
# 5. Benchmark comparison logic
# ---------------------------------------------------------------------------

class TestBenchmarks:

    def test_three_benchmarks_present(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        assert set(res.benchmark_returns.keys()) == {
            "buy_and_hold", "sma_200", "random_entry"
        }

    def test_benchmarks_aligned_to_oos(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        for series in res.benchmark_returns.values():
            assert len(series) == len(res.returns)

    def test_buy_and_hold_matches_price_change(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        bh = res.benchmark_returns["buy_and_hold"]
        oos_close = data["close"].loc[res.returns.index]
        expected = oos_close.pct_change().fillna(0.0)
        np.testing.assert_allclose(bh.values, expected.values, rtol=1e-9)

    def test_vs_benchmark_table_has_strategy_and_benchmarks(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        pa = PerformanceAnalyser.from_backtest_result(res)
        table = pa.vs_benchmark()
        names = set(table["name"])
        assert "strategy" in names
        assert "buy_and_hold" in names


# ---------------------------------------------------------------------------
# 6. Regime breakdown & confidence buckets
# ---------------------------------------------------------------------------

class TestRegimeAndConfidence:

    def test_regime_breakdown_runs(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        pa = PerformanceAnalyser.from_backtest_result(res)
        table = pa.regime_breakdown()
        assert "regime" in table.columns
        assert "sharpe_ratio" in table.columns

    def test_confidence_buckets_three_levels(self, small_backtester):
        data = _make_ohlcv(420)
        res = small_backtester.run(data)
        pa = PerformanceAnalyser.from_backtest_result(res)
        buckets = pa.confidence_buckets()
        assert list(buckets["bucket"]) == ["low", "medium", "high"]


# ---------------------------------------------------------------------------
# 7. Stress injection logic
# ---------------------------------------------------------------------------

class TestStressInjection:

    def test_injection_creates_price_drops(self, small_backtester):
        data = _make_ohlcv(420, seed=1)
        stressed = small_backtester.inject_stress_events(data, n_events=3)
        assert "stress_bars" in stressed.attrs
        assert len(stressed.attrs["stress_bars"]) == 3

    def test_injected_drop_magnitude(self, small_backtester):
        data = _make_ohlcv(420, seed=2)
        stressed = small_backtester.inject_stress_events(
            data, n_events=1, min_drop=0.10, max_drop=0.15
        )
        bar = stressed.attrs["stress_bars"][0]
        # The single-bar return at the crash should be a large negative move
        ret_at_crash = (stressed["close"].iloc[bar] / data["close"].iloc[bar]) - 1.0
        # Persistent scaling: crash bar is 10–15% below the un-stressed price
        assert -0.15 - 1e-6 <= ret_at_crash <= -0.10 + 1e-6

    def test_injection_preserves_length(self, small_backtester):
        data = _make_ohlcv(420)
        stressed = small_backtester.inject_stress_events(data, n_events=3)
        assert len(stressed) == len(data)

    def test_backtest_runs_with_stress(self):
        data = _make_ohlcv(420, seed=9)
        bt = Backtester(ticker="T", train_window=120, test_window=60, random_seed=9)
        res = bt.run(data, inject_stress=True)
        assert res.metadata["stress_injected"] is True
        assert len(res.returns) > 0

    def test_stress_increases_drawdown(self):
        """Injected crashes should not improve (reduce) max drawdown."""
        data = _make_ohlcv(420, seed=4)
        bt_clean  = Backtester(ticker="T", train_window=120, test_window=60, random_seed=4)
        bt_stress = Backtester(ticker="T", train_window=120, test_window=60, random_seed=4)
        clean  = bt_clean.run(data, inject_stress=False)
        stress = bt_stress.run(data, inject_stress=True)
        dd_clean  = max_drawdown(clean.returns)
        dd_stress = max_drawdown(stress.returns)
        # More negative (or equal) drawdown under stress
        assert dd_stress <= dd_clean + 1e-9


# ---------------------------------------------------------------------------
# 8. Rebalance gating & strategy overrides
# ---------------------------------------------------------------------------

class TestTradeGatingAndOverrides:

    def test_rebalance_gating_reduces_turnover(self):
        """
        Gated execution (drift / regime-change / staleness triggers) must
        not trade MORE often than a deliberately churny configuration that
        rebalances every bar.
        """
        data = _make_ohlcv(420, seed=6)
        bt_gated = Backtester(
            ticker="T", train_window=120, test_window=60, random_seed=6,
        )
        bt_churn = Backtester(
            ticker="T", train_window=120, test_window=60, random_seed=6,
            strategy_overrides={"rebalance_max_bars": 1, "drift_threshold": 0.0},
        )
        n_gated = len(bt_gated.run(data).trade_log)
        n_churn = len(bt_churn.run(data).trade_log)
        assert n_gated <= n_churn

    def test_trend_filter_flag_recorded_in_metadata(self):
        data = _make_ohlcv(420, seed=8)
        bt = Backtester(
            ticker="T", train_window=120, test_window=60, random_seed=8,
            use_trend_filter=False,
        )
        res = bt.run(data)
        assert res.metadata["trend_filter"] is False

    def test_strategy_overrides_recorded_in_metadata(self):
        data = _make_ohlcv(420, seed=8)
        bt = Backtester(
            ticker="T", train_window=120, test_window=60, random_seed=8,
            strategy_overrides={"vol_target": 0.12},
        )
        res = bt.run(data)
        assert res.metadata["strategy_overrides"] == {"vol_target": 0.12}


# ---------------------------------------------------------------------------
# 9. Costs — slippage must hit the equity curve
# ---------------------------------------------------------------------------

class TestSlippageCharged:

    def test_slippage_reduces_equity(self):
        """
        Identical run with higher slippage must end with a lower total
        return.  (Slippage used to be applied to the logged fill price only
        and never charged against equity.)
        """
        data = _make_ohlcv(420, seed=6)
        common = dict(
            ticker="T", train_window=120, test_window=60, random_seed=6,
            commission=0.0,
            strategy_overrides={"rebalance_max_bars": 1, "drift_threshold": 0.0},
        )
        res_free = Backtester(slippage=0.0, **common).run(data)
        res_slip = Backtester(slippage=0.01, **common).run(data)
        assert len(res_slip.trade_log) > 0
        assert total_return(res_slip.returns) < total_return(res_free.returns)


# ---------------------------------------------------------------------------
# 10. Execution timing — decisions fill at the NEXT bar's open
# ---------------------------------------------------------------------------

class TestNextOpenExecution:

    def test_fills_occur_at_the_bars_open(self):
        """
        Every logged fill must price off the OPEN of the bar it executes on
        (± slippage).  A fill at the signal bar's close is impossible live:
        the daily bar only completes at the close.
        """
        data = _make_ohlcv(420, seed=6)
        slip = 0.001
        bt = Backtester(
            ticker="T", train_window=120, test_window=60, random_seed=6,
            slippage=slip, commission=0.0,
        )
        res = bt.run(data)
        tl = res.trade_log
        assert len(tl) > 0
        for _, t in tl.iterrows():
            o = float(data["open"].loc[t["timestamp"]])
            expected = o * (1 + slip) if t["side"] == "BUY" else o * (1 - slip)
            assert t["fill_price"] == pytest.approx(expected), (
                f"fill at {t['timestamp']} priced off something other than "
                f"that bar's open"
            )

    def test_no_fill_on_first_oos_bar(self):
        """The first OOS bar can only DECIDE; the earliest fill is bar 2."""
        data = _make_ohlcv(420, seed=6)
        bt = Backtester(ticker="T", train_window=120, test_window=60,
                        random_seed=6)
        res = bt.run(data)
        first_oos_ts = res.returns.index[0]
        tl = res.trade_log
        if len(tl):
            assert (tl["timestamp"] > first_oos_ts).all()


# ---------------------------------------------------------------------------
# 11. Cash yield — idle cash must earn the configured rate
# ---------------------------------------------------------------------------

class TestCashYield:

    def test_cash_yield_credits_idle_cash(self):
        """
        With the exposure cap at 0.5 the book is always >= 50% cash, so a
        positive cash yield must strictly raise the total return without
        changing any trading decision (weights are equity-proportional).
        """
        data = _make_ohlcv(420, seed=8)
        common = dict(ticker="T", train_window=120, test_window=60,
                      random_seed=8, slippage=0.0, commission=0.0)
        res0 = Backtester(cash_yield_annual=0.0, **common).run(data)
        res5 = Backtester(cash_yield_annual=0.05, **common).run(data)
        assert total_return(res5.returns) > total_return(res0.returns)
        assert len(res5.trade_log) == len(res0.trade_log)

    def test_benchmarks_receive_cash_yield_on_idle_bars(self):
        data = _make_ohlcv(420, seed=8)
        common = dict(ticker="T", train_window=120, test_window=60,
                      random_seed=8)
        b0 = Backtester(cash_yield_annual=0.0, **common).run(data).benchmark_returns
        b5 = Backtester(cash_yield_annual=0.05, **common).run(data).benchmark_returns
        # sma_200 spends some OOS bars in cash for this seed → yield helps.
        assert total_return(b5["sma_200"]) >= total_return(b0["sma_200"])
        # buy & hold is always invested → identical either way.
        assert total_return(b5["buy_and_hold"]) == pytest.approx(
            total_return(b0["buy_and_hold"])
        )


# ---------------------------------------------------------------------------
# 12. Benchmark execution timing + T-bill yield series
# ---------------------------------------------------------------------------

class TestBenchmarkTimingAndYieldSeries:

    def test_benchmark_timing_matches_close_fill_when_gapless(self):
        """
        Benchmarks now fill at the next open like the strategy.  On data
        with NO overnight gaps (open_i == close_{i-1}) the next-open and
        close-fill formulations are mathematically identical — a direct
        check that the timing decomposition is wired correctly.
        """
        data = _make_ohlcv(420, seed=9)
        data["open"] = data["close"].shift(1).fillna(data["close"].iloc[0])
        bt = Backtester(ticker="T", train_window=120, test_window=60,
                        random_seed=9, cash_yield_annual=0.0)
        res = bt.run(data)
        sma_bench = res.benchmark_returns["sma_200"]
        close = data["close"]
        invested = (close > close.rolling(200).mean()).shift(1).fillna(False)
        ref = (close.pct_change().fillna(0.0) * invested.astype(float))
        ref = ref.loc[sma_bench.index]
        assert np.allclose(sma_bench.values, ref.values)

    def test_cash_yield_series_equivalent_to_flat_rate(self):
        """A constant yield series must reproduce the flat-rate path exactly."""
        data = _make_ohlcv(420, seed=8)
        ser = pd.Series(0.05, index=data.index)
        common = dict(ticker="T", train_window=120, test_window=60,
                      random_seed=8)
        res_flat = Backtester(cash_yield_annual=0.05, **common).run(data)
        res_ser = Backtester(cash_yield_annual=0.0, cash_yield_series=ser,
                             **common).run(data)
        assert np.allclose(res_flat.returns.values, res_ser.returns.values)

    def test_yield_series_gaps_are_forward_filled(self):
        """Sparse yield observations (weekly) must still cover every bar."""
        data = _make_ohlcv(420, seed=8)
        sparse = pd.Series(0.05, index=data.index[::5])   # every 5th bar only
        bt = Backtester(ticker="T", train_window=120, test_window=60,
                        random_seed=8, cash_yield_annual=0.0,
                        cash_yield_series=sparse)
        daily = bt._build_daily_yield(data.index)
        assert (daily.iloc[1:] > 0).all()                 # ffilled everywhere
