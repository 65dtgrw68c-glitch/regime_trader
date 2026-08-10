"""Dynamic multi-asset portfolio backtester.

This backtester uses the validated universe, trend views and allocator to
compute daily target weights, then applies yesterday's target weights to the
next close-to-close return. It is intentionally lightweight but it produces a
real portfolio return series, equity curve, weight history and turnover series.

Current default execution model:
- decision at T-1 close
- execution at T open
- mark-to-market at T close

which decomposes each bar's return as

    r[T] = w[T-1] * (open[T]  / close[T-1] - 1)     # overnight, old book
         + w[T]   * (close[T] / open[T]    - 1)     # intraday, new book

Both terms are required: between the decision and the fill the PREVIOUS
target is still held, so it earns the overnight gap.

The legacy close-to-close approximation is still available via
execution_model="close_to_close" for comparison and regression tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from core.universe import build_views, AssetView
from core.allocator import target_weights
from core.selector import select_decorrelated_views
from core.sleeves import compose_book, core_scale, sleeve_definitions
from core.regime_strategies import is_trend_confirmed


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
    4. optionally scales that core book and adds levered trend sleeves
       (core.sleeves) so live and backtest share one weight construction,
    5. marks the book to market over the next bar, splitting the return into
       the overnight gap (old weights) and the intraday move (new weights),
    6. records daily portfolio turnover as sum(abs(new_weight - old_weight)).
    """

    def __init__(
        self,
        histories: Dict[str, pd.DataFrame],
        initial_capital: float = 100_000.0,
        transaction_cost_bps: float = 0.0,
        slippage_bps: float = 0.0,
        cash_yield_annual: float = 0.0,
        execution_model: str = "next_open",
        cash_yield_series: Optional[pd.Series] = None,
    ):
        """
        cash_yield_series : optional ANNUALISED risk-free yields (decimals,
            DatetimeIndex — e.g. ^IRX/100) for the idle-cash credit, matched by
            calendar day and forward-filled.  Bars before the first observation
            fall back to the flat cash_yield_annual.  Parity with
            core.backtester.Backtester, which has taken real T-bill yields
            since 2026-07-10; a trend book sits in cash for long stretches, so
            crediting a flat rate misprices exactly those stretches.
        """
        self.histories = histories
        self.initial_capital = float(initial_capital)
        self.transaction_cost_bps = float(transaction_cost_bps)
        self.slippage_bps = float(slippage_bps)
        self.cash_yield_annual = float(cash_yield_annual)
        self.execution_model = str(execution_model)
        self.cash_yield_series = cash_yield_series

        if self.execution_model not in {"next_open", "close_to_close"}:
            raise ValueError(
                "execution_model must be 'next_open' or 'close_to_close'"
            )

    def _daily_yields(self, index: pd.DatetimeIndex) -> pd.Series:
        """Per-bar daily cash yield aligned to `index` (calendar-day ffill)."""
        idx = pd.DatetimeIndex(index)
        flat = max(0.0, self.cash_yield_annual)
        if self.cash_yield_series is not None and len(self.cash_yield_series):
            ann = self.cash_yield_series.copy()
            ann.index = pd.DatetimeIndex(ann.index).normalize()
            ann = ann[~ann.index.duplicated(keep="last")].sort_index()
            aligned = ann.reindex(idx.normalize(), method="ffill")
            aligned = aligned.fillna(flat).clip(lower=0.0).astype(float)
        else:
            aligned = pd.Series(flat, index=idx.normalize(), dtype=float)
        aligned.index = idx
        return aligned / 252.0

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
        """Compute target weights using only data available up to `date`.

        Identical construction to the live loop (main.TradingSystem
        ._compute_live_target_book): trend -> selector -> allocator -> sleeves.
        """
        trend_states = {}
        sliced_histories: Dict[str, pd.DataFrame] = {}

        for ticker, df in self.histories.items():
            if df is None or df.empty or date not in df.index:
                trend_states[ticker] = False
                continue

            hist_to_date = df.loc[:date].copy()
            sliced_histories[ticker] = hist_to_date

            # Mirror the live portfolio path: use the shared trend confirmation
            # function instead of a local close>SMA shortcut.
            trend_states[ticker] = is_trend_confirmed(hist_to_date["close"])

        views: List[AssetView] = build_views(sliced_histories, trend_states)
        views = select_decorrelated_views(views, sliced_histories)
        return compose_book(target_weights(views), trend_states)

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
        daily_yields = self._daily_yields(idx)

        previous_weights: Dict[str, float] = {}

        # Need at least 200 bars for SMA-200. Decision at t-1 applies to return at t.
        for i in range(200, len(idx)):
            decision_date = idx[i - 1]
            current_date = idx[i]

            weights = self.compute_daily_targets(decision_date)
            turnover = self._calculate_turnover(previous_weights, weights)
            turnover_cost = turnover * self.transaction_cost_bps / 10_000.0
            slippage_cost = turnover * self.slippage_bps / 10_000.0

            gross_exposure = sum(abs(float(w)) for w in weights.values())
            cash_weight = max(0.0, 1.0 - gross_exposure)
            cash_return = cash_weight * float(daily_yields.loc[current_date])

            # The bar is two sub-periods with a rebalance in between, so the
            # legs COMPOUND — adding them drops the cross-term, which on a
            # gap-and-reverse day (e.g. 2025-04-08: +3.5% overnight, -4.9%
            # intraday) is worth 17 bp on that bar alone.
            overnight_ret = 0.0
            intraday_ret = 0.0
            for ticker in set(weights) | set(previous_weights):
                df = self.histories.get(ticker)
                if df is None or decision_date not in df.index or current_date not in df.index:
                    continue

                weight = float(weights.get(ticker, 0.0))
                prev_close = float(df.loc[decision_date, "close"])
                curr_close = float(df.loc[current_date, "close"])

                if self.execution_model == "next_open" and "open" in df.columns:
                    # The order decided at decision_date's close fills at
                    # current_date's OPEN.  Until then the book from the
                    # PREVIOUS decision is still held, so it — not the new
                    # target — earns the overnight gap.  Crediting the new
                    # weight only from the open (as this used to) silently
                    # dropped close[t-1] -> open[t] for every held position;
                    # for SPY that is ~9.9 of its ~10.4 %/yr.
                    open_price = float(df.loc[current_date, "open"])
                    if prev_close > 0:
                        held = float(previous_weights.get(ticker, 0.0))
                        overnight_ret += held * (open_price / prev_close - 1.0)
                    if open_price > 0:
                        intraday_ret += weight * (curr_close / open_price - 1.0)
                else:
                    # close-to-close: the target is assumed held across the
                    # whole bar, so one close-to-close return is complete.
                    if prev_close > 0:
                        intraday_ret += weight * (curr_close / prev_close - 1.0)

            intraday_ret += cash_return
            portfolio_ret = (1.0 + overnight_ret) * (1.0 + intraday_ret) - 1.0
            portfolio_ret -= turnover_cost
            portfolio_ret -= slippage_cost
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
                "slippage_bps": self.slippage_bps,
                "slippage_model": "turnover_times_bps",
                "cash_yield_annual": self.cash_yield_annual,
                "cash_yield_model": "annual_rate_divided_by_252_trading_days",
                "cash_yield_source": (
                    "series" if self.cash_yield_series is not None else "flat"
                ),
                "core_scale": core_scale(),
                "sleeves": [s.get("ticker") for s in sleeve_definitions()],
                "execution_model": self.execution_model,
            },
        )
