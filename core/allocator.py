"""Simple class-budgeted inverse-vol allocator.

Implements the target_weights function described in the review: inverse-vol
within class budgets, respects per-name caps and a gross cap.
"""
from collections import defaultdict
import math
from typing import Dict, List

from core.universe import AssetView
from settings import config


def target_weights(views: List[AssetView]) -> Dict[str, float]:
    per_name = float(config.RISK.get("per_name_cap", config.RISK.get("max_position_size", 1.0)))
    gross = float(config.RISK.get("gross_cap", 1.0))
    class_caps = config.RISK.get("class_caps", config.UNIVERSE.get("class_caps", {}))
    by_class: Dict[str, List[AssetView]] = defaultdict(list)
    for v in views:
        if v.in_trend and v.realised_vol is not None and not math.isnan(v.realised_vol):
            by_class[v.asset_class].append(v)

    weights: Dict[str, float] = {}
    for cls, members in by_class.items():
        inv = {v.ticker: 1.0 / max(v.realised_vol, 0.05) for v in members}
        total = sum(inv.values())
        if total <= 0:
            continue
        budget = float(class_caps.get(cls, 0.0))
        for t, iv in inv.items():
            w = budget * iv / total
            # enforce per-name cap
            weights[t] = min(per_name, w)

    s = sum(abs(w) for w in weights.values())
    if s > gross and s > 0:
        factor = gross / s
        weights = {t: w * factor for t, w in weights.items()}

    # round small floating noise
    return {t: round(w, 6) for t, w in weights.items()}
