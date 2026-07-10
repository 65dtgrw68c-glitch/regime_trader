# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_brake | +46.4% | +8.0% | 1.04 | -7.2% | 27 | 8.2 | $0 |
| tc_confirm3 | +45.9% | +8.0% | 1.03 | -7.3% | 49 | 8.2 | $0 |
| tc_confirm3_hi50 | +45.9% | +8.0% | 1.03 | -7.3% | 49 | 8.2 | $0 |
| tc_vol15 | +45.1% | +7.8% | 1.01 | -10.2% | 60 | 12.8 | $0 |
| tc_vol15_nocb | +45.1% | +7.8% | 1.01 | -10.2% | 60 | 12.8 | $0 |
| trend_core | +44.8% | +7.8% | 1.01 | -10.3% | 59 | 12.9 | $0 |
| tc_hi50 | +44.8% | +7.8% | 1.01 | -10.3% | 59 | 12.9 | $0 |
| trend_core_nocb | +44.8% | +7.8% | 1.01 | -10.3% | 59 | 12.9 | $0 |
| regime_smooth_30 | +28.1% | +5.1% | 0.98 | -8.3% | 252 | 19.1 | $0 |
| regime_defaults | +23.5% | +4.4% | 0.77 | -11.3% | 248 | 49.8 | $0 |
| legacy_churn | +18.5% | +3.5% | 0.51 | -17.8% | 1190 | 52.8 | $0 |
| bench:buy_and_hold | +98.5% | +14.9% | 0.72 | -35.6% | 0 | 0.0 | $0 |
| bench:sma_200 | +102.6% | +15.4% | 1.00 | -19.6% | 0 | 0.0 | $0 |
| bench:random_entry | +18.5% | +3.5% | 0.30 | -41.2% | 0 | 0.0 | $0 |
