"""
Dashboard — Streamlit monitoring app (Prompt 8).

Layout
------
TOP     : key metrics (regime, confidence, portfolio value, buying power,
          #regimes, #open positions)
MIDDLE  : price chart with regime overlays, volume, confidence-over-time,
          regime distribution
BOTTOM-L: risk controls (circuit breakers, drawdown vs limit, leverage)
BOTTOM-R: signal feed / trade-history table
BOTTOM  : regime reference table (allocation %, leverage, strategy per regime)

Launch
------
    pip install -r requirements.txt
    streamlit run monitoring/dashboard.py

`streamlit` and `plotly` are imported lazily inside the render functions so
this module imports cleanly (and the rest of the test suite runs) even when
those UI dependencies are absent.  All data-shaping helpers are pure and
unit-tested in tests/test_monitoring.py.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from core.regime_strategies import (
    LABEL_TO_TIER,
    REGIME_PARAMS,
    TIER_COLOUR,
    VolTier,
)
from settings import config


# ===========================================================================
# Pure data-shaping helpers (testable without Streamlit)
# ===========================================================================

def regime_colour(regime_label: str) -> str:
    """Hex colour for a regime's volatility tier."""
    tier = LABEL_TO_TIER.get(regime_label, VolTier.MED)
    return TIER_COLOUR[tier]


def build_equity_frame(equity_curve: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": equity_curve.index, "equity": equity_curve.values})


def build_positions_frame(positions: dict) -> pd.DataFrame:
    """Shape a positions dict into a colour-coded table frame."""
    rows = []
    for ticker, p in positions.items():
        qty = getattr(p, "qty", p.get("qty") if isinstance(p, dict) else 0)
        pnl = getattr(p, "unrealised_pnl",
                      p.get("unrealised_pnl") if isinstance(p, dict) else 0.0)
        rows.append({
            "ticker": ticker,
            "qty": qty,
            "unrealised_pnl": pnl,
            "direction": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
        })
    return pd.DataFrame(rows)


def drawdown_pct(current_equity: float, peak_equity: float) -> float:
    """Current drawdown from peak as a percentage (<= 0)."""
    if peak_equity <= 0:
        return 0.0
    return (current_equity - peak_equity) / peak_equity * 100.0


def compute_regime_spans(regime_series: pd.Series) -> list[tuple]:
    """
    Collapse a per-bar regime series into contiguous (start, end, regime)
    spans — used to draw colour-coded background bands on the price chart.
    """
    if regime_series is None or len(regime_series) == 0:
        return []
    spans = []
    idx = list(regime_series.index)
    values = list(regime_series.values)
    span_start = idx[0]
    current = values[0]
    for i in range(1, len(values)):
        if values[i] != current:
            spans.append((span_start, idx[i - 1], current))
            span_start = idx[i]
            current = values[i]
    spans.append((span_start, idx[-1], current))
    return spans


def build_regime_distribution(regime_series: pd.Series) -> pd.DataFrame:
    """Percentage of time spent in each regime (historical distribution)."""
    if regime_series is None or len(regime_series) == 0:
        return pd.DataFrame(columns=["regime", "pct"])
    counts = regime_series.value_counts(normalize=True) * 100.0
    return pd.DataFrame({"regime": counts.index, "pct": counts.values})


def build_signal_feed_table(signals: list[dict]) -> pd.DataFrame:
    """
    Build the trade-history / signal-feed table with the required columns.
    Accepts a list of signal dicts; missing fields render blank.
    """
    columns = [
        "timestamp", "ticker", "direction", "regime", "allocation_pct",
        "entry_price", "stop_price", "pnl", "status",
    ]
    if not signals:
        return pd.DataFrame(columns=columns)
    rows = [{c: s.get(c, "") for c in columns} for s in signals]
    return pd.DataFrame(rows, columns=columns)


def build_regime_reference_table() -> pd.DataFrame:
    """
    Reference table of every configured regime: name, allocation %, leverage
    allowed, and the strategy description for that regime.
    """
    caps = getattr(config, "REGIME_LEVERAGE_CAPS", {})
    rows = []
    for label, tier in LABEL_TO_TIER.items():
        params = REGIME_PARAMS[tier]
        rows.append({
            "regime": label,
            "tier": tier.value,
            "allocation_pct": round(params.allocation_pct * 100, 1),
            "leverage": caps.get(label, params.max_leverage),
            "strategy": params.rationale,
        })
    # Order by allocation descending so aggressive regimes sit on top.
    return pd.DataFrame(rows).sort_values(
        "allocation_pct", ascending=False
    ).reset_index(drop=True)


def circuit_breaker_status(cb_level_name: str) -> dict:
    """Map a CBLevel name to a colour-coded status for each breaker light."""
    severity = {"NONE": 0, "HALVE": 1, "WEEKLY_RESIZE": 2, "FLATTEN": 3, "HALT": 4}
    active = severity.get(cb_level_name, 0)
    return {
        "halve":         "red" if active >= 1 else "green",
        "weekly_resize": "red" if active >= 2 else "green",
        "flatten":       "red" if active >= 3 else "green",
        "halt":          "red" if active >= 4 else "green",
    }


# ===========================================================================
# Streamlit render sections
# ===========================================================================

def render_header(st) -> None:                                 # pragma: no cover - UI
    from datetime import datetime, timezone
    st.title("regime_trader — Live Monitor")
    st.caption(f"Last updated: {datetime.now(timezone.utc).isoformat()}")


def render_top_metrics(st, state: dict) -> None:               # pragma: no cover - UI
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    regime = state.get("regime_label", "Unknown")
    c1.markdown(f"**Regime**<br><span style='color:{regime_colour(regime)};font-size:1.4em'>"
                f"{regime}</span>", unsafe_allow_html=True)
    c2.metric("Confidence", f"{state.get('confidence', 0.0)*100:.1f}%")
    c3.metric("Portfolio", f"${state.get('portfolio_value', 0.0):,.0f}")
    c4.metric("Buying Power", f"${state.get('buying_power', 0.0):,.0f}")
    c5.metric("# Regimes", state.get("n_regimes", 0))
    c6.metric("Open Positions", len(state.get("positions", {})))


def render_price_with_regimes(st, price_df: pd.DataFrame,    # pragma: no cover - UI
                              regime_series: Optional[pd.Series]) -> None:
    import plotly.graph_objects as go
    st.subheader("Price Action with Regime Overlay")
    fig = go.Figure(go.Scatter(x=price_df.index, y=price_df["close"],
                               mode="lines", name="close"))
    if regime_series is not None:
        for start, end, regime in compute_regime_spans(regime_series):
            fig.add_vrect(x0=start, x1=end, fillcolor=regime_colour(str(regime)),
                          opacity=0.12, line_width=0)
    st.plotly_chart(fig, use_container_width=True)


def render_volume(st, price_df: pd.DataFrame) -> None:         # pragma: no cover - UI
    st.subheader("Volume")
    if "volume" in price_df.columns:
        st.bar_chart(price_df["volume"])


def render_confidence_over_time(st, confidence: pd.Series) -> None:  # pragma: no cover - UI
    st.subheader("Regime Confidence Over Time")
    if confidence is not None and len(confidence):
        st.line_chart(confidence)


def render_regime_distribution(st, regime_series: pd.Series) -> None:  # pragma: no cover - UI
    st.subheader("Regime Distribution")
    dist = build_regime_distribution(regime_series)
    if not dist.empty:
        st.bar_chart(dist.set_index("regime")["pct"])


def render_risk_panel(st, state: dict) -> None:                # pragma: no cover - UI
    st.subheader("Risk Controls")
    status = circuit_breaker_status(state.get("cb_level", "NONE"))
    for name, colour in status.items():
        light = "🟢" if colour == "green" else "🔴"
        st.write(f"{light} {name.replace('_', ' ').title()}")
    dd = drawdown_pct(state.get("current_equity", 0.0), state.get("peak_equity", 0.0))
    st.metric("Drawdown", f"{dd:.2f}%",
              f"limit -{config.RISK['cb_max_drawdown_halt']*100:.0f}%")
    st.metric("Leverage", f"{state.get('leverage', 0.0):.2f}x")


def render_signal_feed(st, signals: list) -> None:             # pragma: no cover - UI
    st.subheader("Signal Feed / Trade History")
    table = build_signal_feed_table(signals or [])
    if table.empty:
        st.info("No signals yet.")
    else:
        st.dataframe(table, use_container_width=True)


def render_regime_reference(st) -> None:                       # pragma: no cover - UI
    st.subheader("Regime Reference")
    st.dataframe(build_regime_reference_table(), use_container_width=True)


def render_metrics_summary(st, metrics: dict) -> None:         # pragma: no cover - UI
    """Optional performance summary strip (demo mode)."""
    if not metrics:
        return
    st.subheader("Performance Summary")
    cols = st.columns(5)
    cols[0].metric("Total Return", f"{metrics.get('total_return', 0.0)*100:.1f}%")
    cols[1].metric("Sharpe", f"{metrics.get('sharpe_ratio', 0.0):.2f}")
    cols[2].metric("Max Drawdown", f"{metrics.get('max_drawdown', 0.0)*100:.1f}%")
    cols[3].metric("Win Rate", f"{metrics.get('win_rate', 0.0)*100:.1f}%")
    cols[4].metric("# Trades", metrics.get("num_trades", 0))


def render_sidebar(st):                                        # pragma: no cover - UI
    """Sidebar controls; returns (mode, ticker, seed)."""
    st.sidebar.title("⚙️ Controls")
    mode_label = st.sidebar.radio(
        "Data source",
        ["Demo (synthetic)", "Live (Alpaca paper)"],
        help="Live mode needs valid .env credentials; it falls back to demo "
             "if the connection fails.",
    )
    mode = "live" if mode_label.startswith("Live") else "demo"
    default_ticker = (config.TICKERS[0] if config.TICKERS else "DEMO")
    ticker = st.sidebar.text_input("Ticker", value=default_ticker)
    seed = st.sidebar.number_input("Demo seed", min_value=0, max_value=9999, value=7, step=1)
    if st.sidebar.button("🔄 Refresh data"):
        st.cache_data.clear()
    st.sidebar.caption("regime_trader monitoring UI")
    return mode, ticker, int(seed)


# ===========================================================================
# Entry point
# ===========================================================================

def main(state: Optional[dict] = None) -> None:                # pragma: no cover - UI
    import streamlit as st
    from monitoring import dashboard_data

    st.set_page_config(page_title="regime_trader", layout="wide")

    # When no state is injected (the normal `streamlit run` case), load it
    # from the data provider based on the sidebar selection.  Demo runs are
    # cached so the backtest only recomputes when inputs change.
    if state is None:
        mode, ticker, seed = render_sidebar(st)
        loader = st.cache_data(dashboard_data.load_state)
        with st.spinner(f"Loading {mode} data for {ticker}..."):
            state = loader(mode, ticker, seed)
        if state.get("mode") == "demo" and mode == "live":
            st.sidebar.warning("Live data unavailable — showing demo data.")

    render_header(st)
    st.caption(f"Data source: **{state.get('mode', 'n/a')}**")

    # TOP — key metrics
    render_top_metrics(st, state)
    render_metrics_summary(st, state.get("metrics", {}))

    # MIDDLE — charts
    if "price_df" in state and state["price_df"] is not None:
        col_a, col_b = st.columns(2)
        with col_a:
            render_price_with_regimes(st, state["price_df"], state.get("regime_history"))
            render_volume(st, state["price_df"])
        with col_b:
            render_confidence_over_time(st, state.get("confidence_series"))
            render_regime_distribution(st, state.get("regime_history"))

    # BOTTOM — risk + signal feed
    left, right = st.columns(2)
    with left:
        render_risk_panel(st, state)
    with right:
        render_signal_feed(st, state.get("signals"))

    # BOTTOM PANEL — regime reference
    render_regime_reference(st)


if __name__ == "__main__":                                     # pragma: no cover
    main()
