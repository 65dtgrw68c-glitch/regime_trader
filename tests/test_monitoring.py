"""
Tests for the monitoring layer (Prompt 8):
  * monitoring/logger.py   — TradeLogger event rows + slippage
  * monitoring/alerts.py   — dedicated critical-event triggers + thresholds
  * monitoring/dashboard.py— pure data-shaping helpers

Run with:  pytest tests/test_monitoring.py -v
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitoring.alerts import (
    AlertManager, SEVERITY_CRITICAL, SEVERITY_WARNING,
)
from monitoring.logger import TradeLogger
from monitoring import dashboard as dash
from monitoring import dashboard_data as dd


# ---------------------------------------------------------------------------
# TradeLogger
# ---------------------------------------------------------------------------

def _read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class TestTradeLogger:

    def test_header_written_on_create(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        assert tl.path.exists()
        rows = _read_rows(tl.path)
        assert rows == []   # header only, no data rows yet

    def test_log_signal_row(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_signal("NVDA", "buy", "Bull", 95.0, 0.82)
        rows = _read_rows(tl.path)
        assert len(rows) == 1
        assert rows[0]["event"] == "SIGNAL"
        assert rows[0]["ticker"] == "NVDA"
        assert rows[0]["direction"] == "buy"
        assert rows[0]["regime"] == "Bull"

    def test_log_regime_change_records_prev(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_regime_change("Bear", "Bull", confidence=0.7)
        row = _read_rows(tl.path)[0]
        assert row["event"] == "REGIME_CHANGE"
        assert row["regime"] == "Bull"
        assert row["prev_regime"] == "Bear"

    def test_log_fill_computes_slippage(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_fill({"ticker": "NVDA", "fill_price": 101.0, "expected_price": 100.0})
        row = _read_rows(tl.path)[0]
        assert row["event"] == "FILL"
        assert float(row["slippage"]) == pytest.approx(1.0)

    def test_log_fill_without_expected_no_slippage(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_fill({"ticker": "NVDA", "fill_price": 101.0})
        row = _read_rows(tl.path)[0]
        assert row["slippage"] == ""

    def test_log_circuit_breaker_row(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_circuit_breaker("FLATTEN", True, {"daily": -0.031})
        row = _read_rows(tl.path)[0]
        assert row["event"] == "CIRCUIT_BREAKER"
        assert row["cb_level"] == "FLATTEN"
        assert row["cb_triggered"] == "True"

    def test_order_row_has_type(self, tmp_path):
        tl = TradeLogger(path=str(tmp_path / "t.csv"))
        tl.log_order({"ticker": "NVDA", "side": "buy", "qty": 5,
                      "price": 100.0, "order_id": "x1", "order_type": "limit"})
        row = _read_rows(tl.path)[0]
        assert row["event"] == "ORDER"
        assert row["order_type"] == "limit"


# ---------------------------------------------------------------------------
# AlertManager — dedicated triggers + thresholds
# ---------------------------------------------------------------------------

class TestAlertTriggers:

    def _am(self, cooldown=300):
        return AlertManager(email_recipients=[], webhook_url="", cooldown_seconds=cooldown)

    def test_circuit_breaker_alert_fires(self):
        assert self._am().alert_circuit_breaker("HALT") is True

    def test_daily_drawdown_alert_fires(self):
        assert self._am().alert_daily_drawdown(-0.03) is True

    def test_lock_file_alert_fires(self):
        assert self._am().alert_lock_file({"drawdown": -0.10}) is True

    def test_api_connection_alert_fires(self):
        assert self._am().alert_api_connection_lost() is True

    def test_circuit_breaker_alert_throttled_by_level(self):
        am = self._am()
        assert am.alert_circuit_breaker("HALVE") is True
        assert am.alert_circuit_breaker("HALVE") is False   # same key throttled

    def test_should_alert_drawdown_true(self):
        assert AlertManager.should_alert_drawdown(-0.03, 0.02) is True

    def test_should_alert_drawdown_false(self):
        assert AlertManager.should_alert_drawdown(-0.01, 0.02) is False

    def test_disabled_manager_sends_nothing(self):
        am = AlertManager(email_recipients=[], webhook_url="",
                          cooldown_seconds=300, enabled=False)
        assert am.alert("x", SEVERITY_CRITICAL) is False


# ---------------------------------------------------------------------------
# Dashboard pure helpers
# ---------------------------------------------------------------------------

class TestDashboardHelpers:

    def test_regime_colour_known(self):
        assert dash.regime_colour("Bull").startswith("#")

    def test_drawdown_pct(self):
        assert dash.drawdown_pct(90.0, 100.0) == pytest.approx(-10.0)

    def test_drawdown_pct_zero_peak(self):
        assert dash.drawdown_pct(0.0, 0.0) == 0.0

    def test_compute_regime_spans(self):
        s = pd.Series(["Bull", "Bull", "Bear", "Bear", "Bull"], index=[0, 1, 2, 3, 4])
        spans = dash.compute_regime_spans(s)
        assert spans == [(0, 1, "Bull"), (2, 3, "Bear"), (4, 4, "Bull")]

    def test_compute_regime_spans_empty(self):
        assert dash.compute_regime_spans(pd.Series([], dtype=object)) == []

    def test_regime_distribution_sums_to_100(self):
        s = pd.Series(["Bull", "Bull", "Bear", "Neutral"])
        dist = dash.build_regime_distribution(s)
        assert dist["pct"].sum() == pytest.approx(100.0)

    def test_regime_reference_table_has_all_regimes(self):
        from core.regime_strategies import LABEL_TO_TIER
        table = dash.build_regime_reference_table()
        assert set(table["regime"]) == set(LABEL_TO_TIER.keys())
        assert {"allocation_pct", "leverage", "strategy"}.issubset(table.columns)

    def test_signal_feed_table_columns(self):
        table = dash.build_signal_feed_table([
            {"timestamp": "t", "ticker": "NVDA", "direction": "buy",
             "regime": "Bull", "allocation_pct": 95, "entry_price": 100,
             "stop_price": 98, "pnl": 5, "status": "open"},
        ])
        assert list(table.columns) == [
            "timestamp", "ticker", "direction", "regime", "allocation_pct",
            "entry_price", "stop_price", "pnl", "status",
        ]
        assert len(table) == 1

    def test_signal_feed_empty(self):
        table = dash.build_signal_feed_table([])
        assert table.empty

    def test_circuit_breaker_status_none_all_green(self):
        status = dash.circuit_breaker_status("NONE")
        assert all(v == "green" for v in status.values())

    def test_circuit_breaker_status_halt_all_red(self):
        status = dash.circuit_breaker_status("HALT")
        assert all(v == "red" for v in status.values())

    def test_circuit_breaker_status_halve_partial(self):
        status = dash.circuit_breaker_status("HALVE")
        assert status["halve"] == "red"
        assert status["halt"] == "green"

    def test_build_positions_frame(self):
        frame = dash.build_positions_frame({
            "A": {"qty": 10, "unrealised_pnl": 50.0},
            "B": {"qty": -3, "unrealised_pnl": -10.0},
        })
        assert set(frame["ticker"]) == {"A", "B"}
        directions = dict(zip(frame["ticker"], frame["direction"]))
        assert directions["A"] == "LONG"
        assert directions["B"] == "SHORT"


# ---------------------------------------------------------------------------
# Dashboard data provider (pure helpers)
# ---------------------------------------------------------------------------

class TestDashboardData:

    def test_generate_demo_ohlcv_shape(self):
        df = dd.generate_demo_ohlcv(n=300, seed=1)
        assert len(df) == 300
        assert set(["open", "high", "low", "close", "volume"]).issubset(df.columns)

    def test_generate_demo_ohlcv_reproducible(self):
        a = dd.generate_demo_ohlcv(n=120, seed=42)
        b = dd.generate_demo_ohlcv(n=120, seed=42)
        pd.testing.assert_frame_equal(a, b)

    def test_generate_demo_ohlcv_high_ge_low(self):
        df = dd.generate_demo_ohlcv(n=200, seed=3)
        assert (df["high"] >= df["low"]).all()

    def test_assemble_signals_empty(self):
        assert dd.assemble_signals(pd.DataFrame()) == []

    def test_assemble_signals_buy_stop_below_entry(self):
        log = pd.DataFrame([{
            "timestamp": "2022-01-01", "ticker": "NVDA", "side": "buy",
            "fill_price": 100.0, "regime": "Bull", "confidence": 0.8,
        }])
        rows = dd.assemble_signals(log, stop_loss_pct=0.02)
        assert len(rows) == 1
        r = rows[0]
        assert r["direction"] == "buy"
        assert r["entry_price"] == pytest.approx(100.0)
        assert r["stop_price"] == pytest.approx(98.0)   # 2% below entry
        assert r["allocation_pct"] == pytest.approx(80.0)

    def test_assemble_signals_sell_stop_above_entry(self):
        log = pd.DataFrame([{
            "timestamp": "2022-01-01", "ticker": "NVDA", "side": "sell",
            "fill_price": 100.0, "regime": "Bear", "confidence": 0.6,
        }])
        rows = dd.assemble_signals(log, stop_loss_pct=0.02)
        assert rows[0]["stop_price"] == pytest.approx(102.0)   # 2% above entry

    def test_assemble_signals_columns_match_feed_table(self):
        log = pd.DataFrame([{
            "timestamp": "t", "ticker": "X", "side": "buy",
            "fill_price": 50.0, "regime": "Neutral", "confidence": 0.5,
        }])
        rows = dd.assemble_signals(log)
        # Feeding into the table builder must produce the canonical columns.
        table = dash.build_signal_feed_table(rows)
        assert list(table.columns) == [
            "timestamp", "ticker", "direction", "regime", "allocation_pct",
            "entry_price", "stop_price", "pnl", "status",
        ]
