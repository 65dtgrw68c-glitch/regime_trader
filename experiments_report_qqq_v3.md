# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open; idle cash earns 2.0% p.a. (also credited to sma_200/random benchmarks' idle bars)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_brake | +55.0% | +9.3% | 1.19 | [0.47, 1.91] | -7.4% | 26 | 8.0 | $0 |
| tc_confirm3 | +54.8% | +9.3% | 1.18 | [0.47, 1.91] | -7.3% | 48 | 8.1 | $0 |
| tc_confirm3_hi50 | +54.8% | +9.3% | 1.18 | [0.47, 1.91] | -7.3% | 48 | 8.1 | $0 |
| tc_vol15 | +49.3% | +8.5% | 1.09 | [0.37, 1.80] | -8.9% | 64 | 15.2 | $0 |
| tc_vol15_nocb | +49.3% | +8.5% | 1.09 | [0.37, 1.80] | -8.9% | 64 | 15.2 | $0 |
| trend_core | +49.0% | +8.4% | 1.08 | [0.36, 1.79] | -9.1% | 63 | 15.3 | $0 |
| tc_hi50 | +49.0% | +8.4% | 1.08 | [0.36, 1.79] | -9.1% | 63 | 15.3 | $0 |
| trend_core_nocb | +49.0% | +8.4% | 1.08 | [0.36, 1.79] | -9.1% | 63 | 15.3 | $0 |
| legacy_churn | +33.1% | +6.0% | 0.82 | [0.07, 1.57] | -15.8% | 1233 | 63.3 | $0 |
| regime_smooth_30 | +26.7% | +4.9% | 0.75 | [0.04, 1.46] | -15.3% | 252 | 18.6 | $0 |
| regime_defaults | +23.2% | +4.3% | 0.64 | [-0.15, 1.43] | -19.6% | 308 | 55.0 | $0 |
| bench:buy_and_hold | +104.9% | +15.7% | 0.75 | [0.09, 1.44] | -35.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +104.9% | +15.7% | 1.01 | [0.31, 1.72] | -19.1% | 0 | 0.0 | $0 |
| bench:random_entry | +26.1% | +4.8% | 0.39 | [-0.37, 1.16] | -40.7% | 0 | 0.0 | $0 |
