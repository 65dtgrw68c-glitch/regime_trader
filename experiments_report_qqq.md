# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| tc_vol15 | +72.8% | +11.7% | 0.93 | -13.4% | 168 | 55.2 | $5,517 |
| trend_core | +57.0% | +9.6% | 0.68 | -20.7% | 68 | 44.5 | $4,454 |
| tc_confirm3_brake | +51.6% | +8.8% | 0.64 | -15.5% | 27 | 34.3 | $3,429 |
| tc_confirm3 | +51.6% | +8.8% | 0.64 | -15.5% | 54 | 35.3 | $3,527 |
| legacy_churn | +27.1% | +5.0% | 0.57 | -14.4% | 1179 | 112.7 | $11,270 |
| regime_defaults | +19.6% | +3.7% | 0.50 | -14.2% | 386 | 91.5 | $9,154 |
| regime_smooth_30 | +17.9% | +3.4% | 0.49 | -15.9% | 326 | 28.6 | $2,865 |
| tc_hi50 | +27.8% | +5.1% | 0.44 | -20.1% | 82 | 47.5 | $4,751 |
| tc_confirm3_hi50 | +22.6% | +4.2% | 0.38 | -15.1% | 70 | 39.8 | $3,977 |
| bench:buy_and_hold | +98.5% | +14.9% | 0.72 | -35.6% | 0 | 0.0 | $0 |
| bench:sma_200 | +102.6% | +15.4% | 1.00 | -19.6% | 0 | 0.0 | $0 |
| bench:random_entry | +18.5% | +3.5% | 0.30 | -41.2% | 0 | 0.0 | $0 |
