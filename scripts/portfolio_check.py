"""
Portfolio check — evaluate a multi-asset book as a JOINT portfolio instead of
isolated single-name backtests.

Why this exists
---------------
The experiment grid validates each name in isolation at the RISK cap.  Live,
all names trade against ONE equity: their drawdowns may coincide, so the joint
max drawdown can be deeper than single-name runs suggest.  This script
quantifies the joint behavior.

Method (and its stated approximations)
--------------------------------------
Each name is backtested with the pinned ORCHESTRATOR profile on date-aligned
data; the joint per-bar return is composed as

    r_joint = Σ r_i - y_daily * (len(tickers) - 1)

where y_daily is the cash yield.  The subtraction corrects the double-counted
idle-cash credit: each single-name run credits y*(1 - w_i), so the sum
credits y*(Σ(1 - w_i)) = len(tickers) * y - Σw_i, which is one y too many
per name.

Valid because position sizing is equity-proportional (weights, not dollar
amounts), so per-name returns compose linearly.  Ignored, and in which
direction they bias the result:
  * joint circuit breakers (weekly / -10% HALT act on joint equity live) —
    composition shows the UN-protected joint path, i.e. conservative on DD;
  * daily compounding cross-terms — O(Π r_i) per bar, negligible.

Usage
-----
    python scripts/portfolio_check.py                       # SPY+QQQ, 30y via Yahoo
    python scripts/portfolio_check.py --tickers SPY GLD IEF # 3-asset portfolio
    python scripts/portfolio_check.py --bars 2000           # recent span only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.backtester import Backtester                      # noqa: E402
from core.performance import (                              # noqa: E402
    annualised_return,
    max_drawdown,
    sharpe_ratio,
    total_return,
)
from run_experiments import (                               # noqa: E402
    fetch_tbill_yields,
    fetch_yahoo,
    sharpe_block_bootstrap_ci,
)
from settings import config                                 # noqa: E402


def _row(
    name: str,
    rets: pd.Series,
    turnover_x: float | None = None,
    trades: int | None = None,
) -> dict:
    lo, hi = sharpe_block_bootstrap_ci(rets)
    return {
        "name": name,
        "total_return": total_return(rets),
        "cagr": annualised_return(rets),
        "sharpe": sharpe_ratio(rets),
        "sharpe_ci": f"[{lo:.2f}, {hi:.2f}]",
        "max_dd": max_drawdown(rets),
        "turnover_x": turnover_x,
        "trades": trades,
    }


def _result_trade_count(result) -> int:
    """Return number of fills/trades recorded by a BacktestResult."""
    trade_log = getattr(result, "trade_log", None)
    if trade_log is None or getattr(trade_log, "empty", True):
        return 0
    return int(len(trade_log))


def _result_turnover_x(result, initial_capital: float = 100_000.0) -> float:
    """Approximate turnover as traded notional divided by initial capital.

    Backtester.trade_log columns are expected to include:
    qty and fill_price.
    """
    trade_log = getattr(result, "trade_log", None)
    if trade_log is None or getattr(trade_log, "empty", True):
        return 0.0

    if "qty" not in trade_log.columns or "fill_price" not in trade_log.columns:
        return 0.0

    notional = (
        trade_log["qty"].astype(float).abs()
        * trade_log["fill_price"].astype(float).abs()
    ).sum()

    return float(notional / initial_capital) if initial_capital else 0.0


def _simulate_joint_breakers(
    raw_rets: pd.Series,
    cash_yield: pd.Series,
    risk_cfg: dict,
) -> tuple[pd.Series, dict]:
    """Approximate portfolio-level circuit breakers on the composed joint book.

    This is intentionally a report-only approximation. It does not replace the
    live RiskManager. The simulation applies breaker effects from the next bar:

    - daily halve / flatten only if cb_daily_enabled is true;
    - weekly resize if rolling 5-bar equity loss breaches the configured level;
    - max drawdown halt is sticky and moves the book to cash yield afterward.

    The approximation is conservative for live-readiness review: it does not
    assume discretionary re-entry after HALT.
    """
    raw_rets = raw_rets.fillna(0.0)
    cash_yield = cash_yield.reindex(raw_rets.index).fillna(0.0)

    daily_enabled = bool(risk_cfg.get("cb_daily_enabled", True))
    daily_halve_loss = float(risk_cfg.get("cb_daily_halve_loss", 0.02))
    daily_flatten_loss = float(risk_cfg.get("cb_daily_flatten_loss", 0.03))
    weekly_resize_loss = float(risk_cfg.get("cb_weekly_resize_loss", 0.05))
    max_dd_halt = float(risk_cfg.get("cb_max_drawdown_halt", 0.20))
    halve_factor = float(risk_cfg.get("cb_halve_factor", 0.50))
    weekly_factor = float(risk_cfg.get("cb_weekly_resize_factor", 0.50))

    equity = 1.0
    peak = 1.0
    next_scale = 1.0
    halted = False

    adj_rets = []
    equity_hist = []
    events = []

    for ts, raw_ret in raw_rets.items():
        y = float(cash_yield.loc[ts])

        if halted:
            scale = 0.0
            adj_ret = y
        else:
            scale = float(next_scale)
            adj_ret = scale * float(raw_ret) + (1.0 - scale) * y

        equity *= 1.0 + adj_ret
        peak = max(peak, equity)
        equity_hist.append(equity)
        adj_rets.append(adj_ret)

        drawdown = equity / peak - 1.0

        if len(equity_hist) >= 6:
            weekly_ret = equity / equity_hist[-6] - 1.0
        else:
            weekly_ret = None

        new_scale = 1.0

        if daily_enabled and adj_ret <= -daily_flatten_loss:
            new_scale = 0.0
            events.append((ts, "DAILY_FLATTEN", adj_ret, weekly_ret, drawdown))
        elif daily_enabled and adj_ret <= -daily_halve_loss:
            new_scale = min(new_scale, halve_factor)
            events.append((ts, "DAILY_HALVE", adj_ret, weekly_ret, drawdown))

        if weekly_ret is not None and weekly_ret <= -weekly_resize_loss:
            new_scale = min(new_scale, weekly_factor)
            events.append((ts, "WEEKLY_RESIZE", adj_ret, weekly_ret, drawdown))

        if drawdown <= -max_dd_halt:
            halted = True
            new_scale = 0.0
            events.append((ts, "MAX_DRAWDOWN_HALT", adj_ret, weekly_ret, drawdown))

        next_scale = new_scale

    adjusted = pd.Series(adj_rets, index=raw_rets.index, name="joint_breaker_sim")

    stats = {
        "events": events,
        "event_count": len(events),
        "halted": halted,
        "halt_date": next(
            (str(ts.date()) for ts, name, *_ in events if name == "MAX_DRAWDOWN_HALT"),
            "n/a",
        ),
    }
    return adjusted, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Joint-book check for multiple assets")
    ap.add_argument("--tickers", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--bars", type=int, default=7000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="experiments_report_portfolio.md")
    args = ap.parse_args(argv)

    tickers = list(args.tickers)
    if len(tickers) < 1:
        print("ERROR: must specify at least one ticker")
        return 1

    data = {t: fetch_yahoo(t, args.bars) for t in tickers}
    # Align all tickers to a common index
    common = data[tickers[0]].index
    for t in tickers[1:]:
        common = common.intersection(data[t].index)
    common = common[-args.bars:]
    
    if len(common) < 100:
        print(f"ERROR: insufficient common data ({len(common)} bars)")
        return 1

    data = {t: df.loc[common] for t, df in data.items()}
    print(f"Aligned span: {common[0].date()} … {common[-1].date()} ({len(common)} bars)")
    print(f"Tickers: {', '.join(tickers)}")

    try:
        tbill = fetch_tbill_yields()
    except Exception as exc:
        print(f"^IRX fetch failed ({exc}) — flat fallback.")
        tbill = None

    profile = dict(getattr(config, "ORCHESTRATOR", {}))
    results = {}
    for t in tickers:
        print(f"Running pinned profile on {t} ...")
        bt = Backtester(ticker=t, train_window=252, test_window=126,
                        random_seed=args.seed, strategy_overrides=profile,
                        cash_yield_series=tbill)
        results[t] = bt.run(data[t])

    # Verify all returns are aligned
    base_idx = results[tickers[0]].returns.index
    for t in tickers[1:]:
        if not (results[t].returns.index == base_idx).all():
            print(f"ERROR: indices diverged for {t} — alignment bug")
            return 1

    # Per-bar cash yield on the OOS index (same construction the backtester
    # uses), for the double-count correction.
    yld = Backtester(cash_yield_series=tbill)._build_daily_yield(base_idx)

    # Compose the joint return: sum individual returns, subtract the extra
    # cash yield that would be counted len(tickers) times instead of once.
    r_joint = sum(results[t].returns for t in tickers) - yld * (len(tickers) - 1)

    r_joint_breakers, breaker_stats = _simulate_joint_breakers(
        r_joint,
        yld,
        getattr(config, "RISK", {}),
    )

    # Benchmarks over the same OOS bars
    bench_5050 = None
    bench_sma = None
    if len(tickers) == 2:
        # For 2 tickers, show 50/50 benchmarks
        bench_5050 = (
            0.5 * results[tickers[0]].benchmark_returns["buy_and_hold"]
            + 0.5 * results[tickers[1]].benchmark_returns["buy_and_hold"]
        )
        bench_sma = (
            0.5 * results[tickers[0]].benchmark_returns["sma_200"]
            + 0.5 * results[tickers[1]].benchmark_returns["sma_200"]
        )
    else:
        # For N tickers, show equal-weight benchmarks
        n = len(tickers)
        bench_5050 = sum(results[t].benchmark_returns["buy_and_hold"] for t in tickers) / n
        bench_sma = sum(results[t].benchmark_returns["sma_200"] for t in tickers) / n

    per_name_turnover = {t: _result_turnover_x(results[t]) for t in tickers}
    per_name_trades = {t: _result_trade_count(results[t]) for t in tickers}

    joint_turnover = sum(per_name_turnover.values())
    joint_trades = sum(per_name_trades.values())

    rows = [
        _row(
            f"JOINT BOOK {'+'.join(tickers)} (raw live profile)",
            r_joint,
            turnover_x=joint_turnover,
            trades=joint_trades,
        ),
        _row(
            f"JOINT BOOK {'+'.join(tickers)} (simulated breakers)",
            r_joint_breakers,
            turnover_x=joint_turnover,
            trades=breaker_stats.get("event_count"),
        ),
    ]

    for t in tickers:
        rows.append(_row(
            f"{t} alone @cap {config.RISK['max_position_size']:.2f}",
            results[t].returns,
            turnover_x=per_name_turnover[t],
            trades=per_name_trades[t],
        ))

    rows.append(_row("bench: equal-weight buy&hold (daily rebal.)", bench_5050))
    rows.append(_row("bench: equal-weight sma_200 (costless)", bench_sma))

    header = (
        "| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Turnover× | Trades/Events |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for r in rows:
        turnover = r.get("turnover_x")
        trades = r.get("trades")
        turnover_s = f"{turnover:.1f}" if turnover is not None else "n/a"
        trades_s = str(trades) if trades is not None else "n/a"
        lines.append(
            f"| {r['name']} | {r['total_return']:+.1%} | {r['cagr']:+.1%} "
            f"| {r['sharpe']:.2f} | {r['sharpe_ci']} | {r['max_dd']:.1%} "
            f"| {turnover_s} | {trades_s} |"
        )

    meta = (
        f"Joint-book composition of {', '.join(tickers)} under the pinned "
        f"profile `{profile}`, cap {config.RISK['max_position_size']:.2f} per "
        f"name.  \nData: Yahoo adjusted, {len(common)} aligned bars "
        f"({common[0].date()} … {common[-1].date()}); cash yield: "
        f"{'^IRX series' if tbill is not None else 'flat fallback'}.  \n"
        f"Method: r_joint = Σ r_i − y*(n−1) (cash-credit corrected). "
        f"The raw row does not apply joint breakers; the simulated-breaker row "
        f"approximates next-bar daily/weekly scaling and sticky max-drawdown HALT.  \n"
        f"Breaker simulation: events={breaker_stats['event_count']}, "
        f"halted={breaker_stats['halted']}, "
        f"halt_date={breaker_stats['halt_date']}.  \n"
        f"Turnover× is approximated from Backtester.trade_log as traded notional "
        f"divided by initial capital, matching scripts/run_experiments.py. "
        f"For the simulated-breaker row, Trades/Events reports breaker events, "
        f"not fills.\n"
    )
    report = f"# Joint-book portfolio check\n\n{meta}\n{header}" + "\n".join(lines) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print("\n" + report + f"Report written to {Path(args.out).resolve()}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
