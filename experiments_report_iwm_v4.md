# Strategy experiment report

Data: **IWM via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy_churn | +32.5% | +5.9% | 0.76 | [0.12, 1.40] | -10.7% | 1233 | 60.8 | $0 |
| regime_smooth_30 | +23.4% | +4.4% | 0.74 | [0.11, 1.36] | -9.6% | 261 | 18.6 | $0 |
| tc_confirm3_brake | +30.2% | +5.5% | 0.73 | [0.06, 1.33] | -8.6% | 28 | 10.5 | $0 |
| tc_confirm3 | +30.0% | +5.5% | 0.73 | [0.05, 1.33] | -8.7% | 46 | 10.4 | $0 |
| tc_confirm3_hi50 | +30.0% | +5.5% | 0.73 | [0.05, 1.33] | -8.7% | 46 | 10.4 | $0 |
| tc_vol15 | +27.3% | +5.0% | 0.67 | [-0.00, 1.31] | -10.0% | 71 | 22.9 | $0 |
| tc_vol15_nocb | +27.3% | +5.0% | 0.67 | [-0.00, 1.31] | -10.0% | 71 | 22.9 | $0 |
| trend_core | +27.2% | +5.0% | 0.67 | [-0.01, 1.30] | -10.1% | 71 | 23.2 | $0 |
| tc_hi50 | +27.2% | +5.0% | 0.67 | [-0.01, 1.30] | -10.1% | 71 | 23.2 | $0 |
| trend_core_nocb | +27.2% | +5.0% | 0.67 | [-0.01, 1.30] | -10.1% | 71 | 23.2 | $0 |
| regime_defaults | +21.8% | +4.1% | 0.65 | [0.01, 1.32] | -12.0% | 244 | 49.3 | $0 |
| bench:buy_and_hold | +45.4% | +7.9% | 0.45 | [-0.20, 1.12] | -31.9% | 0 | 0.0 | $0 |
| bench:sma_200 | +40.8% | +7.2% | 0.52 | [-0.16, 1.16] | -21.1% | 0 | 0.0 | $0 |
| bench:random_entry | +46.0% | +8.0% | 0.55 | [-0.12, 1.19] | -25.5% | 0 | 0.0 | $0 |
