# Joint-book portfolio check

Joint-book composition of SPY, QQQ, GLD, IEF under the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50 per name.  
Data: Yahoo adjusted, 5456 aligned bars (2004-11-18 … 2026-07-29); cash yield: ^IRX series.  
Method: r_joint = Σ r_i − y*(n−1) (cash-credit corrected). The raw row does not apply joint breakers; the simulated-breaker row approximates next-bar daily/weekly scaling and sticky max-drawdown HALT.  
Breaker simulation: events=80, halted=False, halt_date=n/a.

| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD |
|---|---:|---:|---:|---:|---:|
| JOINT BOOK SPY+QQQ+GLD+IEF (raw live profile) | +1768.0% | +15.2% | 0.99 | [0.66, 1.34] | -20.3% |
| JOINT BOOK SPY+QQQ+GLD+IEF (simulated breakers) | +1693.1% | +15.0% | 0.99 | [0.67, 1.33] | -17.6% |
| SPY alone @cap 0.50 | +202.1% | +5.5% | 0.93 | [0.58, 1.31] | -9.2% |
| QQQ alone @cap 0.50 | +321.8% | +7.2% | 0.94 | [0.57, 1.32] | -12.9% |
| GLD alone @cap 0.50 | +180.0% | +5.1% | 0.71 | [0.36, 1.04] | -15.7% |
| IEF alone @cap 0.50 | +59.7% | +2.3% | 0.84 | [0.51, 1.21] | -4.5% |
| bench: equal-weight buy&hold (daily rebal.) | +744.0% | +10.9% | 0.98 | [0.66, 1.33] | -28.0% |
| bench: equal-weight sma_200 (costless) | +426.5% | +8.4% | 1.07 | [0.73, 1.42] | -12.0% |
