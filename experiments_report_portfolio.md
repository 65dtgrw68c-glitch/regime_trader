# Joint-book portfolio check

Joint-book composition of the live ticker set under the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50 per name.  
Data: Yahoo adjusted, 6875 aligned bars (1999-03-10 … 2026-07-09); cash yield: ^IRX series.  
Method: r_joint = r_A + r_B − y (cash-credit double-count correction); joint breakers not modelled (conservative on DD) — see scripts/portfolio_check.py docstring.

| Portfolio | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD |
|---|---:|---:|---:|---:|---:|
| JOINT BOOK SPY+QQQ (live profile) | +884.9% | +9.1% | 0.77 | [0.45, 1.06] | -20.4% |
| SPY alone @cap 0.50 | +263.6% | +5.0% | 0.90 | [0.58, 1.21] | -10.4% |
| QQQ alone @cap 0.50 | +379.5% | +6.1% | 0.84 | [0.50, 1.14] | -14.7% |
| bench: 50/50 buy&hold (daily rebal.) | +776.7% | +8.6% | 0.48 | [0.20, 0.78] | -68.9% |
| bench: 50/50 sma_200 (costless) | +636.7% | +7.9% | 0.65 | [0.32, 0.95] | -36.1% |
