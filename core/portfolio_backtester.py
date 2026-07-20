"""Dynamic multi-asset portfolio backtester.

This backtester uses the validated universe, trend views and allocator to
compute daily target weights, then applies yesterday's target weights to the
next close-to-close return. It is intentionally lightweight but it produces a
real portfolio return series, equity curve, weight history and turnover series.

Current execution model:
- decision at T-1 close
- portfolio return from T-1 close to T close

This is a close-to-close approximation. The single-asset backtester has the
more realistic next-open execution model; portfolio next-open execution remains
a separate future hardening step.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from core.universe import build_views, AssetView
from core.allocator import target_weights
from core.selector import select_decorrelated_views


@dataclass
class PortfolioBacktestResult:
    initial_capital: float
    returns: pd.Series
    equity_curve: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    metadata: dict


class PortfolioBacktester:
    """Dynamic multi-asset runner.

    histories:
        Mapping ticker -> DataFrame with at least a 'close' column.

    The runner:
    1. derives SMA-200 trend per asset,
    2. builds AssetView objects only for validated assets,
    3. calls the allocator for class-budgeted inverse-vol weights,
    4. applies target weights to the next bar's close-to-close returns,
    5. records daily portfolio turnover as sum(abs(new_weight - old_weight)).
    """

    def __init__(
        self,
        histories: Dict[str, pd.DataFrame],
        initial_capital: float = 100_000.0,
        transaction_cost_bps: float = 0.0,
    ):
        self.histories = histories
        self.initial_capital = float(initial_capital)
        self.transaction_cost_bps = float(transaction_cost_bps)

    def _common_index(self) -> pd.DatetimeIndex:
        indexes = []
        for df in self.histories.values():
            if df is not None and not df.empty:
                indexes.append(pd.DatetimeIndex(df.index))

        if not indexes:
            return pd.DatetimeIndex([])

        common = indexes[0]
        for idx in indexes[1:]:
            common = common.intersection(idx)

        return common.sort_values()

    @staticmethod
    def _calculate_turnover(
        previous_weights: Dict[str, float],
        current_weights: Dict[str, float],
    ) -> float:
        """Return one-period portfolio turnover.

        Turnover is measured as the total absolute change in target weights.
        Example:
        - 100% SPY -> 100% SPY = 0.0
        - 100% SPY -> 50% SPY / 50% GLD = 1.0
        - 100% SPY -> 100% QQQ = 2.0

        This convention reflects total traded notional as a fraction of equity:
        selling 100% SPY and buying 100% QQQ means 200% traded notional.
        """
        keys = set(previous_weights) | set(current_weights)
        turnover = 0.0

        for key in keys:
            old = float(previous_weights.get(key, 0.0))
            new = float(current_weights.get(key, 0.0))
            turnover += abs(new - old)

        return float(turnover)

    def compute_daily_targets(self, date) -> Dict[str, float]:
        """Compute target weights using only data available up to `date`."""
        trend_states = {}
        sliced_histories: Dict[str, pd.DataFrame] = {}

        for ticker, df in self.histories.items():
            if df is None or df.empty or date not in df.index:
                trend_states[ticker] = False
                continue

            hist_to_date = df.loc[:date].copy()
            sliced_histories[ticker] = hist_to_date

            closes = hist_to_date["close"].dropna()
            if len(closes) >= 200:
                sma = closes.iloc[-200:].mean()
                trend_states[ticker] = float(closes.iloc[-1]) > float(sma)
            else:
                trend_states[ticker] = False

        views: List[AssetView] = build_views(sliced_histories, trend_states)
        views = select_decorrelated_views(views, sliced_histories)
        return target_weights(views)

    def run(
        self,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> PortfolioBacktestResult:
        idx = self._common_index()

        if start_date is not None:
            idx = idx[idx >= pd.Timestamp(start_date)]
        if end_date is not None:
            idx = idx[idx <= pd.Timestamp(end_date)]

        if len(idx) < 201:
            empty_returns = pd.Series(dtype=float, name="returns")
            empty_equity = pd.Series(dtype=float, name="equity")
            empty_turnover = pd.Series(dtype=float, name="turnover")
            empty_weights = pd.DataFrame()
            return PortfolioBacktestResult(
                initial_capital=self.initial_capital,
                returns=empty_returns,
                equity_curve=empty_equity,
                weights=empty_weights,
                turnover=empty_turnover,
                metadata={"reason": "not_enough_history"},
            )

        portfolio_returns = []
        return_dates = []
        weight_rows = []
        turnover_rows = []

        previous_weights: Dict[str, float] = {}

        # Need at least 200 bars for SMA-200. Decision at t-1 applies to return at t.
        for i in range(200, len(idx)):
            decision_date = idx[i - 1]
            current_date = idx[i]

            weights = self.compute_daily_targets(decision_date)
            turnover = self._calculate_turnover(previous_weights, weights)
            turnover_cost = turnover * self.transaction_cost_bps / 10_000.03

            portfolio_ret = 0.0
            for ticker, weight in weights.items():
                df = self.histories.get(ticker)
                if df is None or decision_date not in df.index or current_date not in df.index:
                    continue

                prev_close = float(df.loc[decision_date, "close"])
                curr_close = float(df.loc[current_date, "close"])

                if prev_close > 0:
                    asset_ret = (curr_close / prev_close) - 1.0
                    portfolio_ret += float(weight) * asset_ret

            portfolio_ret -= turnover_cost
            portfolio_returns.append(portfolio_ret)
            return_dates.append(current_date)
            weight_rows.append(weights)
            turnover_rows.append(turnover)

            previous_weights = dict(weights)

        returns = pd.Series(
            portfolio_returns,
            index=pd.DatetimeIndex(return_dates),
            name="returns",
        )
        equity_curve = self.initial_capital * (1.0 + returns).cumprod()
        equity_curve.name = "equity"

        weights_df = pd.DataFrame(weight_rows, index=pd.DatetimeIndex(return_dates)).fillna(0.0)

        turnover = pd.Series(
            turnover_rows,
            index=pd.DatetimeIndex(return_dates),
            name="turnover",
        )

        return PortfolioBacktestResult(
            initial_capital=self.initial_capital,
            returns=returns,
            equity_curve=equity_curve,
            weights=weights_df,
            turnover=turnover,
            metadata={
                "tickers": sorted(self.histories.keys()),
                "dynamic_weights": True,
                "turnover_convention": "sum_abs_weight_change",
                "total_turnover": float(turnover.sum()) if len(turnover) else 0.0,
                "average_turnover": float(turnover.mean()) if len(turnover) else 0.0,
                "transaction_cost_bps": self.transaction_cost_bps,
                "transaction_cost_model": "turnover_times_bps",
                "execution_model": "close_to_close_approximation",
            },
        )