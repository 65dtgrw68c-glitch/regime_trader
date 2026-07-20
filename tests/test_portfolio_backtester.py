from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

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


def test_tradable_universe_contains_validated_diversifiers():
    from core.universe import tradable_universe

    universe = tradable_universe()

    assert "SPY" in universe
    assert "QQQ" in universe
    assert "GLD" in universe
    assert "IEF" in universe

    # DBC bleibt optional und soll aktuell noch nicht live gehandelt werden.
    assert "DBC" not in universe
