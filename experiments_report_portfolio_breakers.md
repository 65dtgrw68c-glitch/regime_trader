# Joint-book portfolio check

Joint-book composition of SPY, QQQ, GLD, IEF under the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50 per name.  
Data: Yahoo adjusted, 5458 aligned bars (2004-11-18 … 2026-07-31); cash yield: ^IRX series.  
Method: r_joint = Σ r_i − y*(n−1) (cash-credit corrected). The raw row does not apply joint breakers; the simulated-breaker row approximates next-bar daily/weekly scaling and sticky max-drawdown HALT.  
Breaker simulation: events=80, halted=False, halt_date=n/a.  
Turnover× is approximated from Backtester.trade_log as traded notional divided by initial capital, matching scripts/run_experiments.py. For the simulated-breaker row, Trades/Events reports breaker events, not fills.

| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Turnover× | Trades/Events |
|---|---:|---:|---:|---:|---:|---:|---:|
| JOINT BOOK SPY+QQQ+GLD+IEF (raw live profile) | +1825.6% | +15.4% | 1.00 | [0.68, 1.36] | -20.3% | 287.1 | 938 |
| JOINT BOOK SPY+QQQ+GLD+IEF (simulated breakers) | +1748.3% | +15.2% | 1.00 | [0.67, 1.36] | -17.6% | 287.1 | 80 |
| SPY alone @cap 0.50 | +205.7% | +5.6% | 0.94 | [0.58, 1.32] | -9.2% | 71.3 | 241 |
| QQQ alone @cap 0.50 | +329.8% | +7.3% | 0.95 | [0.57, 1.35] | -12.9% | 76.6 | 248 |
| GLD alone @cap 0.50 | +180.1% | +5.1% | 0.71 | [0.36, 1.03] | -15.7% | 81.2 | 233 |
| IEF alone @cap 0.50 | +59.8% | +2.3% | 0.84 | [0.50, 1.21] | -4.5% | 58.0 | 216 |
| bench: equal-weight buy&hold (daily rebal.) | +757.2% | +11.0% | 0.98 | [0.67, 1.35] | -28.0% | n/a | n/a |
| bench: equal-weight sma_200 (costless) | +434.9% | +8.5% | 1.08 | [0.74, 1.44] | -12.0% | n/a | n/a |
