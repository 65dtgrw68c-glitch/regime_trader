# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1496 bars (2018-11-01 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open; idle cash earns 2.0% p.a. (also credited to sma_200/random benchmarks' idle bars)  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| trend_core | +36.4% | +6.5% | 1.14 | [0.42, 1.85] | -7.7% | 72 | 19.9 | $0 |
| tc_hi50 | +36.4% | +6.5% | 1.14 | [0.42, 1.85] | -7.7% | 72 | 19.9 | $0 |
| tc_vol15 | +36.4% | +6.5% | 1.14 | [0.42, 1.85] | -7.7% | 72 | 19.9 | $0 |
| trend_core_nocb | +36.4% | +6.5% | 1.14 | [0.42, 1.85] | -7.7% | 72 | 19.9 | $0 |
| tc_vol15_nocb | +36.4% | +6.5% | 1.14 | [0.42, 1.85] | -7.7% | 72 | 19.9 | $0 |
| tc_confirm3_brake | +35.8% | +6.4% | 1.11 | [0.35, 1.85] | -8.2% | 30 | 12.5 | $0 |
| tc_confirm3 | +35.5% | +6.3% | 1.11 | [0.35, 1.85] | -8.1% | 58 | 12.6 | $0 |
| tc_confirm3_hi50 | +35.5% | +6.3% | 1.11 | [0.35, 1.85] | -8.1% | 58 | 12.6 | $0 |
| legacy_churn | +31.5% | +5.7% | 0.96 | [0.27, 1.77] | -9.0% | 1234 | 62.5 | $0 |
| regime_defaults | +30.4% | +5.5% | 0.91 | [0.25, 1.70] | -9.1% | 278 | 59.4 | $0 |
| regime_smooth_30 | +27.8% | +5.1% | 0.89 | [0.26, 1.62] | -7.4% | 255 | 20.8 | $0 |
| bench:buy_and_hold | +82.4% | +12.9% | 0.80 | [0.17, 1.53] | -24.5% | 0 | 0.0 | $0 |
| bench:sma_200 | +62.2% | +10.3% | 0.92 | [0.18, 1.66] | -18.3% | 0 | 0.0 | $0 |
| bench:random_entry | +12.1% | +2.3% | 0.26 | [-0.35, 0.90] | -19.1% | 0 | 0.0 | $0 |
