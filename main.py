"""
main.py — orchestrator that ties all regime_trader components together.

Live trading loop (to be implemented):
1. Initialise logging (monitoring/logger.py).
2. Load environment variables (.env) and validate credentials.
3. Connect to Alpaca via AlpacaClient; wait for market open.
4. Fetch historical bars to warm up HMMEngine and FeatureEngineer.
5. Fit the initial HMM; determine starting regime.
6. Enter the main polling loop (interval from MONITORING["poll_interval_seconds"]):
   a. Fetch the latest bar for each ticker.
   b. Compute features with FeatureEngineer.
   c. Predict regime with HMMEngine; refit if due.
   d. Generate target weights via RegimeRouter.
   e. Size positions with RiskManager (check circuit breaker first).
   f. Rebalance portfolio via OrderExecutor.
   g. Refresh PositionTracker; log state.
   h. Check drawdown limits; fire alerts if thresholds breached.
7. On market close: run daily_reset() on RiskManager; log EOD summary.

Backtest entry point (to be implemented):
- Accept --backtest flag to run Backtester instead of the live loop,
  then print a PerformanceAnalyser summary.

Usage:
    python main.py               # live (or paper) trading
    python main.py --backtest    # historical simulation
"""

# TODO: implement main() and CLI argument parsing


if __name__ == "__main__":
    # TODO: call main()
    pass
