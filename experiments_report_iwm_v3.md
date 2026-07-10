# Strategy experiment report

Data: **IWM via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open; idle cash earns 2.0% p.a. (also credited to sma_200/random benchmarks' idle bars)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy_churn | +25.4% | +4.7% | 0.62 | [-0.00, 1.24] | -10.2% | 1233 | 59.7 | $0 |
| tc_confirm3_brake | +24.4% | +4.5% | 0.61 | [-0.06, 1.22] | -9.6% | 29 | 10.4 | $0 |
| tc_confirm3 | +23.9% | +4.4% | 0.60 | [-0.06, 1.20] | -9.7% | 46 | 10.3 | $0 |
| tc_confirm3_hi50 | +23.9% | +4.4% | 0.60 | [-0.06, 1.20] | -9.7% | 46 | 10.3 | $0 |
| regime_smooth_30 | +16.7% | +3.2% | 0.55 | [-0.07, 1.18] | -9.4% | 261 | 18.3 | $0 |
| tc_vol15 | +21.3% | +4.0% | 0.55 | [-0.14, 1.18] | -10.1% | 71 | 22.7 | $0 |
| tc_vol15_nocb | +21.3% | +4.0% | 0.55 | [-0.14, 1.18] | -10.1% | 71 | 22.7 | $0 |
| trend_core | +21.2% | +4.0% | 0.54 | [-0.14, 1.17] | -10.1% | 71 | 23.0 | $0 |
| tc_hi50 | +21.2% | +4.0% | 0.54 | [-0.14, 1.17] | -10.1% | 71 | 23.0 | $0 |
| trend_core_nocb | +21.2% | +4.0% | 0.54 | [-0.14, 1.17] | -10.1% | 71 | 23.0 | $0 |
| regime_defaults | +15.2% | +2.9% | 0.48 | [-0.14, 1.14] | -11.2% | 244 | 48.4 | $0 |
| bench:buy_and_hold | +45.4% | +7.9% | 0.45 | [-0.20, 1.12] | -31.9% | 0 | 0.0 | $0 |
| bench:sma_200 | +33.5% | +6.0% | 0.46 | [-0.26, 1.08] | -22.6% | 0 | 0.0 | $0 |
| bench:random_entry | +48.8% | +8.4% | 0.60 | [-0.13, 1.36] | -34.4% | 0 | 0.0 | $0 |
