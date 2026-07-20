# Improvement Inventory

## Bereits umgesetzt / nicht erneut bauen

### Backtest-Realismus
- Next-open execution im Single-Asset-Backtester
- Cash yield im Single-Asset-Backtester
- Dividend-adjusted data
- Slippage accounting im Single-Asset-Backtester
- Benchmark timing parity
- Real T-bill yields
- 30-year validation
- Bootstrap Sharpe confidence intervals

### Strategie / Regime
- SMA-200 trend filter
- Confidence ramp
- Vol targeting
- HMM warmup
- Periodic HMM/scaler refit
- Forward-state replay
- HMM no-lookahead test
- trend_confirm_bars=3 pinned

### Risiko
- Position sizing fixed
- Drawdown halt logic
- Stop-loss/take-profit opt-in
- Stop/TP sweep evidence
- Correlation risk check

### Live Trading
- Broker abstraction
- Alpaca integration
- Market-hours guard
- Daily bars
- Bar deduplication
- Stale-bar skip
- Order idempotency
- Duplicate-order prevention
- Pause recovery

### Multi-Asset / Portfolio
- Dynamic multi-asset portfolio backtester
- Portfolio correlation selector
- Selector tests for correlated duplicates
- GLD and IEF activated as diversifiers
- Portfolio batch trading loop

### Deployment
- Oracle Cloud systemd deployment
- Daily timer
- Webhook health monitor
- setup.sh preserves .env/logs/cache
- pyarrow installed for parquet cache
- Low-memory swapfile support

## Offen / gezielt validieren

### Portfolio Backtester
- Portfolio cash accounting
- Portfolio transaction costs
- Slippage
- Turnover metrics
- Next-open or documented execution model
- Missing-data behavior
- Cash never negative test

### Live-vs-Backtest-Parität
- Same data should produce same target weights
- Portfolio backtester and live loop should share selector/allocation path

### Portfolio Live Safety
- Portfolio batch loop must not duplicate orders
- Portfolio batch loop must skip stale bars
- Portfolio batch loop must respect drawdown halt
- Portfolio batch loop must not trade when broker positions are unavailable

### Reporting
- Master experiment report
- Accepted/rejected/experimental decision log
