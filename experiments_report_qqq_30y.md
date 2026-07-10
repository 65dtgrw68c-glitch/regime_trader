# Strategy experiment report

Data: **QQQ via Yahoo (30y, adjusted)**, 6876 bars (1999-03-10 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3 | +380.8% | +6.2% | 0.82 | [0.49, 1.12] | -14.7% | 285 | 91.3 | $0 |
| tc_confirm3_hi50 | +380.8% | +6.2% | 0.82 | [0.49, 1.12] | -14.7% | 285 | 91.3 | $0 |
| tc_confirm3_brake | +375.6% | +6.1% | 0.81 | [0.46, 1.09] | -14.9% | 163 | 89.2 | $0 |
| tc_vol15 | +334.9% | +5.8% | 0.79 | [0.46, 1.08] | -14.5% | 390 | 154.3 | $0 |
| tc_vol15_nocb | +334.9% | +5.8% | 0.79 | [0.46, 1.08] | -14.5% | 390 | 154.3 | $0 |
| trend_core | +329.8% | +5.7% | 0.77 | [0.43, 1.06] | -17.0% | 383 | 154.7 | $0 |
| tc_hi50 | +329.8% | +5.7% | 0.77 | [0.43, 1.06] | -17.0% | 383 | 154.7 | $0 |
| trend_core_nocb | +329.8% | +5.7% | 0.77 | [0.43, 1.06] | -17.0% | 383 | 154.7 | $0 |
| regime_smooth_30 | +229.5% | +4.6% | 0.75 | [0.43, 1.07] | -15.5% | 1464 | 173.7 | $0 |
| regime_defaults | +196.4% | +4.2% | 0.65 | [0.33, 0.94] | -20.3% | 1478 | 402.4 | $0 |
| legacy_churn | +197.7% | +4.2% | 0.60 | [0.28, 0.90] | -19.6% | 6332 | 435.8 | $0 |
| bench:buy_and_hold | +671.7% | +8.1% | 0.43 | [0.14, 0.71] | -83.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +693.4% | +8.2% | 0.56 | [0.24, 0.85] | -50.0% | 0 | 0.0 | $0 |
| bench:random_entry | +449.4% | +6.7% | 0.44 | [0.14, 0.74] | -54.4% | 0 | 0.0 | $0 |
