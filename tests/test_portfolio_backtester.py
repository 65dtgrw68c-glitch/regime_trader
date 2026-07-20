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

def test_tradable_universe_contains_validated_diversifiers():
    from core.universe import tradable_universe

    universe = tradable_universe()

    assert "SPY" in universe
    assert "QQQ" in universe
    assert "GLD" in universe
    assert "IEF" in universe

    # DBC bleibt optional und soll aktuell noch nicht live gehandelt werden.
    assert "DBC" not in universe
