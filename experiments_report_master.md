# Master Experiment Report

## Current Production Candidate

Branch: improve-bot-risk-return

## Accepted Improvements

### Backtest Realism
- Single-asset next-open execution
- Cash yield support
- Dividend-adjusted data
- Slippage accounting
- Benchmark timing parity
- Real T-bill yields
- 30-year structural validation
- Bootstrap Sharpe confidence intervals

### Portfolio Backtester
- Dynamic multi-asset target weights
- Portfolio turnover tracking
- Portfolio transaction costs
- Idle cash yield
- Execution model documented as close-to-close approximation
- Live/backtest trend logic alignment
- Target-weight parity test with live path

### Risk Management
- Drawdown halt logic
- Stop-loss/take-profit remains opt-in
- Stop/TP sweep evidence: legacy 2%/4% pair harmful
- Portfolio correlation selector
- Gross exposure cap validation

### Live Trading Safety
- Broker abstraction
- Daily-bar deduplication
- Stale-bar skip
- Order idempotency
- Portfolio batch loop
- Portfolio batch loop stale-batch test
- Portfolio batch loop single shared rebalance test

### Deployment
- Oracle Cloud systemd deployment
- Daily timer
- Webhook health monitor
- pyarrow for parquet cache
- setup/update scripts preserve .env, logs and cache

## Rejected or Disabled Ideas

- Legacy 2% stop-loss / 4% take-profit default
- Stop/TP as default behaviour without fresh evidence
- Additional strategy parameters without out-of-sample validation

## Experimental / Needs More Evidence

- Portfolio next-open execution
- Portfolio slippage model
- Live-vs-backtest full decision parity beyond target weights
- Portfolio batch loop halt/risk rejection edge cases
- Legacy PortfolioBacktester in core/backtester.py cleanup

## Current Portfolio Backtest Assumptions

- Target weights are computed dynamically.
- Trend confirmation uses shared is_trend_confirmed() logic.
- Correlation selector removes highly correlated duplicate bets.
- Allocator computes final target weights.
- Turnover is measured as sum(abs(new_weight - old_weight)).
- Transaction costs are turnover * transaction_cost_bps / 10000.
- Idle cash receives cash_yield_annual / 252 per trading day.
- Current portfolio execution model remains close-to-close approximation.

## Open Risks

1. Portfolio backtester still does not use next-open execution.
2. Portfolio slippage model is not yet implemented.
3. There are two PortfolioBacktester classes:
   - core/portfolio_backtester.py
   - core/backtester.py
   Legacy check: current grep found active usage in main.py/tests for the new portfolio backtester path, but no direct import from core.backtester.PortfolioBacktester.
4. Portfolio batch loop needs more tests for:
   - drawdown halt
   - rejected target book
   - broker/position failures
   - market closed path
5. Reports should be reproducible from one command.

## Next Recommended Work

1. Add portfolio slippage support.
2. Add portfolio next-open execution or document why close-to-close remains acceptable.
3. Add portfolio batch loop risk rejection and halt tests.
4. Review legacy core/backtester.py PortfolioBacktester.
5. Create one reproducible validation command for reports.

## Decision

Current direction is accepted:
- consolidate portfolio realism,
- improve live safety,
- avoid parameter overfitting,
- require tests before further performance optimization.
