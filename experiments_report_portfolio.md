# Joint-book portfolio check

Joint-book composition of the live ticker set under the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50 per name.  
Data: Yahoo adjusted, 6876 aligned bars (1999-03-10 … 2026-07-10); cash yield: ^IRX series.  
Method: r_joint = r_A + r_B − y (cash-credit double-count correction); joint breakers not modelled (conservative on DD) — see scripts/portfolio_check.py docstring.

| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD |
|---|---:|---:|---:|---:|---:|
| JOINT BOOK SPY+QQQ (live profile) | +858.0% | +9.0% | 0.75 | [0.43, 1.04] | -20.8% |
| SPY alone @cap 0.50 | +264.4% | +5.0% | 0.90 | [0.58, 1.21] | -10.4% |
| QQQ alone @cap 0.50 | +367.3% | +6.0% | 0.81 | [0.47, 1.09] | -14.7% |
| bench: 50/50 buy&hold (daily rebal.) | +780.0% | +8.6% | 0.49 | [0.20, 0.79] | -68.9% |
| bench: 50/50 sma_200 (costless) | +639.4% | +7.9% | 0.65 | [0.33, 0.95] | -36.1% |
