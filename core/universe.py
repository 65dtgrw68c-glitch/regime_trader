"""Universe helpers for validated assets and trend-based selector views."""
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from core.regime_strategies import realised_vol_from_close
from settings import config


@dataclass
class AssetView:
    """A single asset's current trading view for the allocator."""
    ticker: str
    asset_class: str
    in_trend: bool
    realised_vol: Optional[float]


def tradable_universe() -> Dict[str, dict]:
    """Return only assets explicitly marked as validated in config."""
    return {
        ticker: meta
        for ticker, meta in config.UNIVERSE.get("assets", {}).items()
        if meta.get("validated", False)
    }


def build_views(
    histories: Dict[str, pd.DataFrame],
    trend_states: Dict[str, bool],
    vol_lookback: Optional[int] = None,
) -> List[AssetView]:
    """Collect selector views from per-ticker histories and trend states.

    Returns a list of AssetView for all validated assets present in
    `config.UNIVERSE` that also have history provided.
    """
    views: List[AssetView] = []
    window = int(vol_lookback or config.UNIVERSE.get("vol_lookback", 63))
    for ticker, meta in tradable_universe().items():
        hist = histories.get(ticker)
        if hist is None or hist.empty:
            continue
        closes = pd.Series(hist["close"]).dropna()
        try:
            vol = realised_vol_from_close(closes, window=window)
        except Exception:
            vol = None
        views.append(
            AssetView(
                ticker=ticker,
                asset_class=meta.get("asset_class", "equity"),
                in_trend=bool(trend_states.get(ticker, False)),
                realised_vol=vol,
            )
        )
    return views
