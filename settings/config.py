"""
Central configuration file for regime_trader.

All tuneable parameters live here so every module imports from a single
source of truth instead of scattering magic numbers across the codebase.
"""

# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------
# List of symbols the system is allowed to trade.
TICKERS = [
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
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
# Risk thresholds
# ---------------------------------------------------------------------------
RISK = {
    # Maximum fraction of portfolio per position.
    # regime_trader runs a SINGLE-symbol strategy, so this is a portfolio-
    # level exposure ceiling, not a diversification limit. At 0.10 it capped
    # every position to 10% of equity, which — combined with the tier weights
    # applied on top — held effective exposure near 2% and left the bot in
    # cash. 1.0 lets the vol-tier allocations (0.20/0.60/0.95) be the real
    # allocation control; lower it only if you deliberately want a hard cap
    # below the tier weights (e.g. trading several names through one account).
    # No leverage (>1.0) without also raising max_leverage.
    "max_position_size": 1.00,
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

    # ── Hardcoded circuit-breaker trigger levels (non-negotiable) ──────────
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

    # ── Lock file ──────────────────────────────────────────────────────────
    # Path to the halt lock file written on the 10% drawdown breaker.
    # The bot refuses to start while this file exists.
    "lock_file_path": "logs/RISK_HALT.lock",
}

# ---------------------------------------------------------------------------
# Per-regime leverage caps (overrides RISK["max_leverage"] when a regime
# is active).  Keyed by regime label string from hmm_engine._LABEL_MAPS.
# ── EDIT THESE to tune how much leverage each regime is allowed ───────────
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
    # Walk-forward training window (calendar days)
    "train_window_days": 504,
    # Walk-forward test window (calendar days)
    "test_window_days": 63,
    # Bar frequency: "1Day", "1Hour", etc. (Alpaca notation)
    "bar_timeframe": "1Day",
    # Initial paper capital for simulation
    "initial_capital": 100_000,
    # Estimated round-trip commission per trade (fraction)
    "commission": 0.001,
    # Estimated slippage per trade (fraction)
    "slippage": 0.0005,
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
