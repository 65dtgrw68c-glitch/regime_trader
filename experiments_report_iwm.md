# Strategy experiment report

Data: **IWM via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_hi50 | +7.9% | +1.6% | 0.19 | -23.0% | 75 | 35.5 | $3,552 |
| tc_hi50 | +2.9% | +0.6% | 0.11 | -22.9% | 103 | 54.8 | $5,477 |
| tc_vol15 | +0.1% | +0.0% | 0.06 | -22.7% | 171 | 48.0 | $4,796 |
| regime_smooth_30 | -2.3% | -0.5% | -0.05 | -16.0% | 344 | 26.2 | $2,617 |
| legacy_churn | -5.4% | -1.1% | -0.13 | -21.1% | 1217 | 84.6 | $8,459 |
| tc_confirm3_brake | -12.9% | -2.8% | -0.13 | -31.1% | 38 | 29.1 | $2,914 |
| tc_confirm3 | -13.6% | -2.9% | -0.14 | -31.1% | 57 | 30.9 | $3,094 |
| regime_defaults | -7.5% | -1.6% | -0.20 | -22.1% | 385 | 79.6 | $7,958 |
| trend_core | -23.0% | -5.2% | -0.33 | -35.8% | 78 | 47.7 | $4,768 |
| bench:buy_and_hold | +36.6% | +6.5% | 0.39 | -33.1% | 0 | 0.0 | $0 |
| bench:sma_200 | +18.0% | +3.4% | 0.30 | -25.1% | 0 | 0.0 | $0 |
| bench:random_entry | +37.7% | +6.7% | 0.50 | -35.3% | 0 | 0.0 | $0 |
