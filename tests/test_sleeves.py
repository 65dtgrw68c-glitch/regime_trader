"""Tests for core.sleeves — core/sleeve composition of the target book."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import sleeves
from core.risk_manager import RiskManager
from settings import config


@pytest.fixture
def sleeve_config(monkeypatch):
    """A deterministic two-sleeve setup independent of the live config."""
    monkeypatch.setitem(config.UNIVERSE["assets"], "LEV2",
                        {"asset_class": "levered_equity", "validated": True,
                         "role": "sleeve", "leverage": 2.0})
    monkeypatch.setattr(config, "SLEEVES", {
        "core_scale": 0.60,
        "levered": [{"ticker": "LEV2", "signal": "SIG", "weight": 0.40}],
    })
    return config


class TestCompose:
    def test_core_is_scaled_and_sleeve_added_when_signal_in_trend(self, sleeve_config):
        book = sleeves.compose_book({"AAA": 0.50, "BBB": 0.25}, {"SIG": True})
        assert book["AAA"] == pytest.approx(0.30)
        assert book["BBB"] == pytest.approx(0.15)
        assert book["LEV2"] == pytest.approx(0.40)

    def test_sleeve_absent_when_signal_out_of_trend(self, sleeve_config):
        book = sleeves.compose_book({"AAA": 0.50}, {"SIG": False})
        assert "LEV2" not in book
        assert book["AAA"] == pytest.approx(0.30)

    @pytest.mark.parametrize("state", [None, "missing"])
    def test_undefined_signal_never_enters_the_sleeve(self, sleeve_config, state):
        """A levered position must never be opened on an undefined trend."""
        trend = {} if state == "missing" else {"SIG": None}
        assert "LEV2" not in sleeves.compose_book({"AAA": 0.50}, trend)

    def test_gross_cap_applies_to_the_composed_book(self, sleeve_config, monkeypatch):
        """The allocator only ever sees the core, so the cap must bind here."""
        monkeypatch.setitem(config.RISK, "gross_cap", 0.80)
        book = sleeves.compose_book({"AAA": 1.0}, {"SIG": True})
        assert sum(book.values()) == pytest.approx(0.80)
        # ...and the split between core and sleeve is preserved pro rata
        assert book["AAA"] / book["LEV2"] == pytest.approx(0.60 / 0.40)

    def test_core_scale_one_reproduces_the_pure_core_book(self, monkeypatch):
        monkeypatch.setattr(config, "SLEEVES", {"core_scale": 1.0, "levered": []})
        core = {"AAA": 0.5, "BBB": 0.25}
        assert sleeves.compose_book(core, {}) == pytest.approx(core)

    def test_zero_weights_are_dropped(self, sleeve_config):
        assert "AAA" not in sleeves.compose_book({"AAA": 0.0}, {"SIG": False})


class TestSleeveDefinitions:
    def test_unknown_ticker_is_ignored(self, monkeypatch):
        monkeypatch.setattr(config, "SLEEVES", {
            "core_scale": 1.0,
            "levered": [{"ticker": "NOPE", "signal": "SIG", "weight": 0.4}],
        })
        assert sleeves.sleeve_definitions() == []
        assert sleeves.sleeve_weights({"SIG": True}) == {}

    def test_unvalidated_ticker_is_ignored(self, monkeypatch):
        monkeypatch.setitem(config.UNIVERSE["assets"], "OFF",
                            {"asset_class": "equity", "validated": False,
                             "role": "sleeve", "leverage": 2.0})
        monkeypatch.setattr(config, "SLEEVES", {
            "core_scale": 1.0,
            "levered": [{"ticker": "OFF", "signal": "SIG", "weight": 0.4}],
        })
        assert sleeves.sleeve_weights({"SIG": True}) == {}

    def test_signal_tickers_reported(self, sleeve_config):
        assert sleeves.sleeve_signal_tickers() == {"SIG"}


class TestEconomicExposure:
    def test_leverage_factor_is_applied(self, sleeve_config):
        assert sleeves.economic_exposure({"LEV2": 0.40}) == pytest.approx(0.80)

    def test_unlevered_assets_count_once(self):
        assert sleeves.economic_exposure({"SPY": 0.5}) == pytest.approx(0.5)

    def test_unknown_ticker_defaults_to_unlevered(self):
        assert sleeves.economic_exposure({"???": 0.3}) == pytest.approx(0.3)


class TestSleeveExcludedFromAllocator:
    def test_core_universe_excludes_sleeve_role(self):
        """A levered sleeve must never be sized by the allocator: the
        correlation selector would reject it every day (highest vol, ~0.95
        correlated), which is right for a diversifier and wrong for a
        deliberately budgeted beta position."""
        from core.universe import core_universe, tradable_universe

        assert "QLD" in tradable_universe()      # tradable ...
        assert "QLD" not in core_universe()      # ... but not allocator-eligible

    def test_build_views_never_yields_a_sleeve(self):
        import pandas as pd
        from core.universe import build_views

        idx = pd.bdate_range("2021-01-01", periods=120)
        hist = pd.DataFrame({"close": range(100, 220)}, index=idx).astype(float)
        views = build_views({"SPY": hist, "QLD": hist}, {"SPY": True, "QLD": True})
        assert [v.ticker for v in views] == ["SPY"]


class TestDeployedConfigIsCoherent:
    """Guards on the SHIPPED config, so a later edit cannot make the live book
    unrepresentable by the risk layer."""

    def test_every_sleeve_fits_its_class_cap(self):
        caps = config.RISK["class_caps"]
        by_class: dict[str, float] = {}
        for spec in sleeves.sleeve_definitions():
            meta = config.UNIVERSE["assets"][spec["ticker"]]
            cls = meta["asset_class"]
            by_class[cls] = by_class.get(cls, 0.0) + float(spec["weight"])
        for cls, weight in by_class.items():
            assert weight <= caps[cls] + 1e-9, f"{cls} sleeve exceeds its class cap"

    def test_every_sleeve_fits_the_per_name_cap(self):
        cap = config.RISK["per_name_cap"]
        for spec in sleeves.sleeve_definitions():
            assert float(spec["weight"]) <= cap + 1e-9

    def test_maximum_possible_book_passes_the_risk_validator(self):
        """Worst case: core at full gross AND every sleeve on."""
        core = {"SPY": config.RISK["per_name_cap"],
                "GLD": config.UNIVERSE["class_caps"]["gold"],
                "IEF": config.UNIVERSE["class_caps"]["bonds"]}
        book = sleeves.compose_book(core, {"QQQ": True})
        validation = RiskManager(persist_state=False).validate_book(book)
        assert validation.approved, validation.reason

    def test_economic_cap_leaves_headroom_over_the_configured_maximum(self):
        max_economic = (sleeves.core_scale() * config.RISK["gross_cap"]
                        + sum(float(s["weight"])
                              * config.UNIVERSE["assets"][s["ticker"]]["leverage"]
                              for s in sleeves.sleeve_definitions()))
        assert max_economic <= config.RISK["economic_gross_cap"] + 1e-9
