# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1496 bars (2018-11-01 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| trend_core | +23.0% | +4.3% | 0.78 | -11.0% | 74 | 20.3 | $0 |
| tc_hi50 | +23.0% | +4.3% | 0.78 | -11.0% | 74 | 20.3 | $0 |
| tc_vol15 | +23.0% | +4.3% | 0.78 | -11.0% | 74 | 20.3 | $0 |
| trend_core_nocb | +23.0% | +4.3% | 0.78 | -11.0% | 74 | 20.3 | $0 |
| tc_vol15_nocb | +23.0% | +4.3% | 0.78 | -11.0% | 74 | 20.3 | $0 |
| tc_confirm3_brake | +19.2% | +3.6% | 0.66 | -12.2% | 30 | 11.2 | $0 |
| tc_confirm3 | +18.7% | +3.5% | 0.65 | -12.2% | 56 | 11.3 | $0 |
| tc_confirm3_hi50 | +18.7% | +3.5% | 0.65 | -12.2% | 56 | 11.3 | $0 |
| legacy_churn | +19.3% | +3.6% | 0.63 | -9.3% | 1244 | 69.3 | $0 |
| regime_defaults | +13.5% | +2.6% | 0.47 | -11.1% | 323 | 64.3 | $0 |
| regime_smooth_30 | +8.3% | +1.6% | 0.33 | -12.0% | 321 | 23.0 | $0 |
| bench:buy_and_hold | +70.4% | +11.4% | 0.72 | -25.4% | 0 | 0.0 | $0 |
| bench:sma_200 | +48.0% | +8.3% | 0.77 | -20.6% | 0 | 0.0 | $0 |
| bench:random_entry | +3.6% | +0.7% | 0.12 | -21.3% | 0 | 0.0 | $0 |
