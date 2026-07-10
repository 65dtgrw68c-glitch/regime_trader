# Strategy experiment report

Data: **IWM via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_brake | +11.7% | +2.3% | 0.33 | -14.5% | 28 | 9.8 | $0 |
| tc_confirm3 | +11.3% | +2.2% | 0.32 | -14.7% | 46 | 9.8 | $0 |
| tc_confirm3_hi50 | +11.3% | +2.2% | 0.32 | -14.7% | 46 | 9.8 | $0 |
| tc_vol15 | +8.0% | +1.6% | 0.24 | -14.2% | 75 | 23.5 | $0 |
| tc_vol15_nocb | +8.0% | +1.6% | 0.24 | -14.2% | 75 | 23.5 | $0 |
| trend_core | +7.8% | +1.5% | 0.24 | -14.4% | 75 | 23.7 | $0 |
| tc_hi50 | +7.8% | +1.5% | 0.24 | -14.4% | 75 | 23.7 | $0 |
| trend_core_nocb | +7.8% | +1.5% | 0.24 | -14.4% | 75 | 23.7 | $0 |
| legacy_churn | -4.9% | -1.0% | -0.13 | -19.5% | 1243 | 71.1 | $0 |
| regime_smooth_30 | -9.7% | -2.0% | -0.36 | -16.8% | 291 | 18.7 | $0 |
| regime_defaults | -14.1% | -3.0% | -0.53 | -20.4% | 329 | 56.6 | $0 |
| bench:buy_and_hold | +36.6% | +6.5% | 0.39 | -33.1% | 0 | 0.0 | $0 |
| bench:sma_200 | +18.0% | +3.4% | 0.30 | -25.1% | 0 | 0.0 | $0 |
| bench:random_entry | +37.7% | +6.7% | 0.50 | -35.3% | 0 | 0.0 | $0 |
