from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.portfolio_backtester import PortfolioBacktester
from settings import config


def _make_data(n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
    close = 100.0 * (1.0005 ** pd.Series(range(n), index=idx))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def test_portfolio_backtester_builds_dynamic_weighted_returns():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    result = bt.run()

    assert result.returns is not None
    assert len(result.returns) > 0
    assert result.equity_curve.iloc[-1] >= result.initial_capital * 0.9
    assert not result.weights.empty

    gross_cap = float(config.RISK.get("gross_cap", 1.0))
    gross_exposure = result.weights.abs().sum(axis=1)

    assert (gross_exposure <= gross_cap + 1e-9).all()


def test_portfolio_backtester_records_turnover_series():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    result = bt.run()

    assert result.turnover is not None
    assert len(result.turnover) == len(result.returns)
    assert (result.turnover >= 0.0).all()
    assert "total_turnover" in result.metadata
    assert "average_turnover" in result.metadata
    assert result.metadata["turnover_convention"] == "sum_abs_weight_change"


def test_portfolio_turnover_is_zero_when_weights_unchanged():
    previous_weights = {"SPY": 0.6, "GLD": 0.4}
    current_weights = {"SPY": 0.6, "GLD": 0.4}

    turnover = PortfolioBacktester._calculate_turnover(previous_weights, current_weights)

    assert turnover == pytest.approx(0.0)


def test_portfolio_turnover_increases_when_weights_change():
    previous_weights = {"SPY": 1.0}
    current_weights = {"QQQ": 1.0}

    turnover = PortfolioBacktester._calculate_turnover(previous_weights, current_weights)

    assert turnover == pytest.approx(2.0)
    
def test_zero_transaction_cost_matches_default_behavior():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    default_bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    zero_cost_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        transaction_cost_bps=0.0,
    )

    default_result = default_bt.run()
    zero_cost_result = zero_cost_bt.run()

    pd.testing.assert_series_equal(default_result.returns, zero_cost_result.returns)


def test_portfolio_transaction_costs_reduce_returns():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    no_cost_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        transaction_cost_bps=0.0,
    )
    cost_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        transaction_cost_bps=25.0,
    )

    no_cost_result = no_cost_bt.run()
    cost_result = cost_bt.run()

    assert cost_result.metadata["transaction_cost_bps"] == pytest.approx(25.0)
    assert cost_result.metadata["transaction_cost_model"] == "turnover_times_bps"
    assert cost_result.equity_curve.iloc[-1] <= no_cost_result.equity_curve.iloc[-1]



def test_zero_slippage_matches_default_behavior():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    default_bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    zero_slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=0.0,
    )

    default_result = default_bt.run()
    zero_slippage_result = zero_slippage_bt.run()

    pd.testing.assert_series_equal(default_result.returns, zero_slippage_result.returns)


def test_portfolio_slippage_reduces_returns():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    no_slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=0.0,
    )
    slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=25.0,
    )

    no_slippage_result = no_slippage_bt.run()
    slippage_result = slippage_bt.run()

    assert slippage_result.metadata["slippage_bps"] == pytest.approx(25.0)
    assert slippage_result.metadata["slippage_model"] == "turnover_times_bps"
    assert slippage_result.equity_curve.iloc[-1] <= no_slippage_result.equity_curve.iloc[-1]


class _FullInvestedPortfolioBacktester(PortfolioBacktester):
    """Test helper: keep exactly 100% invested in SPY."""

    def compute_daily_targets(self, date):
        return {"SPY": 1.0}


def _make_gap_data(n: int = 201) -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 121.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        index=idx,
    )

    # First tradable test bar after the 200-bar warmup:
    # close-to-close return: 121 / 100 - 1 = 21%
    # next-open return:      121 / 110 - 1 = 10%
    df.iloc[200, df.columns.get_loc("open")] = 110.0
    df.iloc[200, df.columns.get_loc("close")] = 121.0
    return df


def test_portfolio_next_open_execution_ignores_overnight_gap():
    data = _make_gap_data(201)
    histories = {"SPY": data}
    end_date = data.index[200]

    bt = _FullInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        execution_model="next_open",
    )

    result = bt.run(end_date=end_date)

    assert result.metadata["execution_model"] == "next_open"
    assert len(result.returns) == 1
    assert result.returns.iloc[0] == pytest.approx(0.10)


def test_portfolio_close_to_close_execution_kept_for_comparison():
    data = _make_gap_data(201)
    histories = {"SPY": data}
    end_date = data.index[200]

    bt = _FullInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        execution_model="close_to_close",
    )

    result = bt.run(end_date=end_date)

    assert result.metadata["execution_model"] == "close_to_close"
    assert len(result.returns) == 1
    assert result.returns.iloc[0] == pytest.approx(0.21)


def test_portfolio_invalid_execution_model_rejected():
    with pytest.raises(ValueError):
        PortfolioBacktester(histories={}, execution_model="same_close")

class _HalfInvestedPortfolioBacktester(PortfolioBacktester):
    """Test helper: keep exactly 50% invested and 50% cash."""

    def compute_daily_targets(self, date):
        return {"SPY": 0.5}


def test_portfolio_cash_yield_zero_matches_default_behavior():
    histories = {
        "SPY": _make_data(260),
    }

    default_bt = _HalfInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
    )
    zero_yield_bt = _HalfInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        cash_yield_annual=0.0,
    )

    default_result = default_bt.run()
    zero_yield_result = zero_yield_bt.run()

    pd.testing.assert_series_equal(default_result.returns, zero_yield_result.returns)


def test_portfolio_cash_yield_credits_idle_cash():
    histories = {
        "SPY": _make_data(260),
    }

    no_yield_bt = _HalfInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        cash_yield_annual=0.0,
    )
    yield_bt = _HalfInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        cash_yield_annual=0.05,
    )

    no_yield_result = no_yield_bt.run()
    yield_result = yield_bt.run()

    assert yield_result.metadata["cash_yield_annual"] == pytest.approx(0.05)
    assert yield_result.metadata["cash_yield_model"] == "annual_rate_divided_by_252_trading_days"
    assert yield_result.equity_curve.iloc[-1] > no_yield_result.equity_curve.iloc[-1]


def test_portfolio_backtester_matches_live_target_weight_path():
    from core.universe import build_views
    from core.selector import select_decorrelated_views
    from core.allocator import target_weights
    from core.regime_strategies import is_trend_confirmed

    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }
    date = histories["SPY"].index[220]

    bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    backtest_weights = bt.compute_daily_targets(date)

    sliced_histories = {
        ticker: df.loc[:date].copy()
        for ticker, df in histories.items()
    }
    trend_states = {
        ticker: is_trend_confirmed(hist["close"])
        for ticker, hist in sliced_histories.items()
    }

    views = build_views(sliced_histories, trend_states)
    selected_views = select_decorrelated_views(views, sliced_histories)
    live_like_weights = target_weights(selected_views)

    assert set(backtest_weights) == set(live_like_weights)
    for ticker, weight in live_like_weights.items():
        assert backtest_weights[ticker] == pytest.approx(weight)

def test_tradable_universe_contains_validated_diversifiers():
    from core.universe import tradable_universe

    universe = tradable_universe()

    assert "SPY" in universe
    assert "QQQ" in universe
    assert "GLD" in universe
    assert "IEF" in universe

    # DBC bleibt optional und soll aktuell noch nicht live gehandelt werden.
    assert "DBC" not in universe
