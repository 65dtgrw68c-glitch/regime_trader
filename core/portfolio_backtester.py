"""Prototype portfolio backtester that computes target weights per day.

This is intentionally minimal: it reuses existing per-ticker orchestrators
for signals and uses the allocator to compute book-level targets. The
purpose is to provide a runnable harness for early multi-asset checks.
"""
from __future__ import annotations

from typing import Dict, List
import pandas as pd

from core.universe import build_views, AssetView
from core.allocator import target_weights


class PortfolioBacktester:
    """Minimal multi-asset runner.

    histories: mapping ticker -> DataFrame with 'close' column (same index)
    trend_states: mapping date -> mapping ticker -> bool (trend at date)
    """

    def __init__(self, histories: Dict[str, pd.DataFrame]):
        self.histories = histories

    def compute_daily_targets(self, date) -> Dict[str, float]:
        # build per-ticker views for that date — caller must provide
        # already-validated histories and trend flags.
        # For the prototype, derive trend as close > SMA200 when available.
        trend_states = {}
        for t, df in self.histories.items():
            if date not in df.index:
                trend_states[t] = False
                continue
            closes = df.loc[:date]["close"].dropna()
            if len(closes) >= 200:
                sma = closes.iloc[-200:].mean()
                trend_states[t] = float(closes.iloc[-1]) > sma
            else:
                trend_states[t] = False

        views: List[AssetView] = build_views(self.histories, trend_states)
        return target_weights(views)
