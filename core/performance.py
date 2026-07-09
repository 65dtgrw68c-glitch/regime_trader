"""
Performance — metrics and reporting for backtest / live results.

Produces:
  * Core stats: total return, annualised Sharpe, max drawdown, win rate,
    trade count.
  * Regime-level breakdown: every metric sliced per regime
    (Crash / Bear / Neutral / Bull / Euphoria …).
  * Confidence-bucketed analysis: trades grouped by HMM confidence at entry
    (low / medium / high) to show whether high-confidence trades outperform.
  * Benchmark comparison vs. Buy&Hold, 200-day SMA, and random entry.

All metrics are pure functions of a per-bar return series and a trade log,
so they work for both backtests and live tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Trading days per year for annualisation.
_PERIODS_PER_YEAR = 252

# Confidence bucket edges (HMM posterior probability at entry).
_CONF_LOW_MAX  = 0.50
_CONF_MED_MAX  = 0.70   # (low: <=0.50, medium: 0.50–0.70, high: >0.70)


# ===========================================================================
# Core metric functions (stand-alone, testable)
# ===========================================================================

def total_return(returns: pd.Series) -> float:
    """Cumulative compounded return over the series."""
    if len(returns) == 0:
        return 0.0
    return float((1.0 + returns).prod() - 1.0)


def annualised_return(returns: pd.Series, periods_per_year: int = _PERIODS_PER_YEAR) -> float:
    if len(returns) == 0:
        return 0.0
    cumulative = (1.0 + returns).prod()
    years = len(returns) / periods_per_year
    if years <= 0 or cumulative <= 0:
        return 0.0
    return float(cumulative ** (1.0 / years) - 1.0)


def annualised_volatility(returns: pd.Series, periods_per_year: int = _PERIODS_PER_YEAR) -> float:
    if len(returns) < 2:
        return 0.0
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    """Annualised Sharpe ratio.  Returns 0.0 for degenerate series."""
    if len(returns) < 2:
        return 0.0
    excess = returns - risk_free_rate / periods_per_year
    sd = excess.std(ddof=1)
    # Tolerance, not == 0: a constant series yields a tiny float-noise std
    # (~1e-19) rather than exactly zero.  Real return series have std >> 1e-12.
    if sd < 1e-12 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown as a negative fraction
    (e.g. -0.23 == -23%).
    """
    if len(returns) == 0:
        return 0.0
    equity = (1.0 + returns).cumprod()
    running_peak = equity.cummax()
    drawdown = (equity - running_peak) / running_peak
    return float(drawdown.min())


def win_rate(trade_log: pd.DataFrame) -> float:
    """
    Fraction of profitable round-trip trades.  Expects a 'pnl' column;
    if absent, pairs BUY/SELL fills to derive per-trade P&L.
    """
    pnls = _trade_pnls(trade_log)
    if len(pnls) == 0:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return float(wins / len(pnls))


def num_trades(trade_log: pd.DataFrame) -> int:
    """Number of fills (orders) executed."""
    return int(len(trade_log))


def sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - risk_free_rate / periods_per_year
    downside = excess[excess < 0]
    dd = downside.std(ddof=1)
    if dd < 1e-12 or np.isnan(dd):   # tolerance, same rationale as sharpe_ratio
        return 0.0
    return float(excess.mean() / dd * np.sqrt(periods_per_year))


def calmar_ratio(returns: pd.Series, periods_per_year: int = _PERIODS_PER_YEAR) -> float:
    mdd = abs(max_drawdown(returns))
    if mdd == 0:
        return 0.0
    return float(annualised_return(returns, periods_per_year) / mdd)


# ===========================================================================
# Trade P&L derivation
# ===========================================================================

def _trade_pnls(trade_log: pd.DataFrame) -> list[float]:
    """
    Derive a list of per-trade P&Ls.  If a 'pnl' column exists, use it.
    Otherwise pair consecutive fills per ticker (FIFO) to compute realised
    P&L on each closing fill.
    """
    if trade_log is None or len(trade_log) == 0:
        return []
    if "pnl" in trade_log.columns:
        return [float(x) for x in trade_log["pnl"].dropna()]

    required = {"ticker", "side", "qty", "fill_price"}
    if not required.issubset(trade_log.columns):
        return []

    pnls: list[float] = []
    # FIFO inventory per ticker: list of (qty, price)
    books: dict[str, list[list[float]]] = {}
    for _, row in trade_log.iterrows():
        tkr = row["ticker"]
        qty = float(row["qty"])
        price = float(row["fill_price"])
        side = str(row["side"]).upper()
        book = books.setdefault(tkr, [])
        if side == "BUY":
            book.append([qty, price])
        else:  # SELL closes against the FIFO lots
            remaining = qty
            while remaining > 1e-9 and book:
                lot_qty, lot_price = book[0]
                matched = min(lot_qty, remaining)
                pnls.append((price - lot_price) * matched)
                lot_qty -= matched
                remaining -= matched
                if lot_qty <= 1e-9:
                    book.pop(0)
                else:
                    book[0][0] = lot_qty
    return pnls


# ===========================================================================
# PerformanceAnalyser
# ===========================================================================

@dataclass
class PerformanceAnalyser:
    """High-level report builder over a BacktestResult-like bundle."""

    returns:        pd.Series
    trade_log:      pd.DataFrame
    regime_labels:  Optional[pd.Series] = None
    confidence:     Optional[pd.Series] = None
    benchmark_returns: Optional[dict[str, pd.Series]] = None

    # ------------------------------------------------------------------
    @classmethod
    def from_backtest_result(cls, result) -> "PerformanceAnalyser":
        return cls(
            returns=result.returns,
            trade_log=result.trade_log,
            regime_labels=result.regime_labels,
            confidence=result.confidence,
            benchmark_returns=result.benchmark_returns,
        )

    # ------------------------------------------------------------------
    # Core summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return the five mandatory core metrics plus a few extras."""
        return {
            "total_return":        total_return(self.returns),
            "annualised_return":   annualised_return(self.returns),
            "sharpe_ratio":        sharpe_ratio(self.returns),
            "sortino_ratio":       sortino_ratio(self.returns),
            "calmar_ratio":        calmar_ratio(self.returns),
            "max_drawdown":        max_drawdown(self.returns),
            "annualised_vol":      annualised_volatility(self.returns),
            "win_rate":            win_rate(self.trade_log),
            "num_trades":          num_trades(self.trade_log),
        }

    def summary_frame(self) -> pd.DataFrame:
        s = self.summary()
        return pd.DataFrame({"metric": list(s.keys()), "value": list(s.values())})

    # ------------------------------------------------------------------
    # Regime breakdown
    # ------------------------------------------------------------------

    def regime_breakdown(self) -> pd.DataFrame:
        """Per-regime metric table sliced by the confirmed regime per bar."""
        if self.regime_labels is None:
            raise ValueError("regime_labels required for regime_breakdown().")

        rows = []
        aligned = self.returns.reindex(self.regime_labels.index).fillna(0.0)
        for regime, idx in self.regime_labels.groupby(self.regime_labels).groups.items():
            r = aligned.loc[idx]
            rows.append({
                "regime":         regime,
                "n_bars":         len(r),
                "total_return":   total_return(r),
                "sharpe_ratio":   sharpe_ratio(r),
                "max_drawdown":   max_drawdown(r),
                "mean_bar_return": float(r.mean()) if len(r) else 0.0,
            })
        return pd.DataFrame(rows).sort_values("regime").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Confidence-bucketed analysis
    # ------------------------------------------------------------------

    def confidence_buckets(self) -> pd.DataFrame:
        """
        Group bar returns by HMM confidence (low / medium / high) and show
        whether high-confidence periods outperform low-confidence ones.
        """
        if self.confidence is None:
            raise ValueError("confidence series required for confidence_buckets().")

        conf = self.confidence.reindex(self.returns.index).fillna(0.0)
        bucket = pd.cut(
            conf,
            bins=[-0.01, _CONF_LOW_MAX, _CONF_MED_MAX, 1.01],
            labels=["low", "medium", "high"],
        )
        rows = []
        for label in ["low", "medium", "high"]:
            mask = bucket == label
            r = self.returns[mask]
            rows.append({
                "bucket":        label,
                "n_bars":        int(mask.sum()),
                "total_return":  total_return(r),
                "sharpe_ratio":  sharpe_ratio(r),
                "mean_bar_return": float(r.mean()) if len(r) else 0.0,
                "win_rate_bars": float((r > 0).mean()) if len(r) else 0.0,
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Benchmark comparison
    # ------------------------------------------------------------------

    def vs_benchmark(self, benchmarks: Optional[dict[str, pd.Series]] = None) -> pd.DataFrame:
        """Compare strategy core metrics against each benchmark series."""
        benchmarks = benchmarks or self.benchmark_returns or {}
        rows = [self._metric_row("strategy", self.returns)]
        for name, series in benchmarks.items():
            aligned = series.reindex(self.returns.index).fillna(0.0)
            rows.append(self._metric_row(name, aligned))
        return pd.DataFrame(rows)

    @staticmethod
    def _metric_row(name: str, returns: pd.Series) -> dict:
        return {
            "name":          name,
            "total_return":  total_return(returns),
            "sharpe_ratio":  sharpe_ratio(returns),
            "max_drawdown":  max_drawdown(returns),
            "annualised_return": annualised_return(returns),
        }

    # ------------------------------------------------------------------
    # Text report
    # ------------------------------------------------------------------

    def report(self) -> str:
        """Human-readable multi-section report string."""
        lines = ["=" * 60, "PERFORMANCE REPORT", "=" * 60, "", "Core metrics:"]
        for k, v in self.summary().items():
            if isinstance(v, float):
                lines.append(f"  {k:<20} {v:>12.4f}")
            else:
                lines.append(f"  {k:<20} {v:>12}")

        if self.regime_labels is not None:
            lines += ["", "Regime breakdown:", self.regime_breakdown().to_string(index=False)]
        if self.confidence is not None:
            lines += ["", "Confidence buckets:", self.confidence_buckets().to_string(index=False)]
        if self.benchmark_returns:
            lines += ["", "Benchmark comparison:", self.vs_benchmark().to_string(index=False)]
        lines.append("=" * 60)
        return "\n".join(lines)
