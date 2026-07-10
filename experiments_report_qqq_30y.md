# Strategy experiment report

Data: **QQQ via Yahoo (30y, adjusted)**, 6875 bars (1999-03-10 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open; idle cash earns 2.0% p.a. (also credited to sma_200/random benchmarks' idle bars)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3 | +382.4% | +6.2% | 0.82 | [0.50, 1.12] | -14.7% | 284 | 91.2 | $0 |
| tc_confirm3_hi50 | +382.4% | +6.2% | 0.82 | [0.50, 1.12] | -14.7% | 284 | 91.2 | $0 |
| tc_confirm3_brake | +380.3% | +6.2% | 0.81 | [0.49, 1.10] | -14.6% | 162 | 89.2 | $0 |
| tc_vol15 | +333.9% | +5.7% | 0.79 | [0.45, 1.09] | -17.5% | 391 | 154.9 | $0 |
| tc_vol15_nocb | +333.9% | +5.7% | 0.79 | [0.45, 1.09] | -17.5% | 391 | 154.9 | $0 |
| trend_core | +331.3% | +5.7% | 0.77 | [0.43, 1.07] | -19.9% | 382 | 154.8 | $0 |
| tc_hi50 | +331.3% | +5.7% | 0.77 | [0.43, 1.07] | -19.9% | 382 | 154.8 | $0 |
| trend_core_nocb | +331.3% | +5.7% | 0.77 | [0.43, 1.07] | -19.9% | 382 | 154.8 | $0 |
| regime_smooth_30 | +233.1% | +4.7% | 0.75 | [0.44, 1.06] | -15.1% | 1462 | 171.1 | $0 |
| regime_defaults | +205.9% | +4.3% | 0.65 | [0.34, 0.96] | -19.8% | 1459 | 409.6 | $0 |
| legacy_churn | +206.9% | +4.4% | 0.61 | [0.30, 0.91] | -21.5% | 6292 | 433.0 | $0 |
| bench:buy_and_hold | +671.6% | +8.1% | 0.43 | [0.14, 0.71] | -83.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +656.4% | +8.0% | 0.55 | [0.24, 0.85] | -56.2% | 0 | 0.0 | $0 |
| bench:random_entry | +118.8% | +3.0% | 0.26 | [-0.08, 0.58] | -81.5% | 0 | 0.0 | $0 |
