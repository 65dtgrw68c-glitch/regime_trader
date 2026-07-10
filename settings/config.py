"""
Central configuration file for regime_trader.

All tuneable parameters live here so every module imports from a single
source of truth instead of scattering magic numbers across the codebase.
"""

# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------
# List of symbols the system is allowed to trade.
# Only tickers the pinned strategy profile (see ORCHESTRATOR below) has been
# validated on — SPY and QQQ passed the walk-forward grid; on the never-tuned
# IWM the profile stayed ~flat while pure trend lost -23%, i.e. it degrades
# gracefully where trend has no edge. AAPL/MSFT/NVDA were removed 2026-07-09:
# single-name trend following was never backtested here — run the experiment
# grid on a name BEFORE adding it back.
TICKERS = [
    "SPY",
    "QQQ",
]

# ---------------------------------------------------------------------------
# Broker / Alpaca settings
# ---------------------------------------------------------------------------
BROKER = {
    # Which broker backend: "alpaca" (US) or "ibkr" (Phase 2, EU).
    "provider": "alpaca",
    # "paper" or "live"
    "mode": "paper",
    # Maximum number of API retries before raising
    "max_retries": 3,
    # Seconds to wait between retries
    "retry_delay": 2,
    # Only submit orders while the exchange is open (else just log the decision).
    "trade_only_when_open": True,
}

# ---------------------------------------------------------------------------
# HMM parameters
# ---------------------------------------------------------------------------
HMM = {
    # Number of hidden states (market regimes)
    "n_components": 3,
    # Covariance type: "full", "tied", "diag", "spherical"
    "covariance_type": "full",
    # EM algorithm iterations
    "n_iter": 100,
    # Random seed for reproducibility
    "random_state": 42,
    # Minimum history (bars) required before fitting
    "min_history_bars": 252,
    # How often to refit the model (in bars)
    "refit_interval_bars": 21,
}

# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
STRATEGY = {
    # Mapping of regime label -> strategy name
    # Labels assigned after HMM fit (e.g. 0=trending, 1=mean-revert, 2=risk-off)
    "regime_map": {
        0: "trend_following",
        1: "mean_reversion",
        2: "defensive",
    },
    # Trend-following sub-params
    "trend_following": {
        "fast_ma": 20,
        "slow_ma": 50,
        "entry_threshold": 0.01,
    },
    # Mean-reversion sub-params
    "mean_reversion": {
        "lookback": 20,
        "z_score_entry": 2.0,
        "z_score_exit": 0.5,
    },
    # Defensive / cash-like sub-params
    "defensive": {
        "safe_haven_tickers": [],  # e.g. ["TLT", "GLD"]
        "cash_allocation": 1.0,
    },
}

# ---------------------------------------------------------------------------
# Orchestrator profile — the LIVE bot's pinned strategy configuration.
# ---------------------------------------------------------------------------
# Keyword arguments passed straight to RegimeOrchestrator in main.py.
# Change this ONLY on the basis of an experiment-grid result
# (scripts/run_experiments.py) confirmed on a ticker you did not tune on.
# An empty dict = the orchestrator's legacy defaults.
# (main.py additionally injects max_exposure=RISK["max_position_size"];
# an explicit key here would override that.)
#
# Pinned 2026-07-09 ("tc_vol15"), re-validated 2026-07-10 under the LIVE
# configuration and the corrected measurement stack (exposure cap 0.50
# inside the orchestrator target; 2 bps slippage / zero commission charged
# to equity; decisions fill at the NEXT bar's open; dividend-adjusted
# prices; idle cash earns 2% p.a.; daily breakers off) — see
# experiments_report_{spy,qqq,iwm}_v3.md:
#   * trend_core  — SMA-200 trend rule IS the allocation; the HMM regime
#     tiers no longer drive it (they were measured to subtract value on
#     both tickers, even as a mere risk overlay).
#   * vol_target 0.15 — scales exposure down when realised 21d vol exceeds
#     15% annualised.  NOTE: under the 0.50 cap it only binds when the
#     vol-scale drops below 0.5 (realised vol > 30%), so it trims only the
#     worst episodes.  Kept: never worse than plain trend_core on any
#     ticker, and it restores the full DD protection automatically if the
#     cap is ever raised.
# v3 walk-forward results for the tc_vol15 core (net of costs, 0.50 cap):
#   SPY  +36.4% / Sharpe 1.14 / DD  -7.7%   (bench sma_200@100%: 0.92 / -18.3%)
#   QQQ  +49.3% / Sharpe 1.09 / DD  -8.9%   (bench sma_200@100%: 1.01 / -19.1%)
#   IWM  +21.3% / Sharpe 0.55 / DD -10.1%   (never tuned on; costless
#     sma_200 bench: 0.46 — profile stays modestly ahead where trend is weak)
# HONESTY NOTE: the block-bootstrap 90% CIs on ~6y of data are wide (SPY
# [0.42, 1.85]) and overlap almost completely across ALL trend-core
# variants — short-window rankings alone are not decision-grade.
#
# trend_confirm_bars=3 added 2026-07-10 by a PRE-REGISTERED single-shot
# test (decision rule fixed before running: switch iff combo Sharpe >=
# tc_vol15 on >= 4 of 5 datasets AND max DD nowhere >2pp worse).  The 30y
# Yahoo structural runs (1998-2026, spanning dotcom/2008/2011/2018/2020/
# 2022 — none of which was ever used for tuning) plus the v3 spans:
#   SPY 30y  0.84 vs 0.78,  DD  -9.9% vs -11.1%
#   QQQ 30y  0.84 vs 0.79,  DD -14.0% vs -17.5%
#   SPY v3   1.11 vs 1.14 (the single miss, -0.03)
#   QQQ v3   1.19 vs 1.09,  IWM v3  0.60 vs 0.55
# → 4/5 wins, DD better everywhere, ~25-40% fewer trades.  The 3-bar
# confirmation damps SMA-hover whipsaw — the a-priori rationale it was
# added to the grid with.  vol_target stays: under the 0.50 cap it rarely
# binds, but it restores tail protection automatically if the cap rises.
ORCHESTRATOR: dict = {
    "trend_core": True,
    "trend_confirm_bars": 3,
    "vol_target": 0.15,
}

# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------
RISK = {
    # Maximum fraction of portfolio per position.
    # Per-name exposure ceiling. Each ticker runs its OWN orchestrator that
    # targets up to 100% of equity, so with several tickers this cap is what
    # divides the book between them: 2 validated tickers × 0.50 = fully
    # invested when both are in-trend, half-invested when only one is.
    # (History: 0.10 was the old cap that — multiplied with the tier weights —
    # collapsed effective exposure to ~2% and kept the bot in cash; it was
    # briefly 1.00 while the bot traded a single symbol.)
    # No leverage (sum > 1.0) without also raising max_leverage.
    "max_position_size": 0.50,
    # Maximum gross leverage
    "max_leverage": 1.0,
    # Daily drawdown limit triggering a circuit breaker (fraction)
    "daily_drawdown_limit": 0.02,
    # Peak-to-trough drawdown limit triggering full halt (fraction)
    "max_drawdown_limit": 0.10,
    # Stop-loss per trade (fraction of entry price)
    "stop_loss_pct": 0.02,
    # Take-profit per trade (fraction of entry price)
    "take_profit_pct": 0.04,
    # Volatility scaling: target annualised portfolio vol
    "target_vol": 0.10,

    # ── Per-trade risk (position sizing) ───────────────────────────────────
    # Maximum risk per individual trade as a fraction of total portfolio.
    # Position size = (portfolio * max_risk_per_trade) / stop_loss_distance.
    # ── EDIT THIS to change how much you risk on any single trade ──────────
    "max_risk_per_trade": 0.01,    # 1% of portfolio at risk per trade

    # ── Hardcoded circuit-breaker trigger levels ────────────────────────────
    # Daily HALVE/FLATTEN breakers on/off.  They measure CLOSE-to-close
    # equity on a daily-bar system, i.e. they fire only after the loss is
    # fully realised, sell the low, and the drift trigger re-buys the next
    # bar.  Disabled 2026-07-10 on three independent measurements:
    #   * cap 1.0 walk-forward (SPY): breakers cost -12pp return / -0.16
    #     Sharpe with an IDENTICAL max drawdown,
    #   * cap 0.5 grid (SPY/QQQ/IWM v2 reports): *_nocb rows identical —
    #     the breakers never fire at half exposure, so disabling is free,
    #   * synthetic -10..-15% crash injections (3 seeds): max DD and return
    #     identical with breakers on vs off — the -10% HALT and the vol
    #     target already provide the tail protection.
    # The weekly breaker and the -10% HALT (tail protection, manual review)
    # are NOT affected by this flag and stay active.
    "cb_daily_enabled": False,
    # Single-day loss → halve all position sizes
    "cb_daily_halve_loss": 0.02,   # -2% intraday
    # Single-day loss → close ALL positions immediately
    "cb_daily_flatten_loss": 0.03, # -3% intraday
    # Weekly loss → resize all remaining positions down
    "cb_weekly_resize_loss": 0.05, # -5% over a rolling week
    # Peak-to-trough drawdown → stop the bot and write a lock file
    "cb_max_drawdown_halt": 0.10,  # -10% from equity peak

    # Factor applied to position sizes when the "halve" breaker fires
    "cb_halve_factor": 0.50,
    # Factor applied when the weekly resize breaker fires
    "cb_weekly_resize_factor": 0.50,
    # Trading days that constitute a "week" for the weekly breaker
    "weekly_lookback_days": 5,

    # ── Correlation control ────────────────────────────────────────────────
    # Reject a new position if its correlation with any existing open
    # position exceeds this threshold (absolute value).
    "max_position_correlation": 0.80,
    # Rolling window (bars) used to estimate pairwise correlations
    "correlation_lookback": 60,

    # ── Regime leverage caps ───────────────────────────────────────────────
    # Apply REGIME_LEVERAGE_CAPS in validate_order?  False = parity with the
    # backtester (which never modelled the caps); see REGIME_LEVERAGE_CAPS.
    "use_regime_leverage_caps": False,

    # ── Lock file ──────────────────────────────────────────────────────────
    # Path to the halt lock file written on the 10% drawdown breaker.
    # The bot refuses to start while this file exists.
    "lock_file_path": "logs/RISK_HALT.lock",
}

# ---------------------------------------------------------------------------
# Per-regime leverage caps (overrides RISK["max_leverage"] when a regime
# is active).  Keyed by regime label string from hmm_engine._LABEL_MAPS.
#
# DISABLED for the pinned trend-core profile (see RISK flag below): the
# backtests that validated the profile never applied these caps (the
# backtester does not call validate_order), so leaving them active live
# made the demoted HMM an untested hard entry gate — a noisy "Bear"/"Weak"
# label blocked every trend-core buy.  Re-enable ONLY together with a
# backtest that actually models the gate.
# ---------------------------------------------------------------------------
REGIME_LEVERAGE_CAPS = {
    "Euphoria":    1.25,
    "Strong Bull": 1.25,
    "Bull":        1.25,
    "Neutral":     1.00,
    "Weak":        0.75,
    "Bear":        0.00,
    "Deep Bear":   0.00,
    "Crash":       0.00,
}

# ---------------------------------------------------------------------------
# Backtest windows
# ---------------------------------------------------------------------------
BACKTEST = {
    # Full historical start date (ISO format)
    "start_date": "2015-01-01",
    # Full historical end date (ISO format); None means today
    "end_date": None,
    # Walk-forward windows in BARS — aligned with the Backtester defaults
    # actually used by the harness (the old 504/63 "calendar days" values
    # were read by nothing and disagreed with every published report).
    "train_window_bars": 252,
    "test_window_bars": 126,
    # Bar frequency: "1Day", "1Hour", etc. (Alpaca notation)
    "bar_timeframe": "1Day",
    # Initial paper capital for simulation
    "initial_capital": 100_000,
    # Commission per fill (fraction of notional).  Alpaca US equities are
    # commission-free; the old 0.001 (10 bps) overstated costs ~10× and
    # systematically biased variant selection toward low-turnover configs.
    "commission": 0.0,
    # Slippage per fill (fraction), charged against equity by the
    # backtester: half-spread + impact + timing noise for SPY/QQQ-class
    # liquidity.  Stress-test any variant choice at 2×/4× this value.
    "slippage": 0.0002,
    # Annualised yield credited on idle cash (flat T-bill approximation —
    # 3M bills averaged roughly 2.5% over 2020-2026, near 0% in 2020-21 and
    # ~5% in 2023-24).  A trend strategy spends long stretches in cash;
    # crediting nothing systematically understates it vs buy & hold.  The
    # same yield is credited to the sma_200 / random benchmarks' idle bars.
    # Sensitivity-check important decisions at 0.0 and 0.04.
    "cash_yield_annual": 0.02,
}

# ---------------------------------------------------------------------------
# Monitoring intervals
# ---------------------------------------------------------------------------
MONITORING = {
    # How often the live loop polls for new data (seconds)
    "poll_interval_seconds": 60,
    # How often the dashboard refreshes (seconds)
    "dashboard_refresh_seconds": 30,
    # Email alert recipients
    "alert_email_recipients": [],
    # Webhook URL for Slack / Teams alerts (set via env or here)
    "alert_webhook_url": "",
    # Log level: "DEBUG", "INFO", "WARNING", "ERROR"
    "log_level": "INFO",
    # Directory where log files are written
    "log_dir": "logs",
    # Rotating log file settings
    "log_max_bytes": 5_000_000,   # rotate at ~5 MB
    "log_backup_count": 5,        # keep 5 rotated files
}

# ---------------------------------------------------------------------------
# Alerts — thresholds and channel settings
# ── EDIT THESE to tune when and how the system notifies you ───────────────
# ---------------------------------------------------------------------------
ALERTS = {
    # Master switch
    "enabled": True,

    # ── Thresholds that trigger an alert ───────────────────────────────────
    # Daily drawdown beyond this fraction fires a warning alert.
    "daily_drawdown_alert": 0.02,      # -2%
    # Any circuit breaker at/above this severity fires an alert.
    # (matches core.risk_manager.CBLevel names)
    "circuit_breaker_alert_level": "HALVE",
    # Cooldown (seconds) before the same alert key may fire again.
    "cooldown_seconds": 300,

    # ── Email channel (SMTP) ───────────────────────────────────────────────
    "email_enabled": False,
    "smtp_host": "localhost",
    "smtp_port": 25,
    "smtp_use_tls": False,
    "smtp_username": "",               # leave blank; load secrets from .env
    "email_sender": "regime_trader@localhost",
    # Recipients also read from MONITORING["alert_email_recipients"].

    # ── Webhook channel (Slack / Discord) ──────────────────────────────────
    "webhook_enabled": False,
    # Webhook URL also read from MONITORING["alert_webhook_url"].
}
