"""Tests for scripts/reconcile.py's deviation classifier.

The reconciliation report's value is entirely in whether its explanations
are right. A report that mislabels a real problem as benign is worse than
no report, because it trains the reader to trust it — and the reverse
failure, tested here explicitly, is just as damaging: a report that flags
ordinary intraday price drift as needing a human teaches the reader to
ignore every alert, real ones included. That exact failure was found by
running the tool against a real paper account while building it (see
PHASE4_READINESS.md) — the first version compared raw share counts and
flagged three positions whose deviation was 0.15-1.45% of position size,
pure price movement since the morning's fill. The fix moved materiality to
portfolio weight; these tests pin that behaviour down.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from reconcile import classify


class TestHaltTakesPrecedence:

    def test_halt_explains_any_gap_even_a_large_one(self):
        _, benign = classify(
            diff_shares=-100.0, diff_weight=-0.40,
            open_order_qty=0.0, halted=True, market_open=True,
            weight_tolerance=0.01,
        )
        assert benign, "a flat book under an active HALT is expected, not a fault"


class TestWeightMateriality:
    """The bug: a share-count tolerance cannot tell 'the price moved since
    this morning' apart from 'something is wrong', because the bot
    rebalances once a day while the market value moves continuously.
    Materiality must be judged on portfolio weight instead."""

    def test_small_weight_drift_while_market_open_is_benign(self):
        # ~0.58% of equity — the actual magnitude of the QLD deviation that
        # triggered this fix (446 target vs 439.7 actual, mid-session).
        text, benign = classify(
            diff_shares=-6.48, diff_weight=-0.0058,
            open_order_qty=0.0, halted=False, market_open=True,
            weight_tolerance=0.01,
        )
        assert benign, "small price-driven drift must not be flagged"
        assert "normal drift" in text

    def test_weight_drift_at_the_tolerance_boundary_is_benign(self):
        _, benign = classify(
            diff_shares=-1.0, diff_weight=-0.0099,
            open_order_qty=0.0, halted=False, market_open=True,
            weight_tolerance=0.01,
        )
        assert benign

    def test_weight_drift_over_the_tolerance_is_not_benign(self):
        _, benign = classify(
            diff_shares=-50.0, diff_weight=-0.115,
            open_order_qty=0.0, halted=False, market_open=True,
            weight_tolerance=0.01,
        )
        assert not benign

    def test_no_price_available_falls_back_to_share_based_reasoning(self):
        # diff_weight=None (couldn't price it) with a large share gap and no
        # open order, market closed: must still escalate, not silently pass.
        _, benign = classify(
            diff_shares=25.0, diff_weight=None,
            open_order_qty=0.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert not benign


class TestInFlightOrders:
    """Once a deviation clears the materiality bar, an open order can still
    explain it — but only if it points the right way."""

    def test_open_buy_that_closes_the_gap_is_benign(self):
        # Short 10 shares (material), an open buy for 10 is on the way.
        text, benign = classify(
            diff_shares=-10.0, diff_weight=-0.05,
            open_order_qty=10.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert benign
        assert "in flight" in text

    def test_open_sell_that_closes_the_gap_is_benign(self):
        # Holding 10 too many (material), an open sell for 10 is on the way.
        text, benign = classify(
            diff_shares=10.0, diff_weight=0.05,
            open_order_qty=-10.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert benign
        assert "in flight" in text

    def test_open_order_in_the_wrong_direction_is_not_benign(self):
        # Short 10, but the open order is ALSO a sell — it widens the gap.
        _, benign = classify(
            diff_shares=-10.0, diff_weight=-0.05,
            open_order_qty=-10.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert not benign, "an order moving away from target must not read as benign"

    def test_partial_cover_is_not_benign(self):
        text, benign = classify(
            diff_shares=-10.0, diff_weight=-0.05,
            open_order_qty=3.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert not benign
        assert "partially in flight" in text


class TestUnexplainedCases:

    def test_material_gap_while_market_open_with_no_order_needs_a_human(self):
        _, benign = classify(
            diff_shares=-25.0, diff_weight=-0.05,
            open_order_qty=0.0, halted=False, market_open=True,
            weight_tolerance=0.01,
        )
        assert not benign

    def test_missing_position_after_close_needs_a_human(self):
        text, benign = classify(
            diff_shares=-10.0, diff_weight=-0.05,
            open_order_qty=0.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert not benign
        assert "short of target" in text

    def test_unwanted_holding_after_close_needs_a_human(self):
        text, benign = classify(
            diff_shares=7.0, diff_weight=0.05,
            open_order_qty=0.0, halted=False, market_open=False,
            weight_tolerance=0.01,
        )
        assert not benign
        assert "more than the target book wants" in text
