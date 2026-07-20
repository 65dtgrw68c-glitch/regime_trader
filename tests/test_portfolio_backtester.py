from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtester import PortfolioBacktester


def _make_data(n: int = 240) -> pd.DataFrame:
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


def test_portfolio_backtester_builds_weighted_returns():
    bt = PortfolioBacktester(tickers=["AAA", "BBB"], weights=[0.5, 0.5], train_window=60, test_window=30)
    result = bt.run({"AAA": _make_data(240), "BBB": _make_data(240)})

    assert result.returns is not None
    assert len(result.returns) > 0
    assert result.equity_curve.iloc[-1] >= result.initial_capital * 0.9
