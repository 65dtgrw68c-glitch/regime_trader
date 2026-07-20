from __future__ import annotations

import pandas as pd
import numpy as np

from core.universe import AssetView
from core.allocator import target_weights
from settings import config


def _make_hist(vol=0.01, n=100):
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0004, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame({"close": close})


def test_allocator_respects_caps(monkeypatch):
    # temporary minimal universe via config
    cfg_assets = {
        "A": {"asset_class": "equity", "validated": True},
        "B": {"asset_class": "equity", "validated": True},
        "G": {"asset_class": "gold", "validated": True},
    }
    monkeypatch.setitem(config.UNIVERSE, "assets", cfg_assets)
    # set class caps and risk caps
    monkeypatch.setitem(config.RISK, "per_name_cap", 0.4)
    monkeypatch.setitem(config.RISK, "gross_cap", 0.8)
    monkeypatch.setitem(config.RISK, "class_caps", {"equity": 0.6, "gold": 0.2})

    views = [
        AssetView("A", "equity", True, 0.10),
        AssetView("B", "equity", True, 0.20),
        AssetView("G", "gold", True, 0.15),
    ]
    w = target_weights(views)
    # per-name cap respected
    assert all(v <= 0.4 + 1e-9 for v in w.values())
    # class caps not exceeded
    equity_sum = sum(w.get(t, 0.0) for t in ("A", "B"))
    assert equity_sum <= 0.6 + 1e-9
    gold_sum = w.get("G", 0.0)
    assert gold_sum <= 0.2 + 1e-9
    # gross cap respected
    assert sum(abs(v) for v in w.values()) <= 0.8 + 1e-9


def test_allocator_empty_when_no_trend():
    views = [AssetView("A", "equity", False, 0.1)]
    w = target_weights(views)
    assert w == {}

