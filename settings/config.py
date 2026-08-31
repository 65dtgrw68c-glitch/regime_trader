"""
Central configuration file for regime_trader.

All tuneable parameters live here so every module imports from a single
source of truth instead of scattering magic numbers across the codebase.
"""

import os

try:
    # Best-effort: picks up .env in local/dev (Codespace, tests). On the
    # server, systemd's EnvironmentFile= already sets these directly, so
    # this is a no-op there (load_dotenv doesn't override existing env vars).
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Tickers / universe
# ---------------------------------------------------------------------------
# The runtime universe is now driven by a validated-asset map.  Only entries
# marked as validated are traded; other assets remain available for future
# harness validation without being activated by default.
# Each asset carries:
#   asset_class  — bucket for the portfolio-level class caps
#   validated    — only validated assets are traded at all
#   role         — "core"   : offered to the trend/correlation/allocator path
#                  "sleeve" : NOT allocated by the allocator; driven solely by
#                             an explicit entry in SLEEVES (see below)
#   leverage     — economic exposure per unit of notional (2.0 for a 2x ETF).
#                  The allocator and the gross cap work in NOTIONAL weights;
#                  this factor is what lets the risk layer also bound the
#                  ECONOMIC exposure (RISK["economic_gross_cap"]).
UNIVERSE = {
    "assets": {
        "SPY": {"asset_class": "equity", "validated": True,
                "role": "core", "leverage": 1.0},
        "QQQ": {"asset_class": "equity", "validated": True,
                "role": "core", "leverage": 1.0},
        "GLD": {"asset_class": "gold", "validated": True,
                "role": "core", "leverage": 1.0},
        "IEF": {"asset_class": "bonds", "validated": True,
                "role": "core", "leverage": 1.0},
        "DBC": {"asset_class": "commod", "validated": False,
                "role": "core", "leverage": 1.0},
        # 2x QQQ.  Traded ONLY through the levered sleeve, never by the
        # allocator: the correlation selector would reject it on sight (it is
        # ~0.95 correlated with SPY/QQQ and has the highest vol, so it sorts
        # last and always loses), and that rejection is correct for a
        # DIVERSIFICATION candidate — but this sleeve is a deliberate,
        # separately budgeted beta position, not a diversifier.
        "QLD": {"asset_class": "levered_equity", "validated": True,
                "role": "sleeve", "leverage": 2.0},
    },
    "class_caps": {
        "equity": 0.70,
        "gold": 0.20,
        "bonds": 0.25,
        "commod": 0.10,
        "levered_equity": 0.40,
    },
    "vol_lookback": 63,
}
TICKERS = [
    ticker for ticker, meta in UNIVERSE["assets"].items()
    if meta.get("validated", False)
]

# ---------------------------------------------------------------------------
# Sleeves — how the book is split between the diversified core and explicit
# levered trend sleeves.
# ---------------------------------------------------------------------------
# Rationale (measured 2026-08-01, 2007-2026, 2 bps, ^IRX cash — see
# analysis_report_2026-08-01_deep_review.md and scripts/sleeve_check.py):
#
#   Buch                              CAGR     Vol   Sharpe   maxDD
#   SPY buy&hold (Benchmark)        11.09%   19.7%     0.63  -55.2%
#   Kern allein (core_scale 1.0)     7.89%    7.6%     1.04  -11.8%
#   Sleeve allein (Trend(QQQ)→QLD)  20.43%   31.7%     0.75  -46.8%
#   60% Kern + 40% Sleeve           13.66%   16.6%     0.86  -24.8%
#
# The core book alone is the better STRATEGY (Sharpe 1.04) but structurally
# cannot beat a 19.7%-vol index from 7.6% vol — that is arithmetic, not skill
# (it beat SPY in 0% of rolling 10-year windows).  The blend buys index-beating
# absolute return (+2.6pp p.a., ahead in ~56% of rolling windows) by giving up
# 0.18 Sharpe and doubling the drawdown.  That trade-off is the OWNER'S
# DECISION, taken deliberately on 2026-08-01 — it is not a tuning result and
# must not be re-optimised by grid search.
#
# Known weakness, measured: levered trend works in SLOW bear markets (2008:
# blend +2.7% vs SPY -36.9%) and fails in FAST crashes (2020-02..04: blend
# -15.8% vs SPY -13.5%) because the SMA-200 exits too late.
SLEEVES = {
    # Multiplier applied to the allocator's core book before the sleeves are
    # added.  1.0 = pure core (the pre-2026-08 behaviour).
    "core_scale": 0.60,
    # Explicit levered trend sleeves.  `signal` is the UNLEVERED asset whose
    # SMA-200 trend drives the position (never the levered ETF itself: its own
    # SMA is distorted by the daily-reset path dependency).  `weight` is a
    # FIXED notional target, not vol-scaled — the leverage already lives in
    # the product.
    "levered": [
        {"ticker": "QLD", "signal": "QQQ", "weight": 0.40},
    ],
}

# ---------------------------------------------------------------------------
# Book-level volatility target  (owner decision 2026-08-31, finding K1)
# ---------------------------------------------------------------------------
# Scales EVERY target weight of the composed book by
# min(1, target / realised_vol), where realised_vol is the annualised std of
# the book's own trailing `lookback` daily returns.  Never leverages up.
#
# WHY 0.12, and why this overrides a pre-registered rejection
# ----------------------------------------------------------
# This exact candidate was REJECTED on the frozen holdout — it gives up 298 bp
# of CAGR against a 150 bp limit (preregistration_2026-08-25_tighter_vol_target
# .md).  That verdict stands and has NOT been retuned.  It is overridden here
# deliberately, on a measurement the holdout rule never looked at.
#
# The holdout rule scores candidates on 2021-2026 CAGR.  That window contains
# no regime in which this book's -35% HALT fires, so the rule is structurally
# blind to the one event that dominates the book's long-run return: the HALT
# is STICKY (RiskManager never re-arms it), so firing it does not mean "a bad
# year", it means flat forever until a human intervenes.
#
# scripts/k1_return_ledger.py prices exactly that, over the 2000-2026
# reconstruction with the sticky HALT applied (terminal wealth, 26 years):
#
#   sleeve 40%, no vol-target (was deployed)  halts 2000-07-28   0.82x capital
#   sleeve 40%, vol-target 20%                halts 2002-12-04   0.72x capital
#   sleeve 30% + vol-target 20%               halts 2003-01-17   0.72x capital
#   sleeve 20%                                halts 2003-01-17   0.76x capital
#   sleeve 10%                                never halts        6.51x capital
#   sleeve 0% (core only)                     never halts        5.19x capital
#   sleeve 40%, vol-target 12%   <- THIS      never halts        9.04x capital
#
# Every configuration that halts ends 26 years UNDER water.  Among the three
# that survive, 0.12 keeps the most holdout return (14.92% CAGR vs 12.16% at
# sleeve 10% and 10.15% core-only) AND the most reconstructed terminal wealth.
# It is the return-maximising choice on both windows at once — the 298 bp
# holdout give-up buys an 11x difference in the tail.
#
# Note the ordering this exposes: vol-target 20% and the sleeve cuts are all
# WORSE than doing nothing on the reconstruction.  They soften the drawdown
# without preventing the breach, so the HALT fires later and at a lower equity
# level.  Partial mitigation of a sticky breaker is not partial protection.
#
# KNOWN FRAGILITY, stated plainly: 0.12 clears the -35% HALT with 2.6 pp of
# margin (reconstructed maxDD -32.41%).  That margin rests on a MODEL — QLD
# before 2006-06 is synthetic (2x QQQ less 3.19%/yr drag, fit from the real
# overlap).  If the real tail were ~3 pp deeper than the reconstruction, this
# configuration halts too and lands with the 0.7-0.8x rows.  This is a
# materially better bet than the alternatives, not a guarantee.
#
# Do NOT re-optimise this number by scanning targets until one looks best.
# 0.12 was specified and rejected BEFORE this ledger was computed, which is
# the only reason it can be adopted on the ledger's evidence without that
# being selection.  Same rule as SLEEVES above.
BOOK_VOL_TARGET = {
    # Annualised target vol for the whole book. 0.0 disables the mechanism
    # entirely (the pre-2026-08-31 behaviour).
    "target": 0.12,
    # Trading days of the book's OWN realised returns used to estimate vol.
    # 21 is carried over unchanged from all three pre-registrations.
    "lookback": 21,
}

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
    # Which live decision path runs in TradingSystem.run():
    #   True  → run_portfolio_once: ONE shared target book per bar
    #           (universe → selector → allocator → sleeves).  This is the
    #           validated path — scripts/sleeve_check.py and
    #           core/portfolio_backtester.py evaluate exactly this book.
    #   False → run_once per ticker via RegimeOrchestrator (HMM/trend_core).
    # This key used to be ABSENT while main.py read it with a `True` default,
    # so the live path was chosen by an invisible default and silently
    # diverged from everything the reports validated.  Keep it explicit.
    "portfolio_batch_loop": True,
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
    # Whether a stable HMM regime is required before trading.  The pinned
    # trend-core profile is robust enough to continue with the last stable
    # regime when the model is unavailable or still warming up.
    "required": False,
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
#     15% annualised.  Under the 0.50 cap it only binds when the vol-scale
#     drops below 0.5 (realised vol > 30%) — but exactly those crash
#     episodes are where it earns its keep: with the -20% halt (which no
#     longer truncates crash windows) the 30y QQQ runs WITHOUT it blow out
#     to DD -21.5% (confirm3 alone) / -27.2% (trend_core alone), while the
#     pinned combo holds -14.7%.  The old -10% halt had masked this.
# Walk-forward results for the PINNED profile (net of costs, 0.50 cap,
# ^IRX cash yield, next-open fills for strategy AND benchmarks, -20% halt):
#   SPY  +41.8% / Sharpe 1.27 / DD -7.7%   (bench sma_200@100%: 0.95 / -16.9%)
#   QQQ  +61.7% / Sharpe 1.30 / DD -7.2%   (bench sma_200@100%: 1.02 / -18.9%)
#   IWM  +29.7% / Sharpe 0.72 / DD -8.7%   (never tuned on; costless
#     sma_200 bench: 0.52 — profile stays ahead where trend is weak)
#   30y structural (per name): SPY 0.90 / -10.4%, QQQ 0.81 / -14.7%
# JOINT BOOK (scripts/portfolio_check.py, 27y SPY+QQQ on shared equity):
#   CAGR +9.0% / Sharpe 0.75 [0.43, 1.04] / DD -20.8%  vs  50/50 buy&hold
#   +8.6% / 0.49 / DD -68.9% and costless 50/50 sma_200 +7.9% / 0.65 /
#   -36.1%.  NOTE the joint DD is ~2x the single-name runs (SPY/QQQ draw
#   down together) — see the cb_max_drawdown_halt comment in RISK.
# HONESTY NOTE: the block-bootstrap 90% CIs on ~6y of data are wide (SPY
# [0.50, 2.03]) and overlap almost completely across ALL trend-core
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
#
# NOT READ BY THE DEPLOYED LIVE/BACKTEST PATH. `trend_confirm_bars` and
# `vol_target` above are consumed only by RegimeOrchestrator
# (core/regime_strategies.py), which the default portfolio path
# (BROKER["portfolio_batch_loop"]=True, i.e. main.py's run_portfolio_once /
# core/portfolio_backtester.py) never instantiates. The deployed sleeve/core
# signal is the plain `is_trend_confirmed()` check (close > SMA200, no bar
# confirmation, no portfolio vol target) in core/regime_strategies.py and
# core/sleeves.py — every walk-forward number and pre-registration result
# documented above and below applies to RegimeOrchestrator, not to what runs
# at 09:35 NY. Found 2026-08-13 (analysis_report_2026-08-13_rendite.md,
# "Section 0" / P1+P2) after the mandate that produced that report assumed
# both were active live.
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
    # max_position_size remains as a backwards-compatible alias for the
    # portfolio-level per-name cap used by the sizing layer.
    "max_position_size": 0.50,
    # Hard per-name cap for target weights in the book validator.
    "per_name_cap": 0.50,
    # Maximum gross NOTIONAL exposure of the target book (no margin).
    "gross_cap": 1.0,
    # Maximum gross ECONOMIC exposure: Σ |weight_i| * leverage_i, using the
    # per-asset `leverage` from UNIVERSE.  Notional and economic exposure only
    # differ once levered products are held: the 60/40 book is ~0.84 notional
    # but ~1.24 economic on average (0.44 core ×1 + 0.40 QLD ×2), peaking near
    # 1.4.  Without this second cap the notional gross_cap would happily wave
    # through a book that is economically 2x levered.
    "economic_gross_cap": 1.50,
    # Portfolio-level caps per asset class.  levered_equity is deliberately
    # equal to the configured sleeve weight, so SLEEVES cannot silently grow
    # past what the risk layer was set up to allow.
    "class_caps": {
        "equity": 0.70,
        "gold": 0.20,
        "bonds": 0.25,
        "commod": 0.10,
        "levered_equity": 0.40,
    },
    # Maximum gross leverage
    "max_leverage": 1.0,
    # (The former daily_drawdown_limit / max_drawdown_limit keys were unused
    # duplicates of cb_daily_halve_loss / cb_max_drawdown_halt — removed so
    # there is a single source of truth for each threshold.)
    # Per-trade protective exits, simulated intraday by the backtester
    # against each bar's low/high (0.0 = disabled).  DISABLED — measured,
    # not assumed (experiments_report_stops.md, 28y SPY+QQQ, pinned
    # profile, 2026-07-10):
    #   * the legacy 2%/4% pair these keys used to advertise would have
    #     cost 0.12-0.19 Sharpe AND worsened max drawdown on BOTH tickers
    #     (SPY 0.84→0.72 / DD -10.3%→-13.9%; QQQ 0.81→0.62 / -14.7%→-16.7%)
    #     at ~3-4x the trades;
    #   * NO stop level (2/5/10/15%) improved anything consistently: every
    #     config is <= baseline on QQQ, and SPY's best rows (+0.01/+0.02
    #     Sharpe) are inside the noise band with worse DD or more trades;
    #   * even a 15% "disaster stop" never fires on SPY and sold lows for
    #     nothing on QQQ.
    # For a daily-bar trend system the exit IS the signal (SMA-200 flip,
    # vol targeting, portfolio breakers).  Turning these on requires a new
    # sweep that beats baseline on a ticker you did not tune on.
    "stop_loss_pct": 0.0,
    "take_profit_pct": 0.0,
    # Volatility scaling: target annualised portfolio vol
    "target_vol": 0.10,

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
    # Peak-to-trough drawdown → stop the bot and write a lock file.
    # RAISED 0.10 → 0.20 (owner decision 2026-07-10): the joint-book check
    # showed the two-ticker book's NORMAL max drawdown over 27 years is
    # ~-20% (2008, 2022), so a -10% halt sat inside the strategy's ordinary
    # operating range and would have stopped the bot in every major bear
    # market — the validated performance exists precisely because the
    # trend rule trades THROUGH those phases and catches the recovery.
    # At -20% the halt is a tail/malfunction safeguard rather than a
    # scheduled bear-market shutdown.  The bot still de-risks in bears on
    # its own (SMA-200 exit + vol target kept the joint DD near -20% vs
    # -69% for 50/50 buy&hold).  CAVEAT (measured under this setting): the
    # 27y joint-book DD is -20.8%, i.e. this halt would still have fired
    # ONCE, at the very trough of the worst episode.  That is arguably
    # exactly what a tail safeguard is for; raise to ~0.25 only if you
    # want it strictly outside everything in 27 years of history.
    # RAISED 0.20 → 0.35 (owner decision 2026-08-01, together with the levered
    # sleeve).  MEASURED, not guessed (scripts/sleeve_check.py, 2007-2026):
    # the 60/40 book's own worst drawdown is -24.8% (trough 2016-06-27), so a
    # -20% halt fires INSIDE the strategy's ordinary operating range and kills
    # it for good — CAGR collapses 13.66% → 1.00%, Sharpe 0.86 → 0.18.  At
    # -25%/-30%/-35% it never fires over the whole span.
    # 0.35 rather than 0.25 on purpose: pinning the halt just past the deepest
    # drawdown that happens to be in the sample is fitting a safety limit to
    # one realised path.  0.35 keeps ~10pp of headroom above anything observed
    # while still stopping a genuine malfunction long before ruin.
    # NOTE: this threshold only does anything because the peak-equity state is
    # now persisted across process restarts (see RiskManager._state_path); in
    # the daily `--once` deployment it previously reset every morning and no
    # drawdown breaker could ever fire.
    "cb_max_drawdown_halt": 0.35,  # -35% from equity peak

    # Factor applied to position sizes when the "halve" breaker fires
    "cb_halve_factor": 0.50,
    # Factor applied when the weekly resize breaker fires
    "cb_weekly_resize_factor": 0.50,
    # Trading days that constitute a "week" for the weekly breaker
    "weekly_lookback_days": 5,

    # ── Correlation control ────────────────────────────────────────────────
    # Reject a new position if its correlation with any existing open
    # position exceeds this threshold (absolute value).
    "enable_correlation_check": True,
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
# Frozen holdout — set 2026-08-24, Phase 0 of that audit's remediation plan
# ---------------------------------------------------------------------------
# core_scale/sleeve weight (SLEEVES above), cb_max_drawdown_halt and every
# class/gross cap in RISK were all chosen by looking at performance over the
# FULL history back to 2007. That is fine for picking a structure once, but
# it means the full history can no longer tell you whether a NEW change
# (e.g. the Section-F book-level vol-target candidate) generalises or was
# just fit to what's already been seen — the two questions need different
# data.
#
# HOLDOUT_START marks the boundary: every date >= this is off-limits for
# choosing or justifying a parameter, threshold, or structural change. It
# may be used only to evaluate a change that was fully specified (formula,
# parameter values, accept/reject rule) BEFORE looking at data in this
# window — i.e. this can confirm or reject a candidate, never help select
# one. Chosen to include both 2022 (a slow bear) and the 2025-02..05 tariff
# shock (a fast one), the two crisis shapes the book behaves most
# differently under (Section D of the audit).
#
# This is a policy marker, not an enforced code path — no function filters
# on it today. It exists so future analysis scripts have one place to read
# the boundary from, and so a report that touches this window without
# saying why is a visible policy violation, not just an omission.
HOLDOUT_START = "2021-01-01"

# ---------------------------------------------------------------------------
# Monitoring intervals
# ---------------------------------------------------------------------------
MONITORING = {
    # How often the live loop polls for new data (seconds)
    "poll_interval_seconds": 60,
    # How often the dashboard refreshes (seconds)
    "dashboard_refresh_seconds": 30,
    # Email alert recipients — comma-separated ALERT_EMAIL_RECIPIENTS in .env.
    "alert_email_recipients": [
        r.strip() for r in os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",") if r.strip()
    ],
    # Webhook URL for Slack / Discord alerts — ALERT_WEBHOOK_URL in .env.
    # The server's .env already sets this for deploy/monitor.sh's 30-minute
    # healthcheck; wiring it through here means AlertManager (fired in-process
    # by main.py for order rejections, API outages, drift, halts, ...) uses
    # the SAME url and pushes immediately instead of waiting for the next
    # healthcheck cycle.
    "alert_webhook_url": os.getenv("ALERT_WEBHOOK_URL", ""),
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
    # Per-channel kill switch — actually enforced in AlertManager.alert() now
    # (it used to be read at init and never checked, so this flag was purely
    # decorative). True by default: the real gate is having a destination
    # configured at all (alert_email_recipients is empty unless
    # ALERT_EMAIL_RECIPIENTS is set), so leaving this on costs nothing until
    # you actually add a recipient. Flip to False to silence this channel
    # even with recipients configured.
    "email_enabled": True,
    "smtp_host": "localhost",
    "smtp_port": 25,
    "smtp_use_tls": False,
    "smtp_username": "",               # leave blank; load secrets from .env
    "email_sender": "regime_trader@localhost",
    # Recipients also read from MONITORING["alert_email_recipients"].

    # ── Webhook channel (Slack / Discord) ──────────────────────────────────
    # Same kill-switch semantics as email_enabled above. True by default:
    # gated in practice by alert_webhook_url being set at all.
    "webhook_enabled": True,
    # Webhook URL also read from MONITORING["alert_webhook_url"].
}
