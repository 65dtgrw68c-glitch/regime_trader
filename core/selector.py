"""Portfolio selection helpers.

The selector removes highly correlated duplicate bets before the allocator
turns views into target weights. This is especially important for SPY/QQQ:
both can be valid trend assets, but when their rolling correlation is high
they represent nearly the same equity risk.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from core.universe import AssetView
from settings import config


def _asset_returns(
    histories: Dict[str, pd.DataFrame],
    ticker: str,
    lookback: int,
) -> Optional[pd.Series]:
    df = histories.get(ticker)
    if df is None or df.empty or "close" not in df:
        return None

    closes = pd.Series(df["close"]).dropna()
    if len(closes) < max(3, lookback // 2):
        return None

    return closes.pct_change().dropna().tail(lookback)


def _abs_corr(a: pd.Series, b: pd.Series) -> Optional[float]:
    joined = pd.concat([a, b], axis=1).dropna()
    if len(joined) < 3:
        return None

    corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
    if pd.isna(corr):
        return None

    return abs(float(corr))


def select_decorrelated_views(
    views: List[AssetView],
    histories: Dict[str, pd.DataFrame],
    max_correlation: Optional[float] = None,
    lookback: Optional[int] = None,
) -> List[AssetView]:
    """Return trend-valid views after removing highly correlated duplicates."""
    if not config.RISK.get("enable_correlation_check", False):
        return views

    threshold = float(
        max_correlation
        if max_correlation is not None
        else config.RISK.get("max_position_correlation", 0.80)
    )
    window = int(
        lookback
        if lookback is not None
        else config.RISK.get("correlation_lookback", 60)
    )

    eligible = [
        v
        for v in views
        if v.in_trend and v.realised_vol is not None and not pd.isna(v.realised_vol)
    ]

    ranked = sorted(
        eligible,
        key=lambda v: (
            float(v.realised_vol),
            v.ticker,
        ),
    )

    selected: List[AssetView] = []
    selected_returns: Dict[str, pd.Series] = {}

    for candidate in ranked:
        cand_returns = _asset_returns(histories, candidate.ticker, window)

        reject = False
        if cand_returns is not None:
            for chosen in selected:
                chosen_returns = selected_returns.get(chosen.ticker)
                if chosen_returns is None:
                    continue

                corr = _abs_corr(cand_returns, chosen_returns)
                if corr is not None and corr > threshold:
                    reject = True
                    break

        if reject:
            continue

        selected.append(candidate)
        if cand_returns is not None:
            selected_returns[candidate.ticker] = cand_returns

    return selected
