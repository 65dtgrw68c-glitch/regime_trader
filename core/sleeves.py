"""Sleeve composition — turns the allocator's core book into the final target book.

The final book is

    target = core_scale * core_book  +  Σ levered sleeves

where each levered sleeve holds a FIXED notional weight in a levered ETF while
its UNLEVERED signal asset is in trend, and nothing otherwise.

Why this lives in its own module
--------------------------------
`main.py` (live) and `core/portfolio_backtester.py` (evaluation) must build the
target book from ONE piece of code.  The 2026-08-01 review found the live path
had silently diverged from the validated one — the live loop ran a completely
different strategy than every published report described.  Both callers now go
through `compose_book()`, so that class of drift cannot recur unnoticed: a
change here changes both, or neither.

Sleeves are deliberately NOT routed through the correlation selector.  A 2x QQQ
ETF is ~0.95 correlated with the equity core and carries the highest volatility
in the universe, so the selector would reject it every single day — correct
behaviour for a diversification candidate, wrong for a separately budgeted beta
position that is in the book on purpose.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, Mapping, Optional, Sequence

from settings import config

logger = logging.getLogger(__name__)


def sleeve_definitions() -> list[dict]:
    """Configured levered sleeves whose ticker is a validated universe asset."""
    assets = config.UNIVERSE.get("assets", {})
    out = []
    for spec in config.SLEEVES.get("levered", []):
        ticker = spec.get("ticker")
        meta = assets.get(ticker)
        if meta is None:
            logger.warning("Sleeve %s is not in UNIVERSE['assets'] — ignored.", ticker)
            continue
        if not meta.get("validated", False):
            logger.warning("Sleeve %s is not validated — ignored.", ticker)
            continue
        out.append(spec)
    return out


def core_scale() -> float:
    """Multiplier applied to the allocator's core book (1.0 = pure core)."""
    return float(config.SLEEVES.get("core_scale", 1.0))


def sleeve_signal_tickers() -> set[str]:
    """Tickers whose trend state the sleeves need (may differ from what they trade)."""
    return {s["signal"] for s in sleeve_definitions() if s.get("signal")}


def sleeve_weights(trend_states: Mapping[str, Optional[bool]]) -> Dict[str, float]:
    """
    Target weights contributed by the levered sleeves.

    `trend_states` maps ticker -> is_trend_confirmed() output. A signal that is
    None (not enough history) or missing yields NO position: a levered sleeve
    must never be entered on an undefined trend.
    """
    out: Dict[str, float] = {}
    for spec in sleeve_definitions():
        signal = spec.get("signal")
        state = trend_states.get(signal)
        if state is not True:
            continue
        weight = float(spec.get("weight", 0.0))
        if weight > 0:
            out[spec["ticker"]] = out.get(spec["ticker"], 0.0) + weight
    return out


def book_vol_target() -> float:
    """Configured annualised book vol target (0.0 = mechanism off)."""
    cfg = getattr(config, "BOOK_VOL_TARGET", {}) or {}
    return max(0.0, float(cfg.get("target", 0.0)))


def vol_target_lookback() -> int:
    """Trading days of the book's own returns used to estimate realised vol."""
    cfg = getattr(config, "BOOK_VOL_TARGET", {}) or {}
    return max(1, int(cfg.get("lookback", 21)))


def vol_target_scale(
    realised_returns: Sequence[float],
    target: Optional[float] = None,
    lookback: Optional[int] = None,
) -> float:
    """Scale applied to every target weight: min(1, target / realised_vol).

    `realised_returns` is the BOOK's own trailing daily return history, most
    recent last, up to but NOT including the bar being decided — so the scale
    is causal by construction, in the backtest and live alike.

    Returns 1.0 (no scaling) when the mechanism is off, when fewer than
    `lookback` returns exist, and whenever realised vol is degenerate
    (zero/undefined) — a cold start or a dead-flat window must never
    manufacture leverage.

    This lives here, next to compose_book(), for the same reason compose_book()
    does: main.py (live) and core/portfolio_backtester.py (evaluation) must
    scale the book with ONE piece of code.  The 2026-08-01 review found the
    live path had silently diverged from the validated one; a vol target that
    existed only in the backtester would have been that same bug, pre-shipped.
    """
    tgt = book_vol_target() if target is None else max(0.0, float(target))
    if tgt <= 0:
        return 1.0

    window_n = vol_target_lookback() if lookback is None else max(1, int(lookback))
    if len(realised_returns) < window_n:
        return 1.0

    window = [float(x) for x in realised_returns[-window_n:]]
    if len(window) < 2:
        return 1.0

    mean = sum(window) / len(window)
    var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)   # ddof=1
    realised_vol = math.sqrt(var) * math.sqrt(252.0)
    if not realised_vol > 0:
        return 1.0

    return min(1.0, tgt / realised_vol)


def compose_book(
    core_weights: Mapping[str, float],
    trend_states: Mapping[str, Optional[bool]],
) -> Dict[str, float]:
    """
    Compose the final target book from the allocator's core weights and the
    configured sleeves.  Enforces the notional gross cap on the RESULT — the
    allocator only ever saw the core, so it cannot know about the sleeves.
    """
    scale = core_scale()
    book: Dict[str, float] = {
        ticker: float(weight) * scale for ticker, weight in core_weights.items()
    }
    for ticker, weight in sleeve_weights(trend_states).items():
        book[ticker] = book.get(ticker, 0.0) + weight

    gross_cap = float(config.RISK.get("gross_cap", 1.0))
    gross = sum(abs(w) for w in book.values())
    if gross > gross_cap and gross > 0:
        factor = gross_cap / gross
        book = {t: w * factor for t, w in book.items()}

    return {t: round(w, 6) for t, w in book.items() if abs(w) > 1e-9}


def economic_exposure(weights: Mapping[str, float]) -> float:
    """Σ |weight| * leverage — true market exposure behind the notional book."""
    assets = config.UNIVERSE.get("assets", {})
    total = 0.0
    for ticker, weight in weights.items():
        lev = float(assets.get(ticker, {}).get("leverage", 1.0))
        total += abs(float(weight)) * lev
    return total
