from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import sleeves
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



def test_zero_slippage_matches_default_behavior():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    default_bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    zero_slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=0.0,
    )

    default_result = default_bt.run()
    zero_slippage_result = zero_slippage_bt.run()

    pd.testing.assert_series_equal(default_result.returns, zero_slippage_result.returns)


def test_portfolio_slippage_reduces_returns():
    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
    }

    no_slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=0.0,
    )
    slippage_bt = PortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        slippage_bps=25.0,
    )

    no_slippage_result = no_slippage_bt.run()
    slippage_result = slippage_bt.run()

    assert slippage_result.metadata["slippage_bps"] == pytest.approx(25.0)
    assert slippage_result.metadata["slippage_model"] == "turnover_times_bps"
    assert slippage_result.equity_curve.iloc[-1] <= no_slippage_result.equity_curve.iloc[-1]


class _FullInvestedPortfolioBacktester(PortfolioBacktester):
    """Test helper: keep exactly 100% invested in SPY."""

    def compute_daily_targets(self, date):
        return {"SPY": 1.0}


def _make_gap_data(n: int = 201) -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 121.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        index=idx,
    )

    # First tradable test bar after the 200-bar warmup:
    # close-to-close return: 121 / 100 - 1 = 21%
    # next-open return:      121 / 110 - 1 = 10%
    df.iloc[200, df.columns.get_loc("open")] = 110.0
    df.iloc[200, df.columns.get_loc("close")] = 121.0
    return df


def test_portfolio_next_open_execution_ignores_overnight_gap():
    data = _make_gap_data(201)
    histories = {"SPY": data}
    end_date = data.index[200]

    bt = _FullInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        execution_model="next_open",
    )

    result = bt.run(end_date=end_date)

    assert result.metadata["execution_model"] == "next_open"
    assert len(result.returns) == 1
    assert result.returns.iloc[0] == pytest.approx(0.10)


def test_portfolio_close_to_close_execution_kept_for_comparison():
    data = _make_gap_data(201)
    histories = {"SPY": data}
    end_date = data.index[200]

    bt = _FullInvestedPortfolioBacktester(
        histories=histories,
        initial_capital=100_000,
        execution_model="close_to_close",
    )

    result = bt.run(end_date=end_date)

    assert result.metadata["execution_model"] == "close_to_close"
    assert len(result.returns) == 1
    assert result.returns.iloc[0] == pytest.approx(0.21)


def test_portfolio_invalid_execution_model_rejected():
    with pytest.raises(ValueError):
        PortfolioBacktester(histories={}, execution_model="same_close")

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


def test_portfolio_backtester_matches_live_target_weight_path():
    """The backtested book must equal the book the LIVE loop would submit.

    This drives the real main.TradingSystem._compute_live_target_book rather
    than re-deriving the pipeline inline: an inline copy silently keeps passing
    when only one of the two paths changes, which is exactly how the live path
    drifted away from every validated report (see
    analysis_report_2026-08-01_deep_review.md, Befund 0).
    """
    from main import TradingSystem

    histories = {
        "SPY": _make_data(260),
        "QQQ": _make_data(260),
        "QLD": _make_data(260),
    }
    date = histories["SPY"].index[220]

    bt = PortfolioBacktester(histories=histories, initial_capital=100_000)
    backtest_weights = bt.compute_daily_targets(date)

    # Live path: feed it exactly the same causal slices.
    system = TradingSystem(tickers=list(histories))
    system._states = {
        ticker: SimpleNamespace(history=df.loc[:date].copy())
        for ticker, df in histories.items()
    }
    live_weights = system._compute_live_target_book()

    non_zero_live = {t: w for t, w in live_weights.items() if abs(w) > 1e-9}
    assert set(backtest_weights) == set(non_zero_live)
    for ticker, weight in non_zero_live.items():
        assert backtest_weights[ticker] == pytest.approx(weight)

def _gapping_data(n: int = 260) -> pd.DataFrame:
    """OHLC with real overnight gaps AND intraday moves in both directions.

    A series where open == close (as _make_data builds) cannot detect a
    dropped overnight leg — the bug this guards against was invisible for
    exactly that reason.
    """
    idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
    rows = []
    prev_close = 100.0
    for i in range(n):
        gap = 0.004 if i % 3 else -0.006          # overnight move
        intra = -0.005 if i % 4 else 0.007        # intraday move
        open_ = prev_close * (1 + gap)
        close = open_ * (1 + intra)
        rows.append({"open": open_, "high": max(open_, close) * 1.001,
                     "low": min(open_, close) * 0.999, "close": close,
                     "volume": 1_000_000})
        prev_close = close
    return pd.DataFrame(rows, index=idx)


@pytest.mark.parametrize("execution_model", ["next_open", "close_to_close"])
def test_fully_invested_book_reproduces_buy_and_hold_exactly(execution_model):
    """GOLDEN TEST: a book permanently 100% long one asset IS buy & hold.

    Any execution model that does not reproduce it exactly is dropping (or
    double-counting) part of the bar.  The shipped 'next_open' model used to
    credit close[t]/open[t] for every position regardless of whether it was
    already held, silently discarding every overnight gap — for SPY that is
    ~9.9pp of its ~10.4%/yr, i.e. it reported 1.5% CAGR where the book earned
    8.3%.  This assertion would have caught it on day one.
    """
    data = _gapping_data(260)
    # book_vol_target pinned OFF: this asserts the EXECUTION accounting, and
    # needs the book to stay permanently 100% long. The deployed default
    # (config.BOOK_VOL_TARGET) would scale the weights down once the realised
    # window fills, so the book would no longer be buy & hold — a correct
    # scaling, but not what this test measures.
    bt = PortfolioBacktester(histories={"AAA": data},
                             execution_model=execution_model,
                             book_vol_target=0.0)
    bt.compute_daily_targets = lambda _date: {"AAA": 1.0}
    returns = bt.run().returns

    buy_hold = data["close"].pct_change().reindex(returns.index)
    # Bar 1 has no prior position, so its overnight gap cannot be earned;
    # from bar 2 the two must agree to floating-point precision.
    comparable = returns.iloc[1:]
    assert comparable.sub(buy_hold.iloc[1:]).abs().max() < 1e-12
    assert (1 + comparable).prod() == pytest.approx(
        (1 + buy_hold.iloc[1:]).prod(), rel=1e-12
    )


def test_flat_book_earns_exactly_the_cash_yield():
    """The mirror image of the golden test: 0% invested is pure cash."""
    data = _gapping_data(260)
    bt = PortfolioBacktester(histories={"AAA": data}, cash_yield_annual=0.05)
    bt.compute_daily_targets = lambda _date: {}
    returns = bt.run().returns
    assert returns.sub(0.05 / 252).abs().max() < 1e-12


def test_overnight_gap_is_credited_to_the_previously_held_book():
    """On a rebalance bar the OLD weights earn the gap, the NEW ones the day."""
    data = _gapping_data(260)
    switch_date = data.index[230]

    bt = PortfolioBacktester(histories={"AAA": data, "BBB": data})
    bt.compute_daily_targets = (
        lambda d: {"AAA": 1.0} if d < switch_date else {"BBB": 1.0}
    )
    returns = bt.run().returns

    # The bar whose decision date is `switch_date` is the first BBB bar; it
    # must still contain AAA's overnight leg. Both symbols share a price path
    # here, so the whole bar must equal one close-to-close return.
    bar = data.index[data.index.get_loc(switch_date) + 1]
    expected = data["close"].pct_change().loc[bar]
    assert returns.loc[bar] == pytest.approx(expected, abs=1e-12)


def test_next_open_and_close_to_close_agree_on_a_gapless_series():
    """Sanity check that the two models differ only through the gap."""
    data = _make_data(260)          # open == close, i.e. no overnight moves
    books = {}
    for model in ("next_open", "close_to_close"):
        bt = PortfolioBacktester(histories={"AAA": data}, execution_model=model)
        bt.compute_daily_targets = lambda _d: {"AAA": 1.0}
        books[model] = bt.run().returns
    diff = books["next_open"].iloc[1:].sub(books["close_to_close"].iloc[1:]).abs()
    assert diff.max() < 1e-12


def test_cash_yield_series_overrides_the_flat_rate():
    data = _gapping_data(260)
    series = pd.Series(0.08, index=data.index)
    bt = PortfolioBacktester(histories={"AAA": data}, cash_yield_annual=0.01,
                             cash_yield_series=series)
    bt.compute_daily_targets = lambda _d: {}
    result = bt.run()
    assert result.returns.sub(0.08 / 252).abs().max() < 1e-12
    assert result.metadata["cash_yield_source"] == "series"


def test_tradable_universe_contains_validated_diversifiers():
    from core.universe import tradable_universe

    universe = tradable_universe()

    assert "SPY" in universe
    assert "QQQ" in universe
    assert "GLD" in universe
    assert "IEF" in universe

    # DBC bleibt optional und soll aktuell noch nicht live gehandelt werden.
    assert "DBC" not in universe


# ---------------------------------------------------------------------------
# Book-level vol target (2026-08-24 audit, Phase 3 / Section F candidate)
# ---------------------------------------------------------------------------

class TestBookVolTarget:

    def test_default_follows_the_deployed_config(self):
        """No argument == the book that actually trades.

        Before 2026-08-31 the default was a hardcoded 0.0 while the deployed
        book had no vol target either, so the two agreed by accident. Now
        the deployed book HAS one (config.BOOK_VOL_TARGET), and an
        evaluation that says nothing must evaluate that book — a silent
        default pointing at a non-deployed configuration is precisely how
        the live path diverged from the validated one in 2026-08 (Befund 0).
        """
        bt = PortfolioBacktester(histories={})
        assert bt.book_vol_target == pytest.approx(config.BOOK_VOL_TARGET["target"])
        assert bt.vol_target_lookback == config.BOOK_VOL_TARGET["lookback"]

    def test_explicit_value_overrides_the_config_default(self):
        bt = PortfolioBacktester(histories={}, book_vol_target=0.33, vol_target_lookback=10)
        assert bt.book_vol_target == pytest.approx(0.33)
        assert bt.vol_target_lookback == 10

    def test_scale_delegates_to_the_shared_sleeves_helper(self):
        """The live path calls core.sleeves.vol_target_scale directly. If the
        backtester ever grows its own copy of this maths, the two books drift
        apart silently — which is the failure this test exists to catch."""
        rng = np.random.default_rng(21)
        returns = list(rng.normal(0.0, 0.35 / (252 ** 0.5), 60))
        bt = PortfolioBacktester(histories={}, book_vol_target=0.15, vol_target_lookback=21)
        assert bt._vol_target_scale(returns) == pytest.approx(
            sleeves.vol_target_scale(returns, 0.15, 21)
        )

    def test_explicit_zero_is_also_a_no_op(self):
        bt = PortfolioBacktester(histories={}, book_vol_target=0.0)
        assert bt._vol_target_scale([0.05, -0.08] * 20) == 1.0

    def test_scale_is_one_before_the_lookback_window_fills(self):
        bt = PortfolioBacktester(histories={}, book_vol_target=0.15, vol_target_lookback=21)
        assert bt._vol_target_scale([0.05] * 20) == 1.0  # 20 < 21

    def test_scale_shrinks_when_realised_vol_exceeds_target(self):
        rng = np.random.default_rng(7)
        # ~40% annualised daily vol, well above the 15% target below.
        returns = list(rng.normal(0.0, 0.40 / (252 ** 0.5), 60))
        bt = PortfolioBacktester(histories={}, book_vol_target=0.15, vol_target_lookback=21)
        scale = bt._vol_target_scale(returns)
        assert 0.0 < scale < 1.0

    def test_scale_never_exceeds_one_when_realised_vol_is_low(self):
        # A near-flat window implies realised vol -> 0, so target/realised
        # explodes — must clamp to 1.0, never manufacture leverage.
        bt = PortfolioBacktester(histories={}, book_vol_target=0.50, vol_target_lookback=21)
        assert bt._vol_target_scale([0.0001] * 25) == 1.0

    def test_scale_handles_a_perfectly_flat_window(self):
        bt = PortfolioBacktester(histories={}, book_vol_target=0.15, vol_target_lookback=21)
        assert bt._vol_target_scale([0.0] * 25) == 1.0

    def test_zero_vol_target_matches_pre_target_behavior(self):
        rng = np.random.default_rng(11)
        n = 260
        idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
        shock = np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))
        close = pd.Series(100.0 * shock, index=idx)
        df = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99,
             "close": close, "volume": 1_000_000.0},
            index=idx,
        )
        histories = {"SPY": df, "QQQ": df.copy()}

        off_bt = PortfolioBacktester(
            histories=histories, initial_capital=100_000, book_vol_target=0.0,
        )
        also_off_bt = PortfolioBacktester(
            histories=histories, initial_capital=100_000,
            book_vol_target=0.0, vol_target_lookback=21,
        )
        pd.testing.assert_series_equal(
            off_bt.run().returns, also_off_bt.run().returns,
        )

    def test_run_shrinks_gross_exposure_in_a_high_vol_book(self):
        rng = np.random.default_rng(3)
        n = 260
        idx = pd.bdate_range("2021-01-01", periods=n, freq="B")
        # Deliberately high-vol synthetic series (~45% annualised) so a 5%
        # target should visibly bind after the lookback warmup.
        shock = np.exp(np.cumsum(rng.normal(0.0, 0.028, n)))
        close = pd.Series(100.0 * shock, index=idx)
        df = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99,
             "close": close, "volume": 1_000_000.0},
            index=idx,
        )
        histories = {"SPY": df, "QQQ": df.copy()}

        baseline = PortfolioBacktester(
            histories=histories, initial_capital=100_000, book_vol_target=0.0,
        ).run()
        targeted = PortfolioBacktester(
            histories=histories, initial_capital=100_000,
            book_vol_target=0.05, vol_target_lookback=21,
        ).run()

        shared = baseline.weights.index.intersection(targeted.weights.index)[21:]
        baseline_gross = baseline.weights.reindex(shared).abs().sum(axis=1)
        targeted_gross = targeted.weights.reindex(shared).abs().sum(axis=1)

        assert targeted_gross.mean() < baseline_gross.mean()
        assert (targeted_gross <= baseline_gross + 1e-9).all()

    def test_live_and_backtest_produce_the_same_scale(self, tmp_path):
        """GOLDEN TEST for the 2026-08-31 vol target: the live loop and the
        backtester must scale the book by the SAME number given the same
        return history.

        The live path reads its window from RiskManager (daily equity closes,
        persisted across the --once restarts); the backtester reads its own
        in-memory portfolio returns. Two sources, one required answer — if
        they can disagree, the deployed book is not the backtested book,
        which is the failure mode analysis_report_2026-08-01_deep_review.md
        Befund 0 was about.
        """
        from core.risk_manager import RiskManager

        rng = np.random.default_rng(31)
        book_returns = [float(x) for x in rng.normal(0.0, 0.30 / (252 ** 0.5), 45)]

        risk = RiskManager(lock_file_path=str(tmp_path / "RISK_HALT.lock"))
        equity = 100_000.0
        risk.end_of_day(equity)
        for r in book_returns:
            equity *= 1.0 + r
            risk.end_of_day(equity)

        bt = PortfolioBacktester(histories={})
        assert bt.book_vol_target == pytest.approx(config.BOOK_VOL_TARGET["target"])
        assert risk.book_vol_scale() == pytest.approx(
            bt._vol_target_scale(risk.book_returns())
        )

    def test_metadata_reports_the_configured_target(self):
        bt = PortfolioBacktester(
            histories={"SPY": _make_data(260)}, book_vol_target=0.20, vol_target_lookback=30,
        )
        result = bt.run()
        assert result.metadata["book_vol_target"] == pytest.approx(0.20)
        assert result.metadata["vol_target_lookback"] == 30
