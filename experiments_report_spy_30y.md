# Strategy experiment report

Data: **SPY via Yahoo (30y, adjusted)**, 7000 bars (1998-09-09 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open; idle cash earns 2.0% p.a. (also credited to sma_200/random benchmarks' idle bars)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3 | +255.1% | +4.8% | 0.83 | [0.53, 1.14] | -10.5% | 303 | 98.2 | $0 |
| tc_confirm3_hi50 | +255.1% | +4.8% | 0.83 | [0.53, 1.14] | -10.5% | 303 | 98.2 | $0 |
| tc_confirm3_brake | +255.6% | +4.9% | 0.83 | [0.53, 1.14] | -11.0% | 156 | 97.4 | $0 |
| tc_vol15 | +213.7% | +4.4% | 0.78 | [0.47, 1.08] | -11.1% | 406 | 160.7 | $0 |
| tc_vol15_nocb | +213.7% | +4.4% | 0.78 | [0.47, 1.08] | -11.1% | 406 | 160.7 | $0 |
| trend_core | +212.7% | +4.3% | 0.77 | [0.47, 1.07] | -11.0% | 404 | 161.0 | $0 |
| tc_hi50 | +212.7% | +4.3% | 0.77 | [0.47, 1.07] | -11.0% | 404 | 161.0 | $0 |
| trend_core_nocb | +212.7% | +4.3% | 0.77 | [0.47, 1.07] | -11.0% | 404 | 161.0 | $0 |
| regime_smooth_30 | +182.1% | +3.9% | 0.76 | [0.43, 1.11] | -22.1% | 1402 | 151.9 | $0 |
| legacy_churn | +205.2% | +4.3% | 0.72 | [0.43, 1.04] | -20.6% | 6587 | 485.6 | $0 |
| regime_defaults | +167.6% | +3.7% | 0.67 | [0.38, 0.99] | -19.9% | 1562 | 429.6 | $0 |
| bench:buy_and_hold | +795.0% | +8.5% | 0.52 | [0.23, 0.82] | -55.2% | 0 | 0.0 | $0 |
| bench:sma_200 | +537.6% | +7.2% | 0.66 | [0.35, 0.96] | -23.7% | 0 | 0.0 | $0 |
| bench:random_entry | +362.0% | +5.9% | 0.49 | [0.21, 0.80] | -33.3% | 0 | 0.0 | $0 |
