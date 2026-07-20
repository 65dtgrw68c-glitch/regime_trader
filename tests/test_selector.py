from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.selector import select_decorrelated_views
from core.universe import AssetView
from settings import config


def _hist_from_returns(returns):
    idx = pd.bdate_range("2024-01-01", periods=len(returns), freq="B")
    close = 100.0 * (1.0 + pd.Series(returns, index=idx)).cumprod()
    return pd.DataFrame({"close": close}, index=idx)


def test_selector_removes_highly_correlated_duplicate(monkeypatch):
    monkeypatch.setitem(config.RISK, "enable_correlation_check", True)
    monkeypatch.setitem(config.RISK, "max_position_correlation", 0.80)
    monkeypatch.setitem(config.RISK, "correlation_lookback", 60)

    base = [0.001, -0.002, 0.003, 0.0015, -0.0005] * 20

    histories = {
        "SPY": _hist_from_returns(base),
        "QQQ": _hist_from_returns([x * 1.01 for x in base]),
    }

    views = [
        AssetView("SPY", "equity", True, 0.10),
        AssetView("QQQ", "equity", True, 0.20),
    ]

    selected = select_decorrelated_views(views, histories)

    assert [v.ticker for v in selected] == ["SPY"]


def test_selector_keeps_low_correlation_diversifier(monkeypatch):
    monkeypatch.setitem(config.RISK, "enable_correlation_check", True)
    monkeypatch.setitem(config.RISK, "max_position_correlation", 0.80)
    monkeypatch.setitem(config.RISK, "correlation_lookback", 60)

    spy_returns = [0.001, -0.002, 0.003, 0.0015, -0.0005] * 20
    gld_returns = [0.002, 0.001, -0.0015, 0.0002, -0.0007, 0.0018, -0.0003, -0.0011, 0.0009, 0.0004] * 10

    histories = {
        "SPY": _hist_from_returns(spy_returns),
        "GLD": _hist_from_returns(gld_returns),
    }

    views = [
        AssetView("SPY", "equity", True, 0.10),
        AssetView("GLD", "gold", True, 0.12),
    ]

    selected = select_decorrelated_views(views, histories)

    assert {v.ticker for v in selected} == {"SPY", "GLD"}


def test_selector_is_disabled_when_config_flag_false(monkeypatch):
    monkeypatch.setitem(config.RISK, "enable_correlation_check", False)

    base = [0.001, -0.002, 0.003, 0.0015, -0.0005] * 20

    histories = {
        "SPY": _hist_from_returns(base),
        "QQQ": _hist_from_returns([x * 1.01 for x in base]),
    }

    views = [
        AssetView("SPY", "equity", True, 0.10),
        AssetView("QQQ", "equity", True, 0.20),
    ]

    selected = select_decorrelated_views(views, histories)

    assert {v.ticker for v in selected} == {"SPY", "QQQ"}
