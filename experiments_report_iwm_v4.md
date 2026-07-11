# Strategy experiment report

Data: **IWM via Alpaca (IEX feed)**, 1496 bars (2020-07-27 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy_churn | +32.5% | +5.9% | 0.76 | [0.12, 1.39] | -10.7% | 1234 | 60.9 | $0 |
| regime_smooth_30 | +23.4% | +4.3% | 0.73 | [0.11, 1.36] | -9.6% | 261 | 18.6 | $0 |
| tc_confirm3_brake | +29.9% | +5.4% | 0.72 | [0.07, 1.33] | -8.6% | 28 | 10.5 | $0 |
| tc_confirm3 | +29.7% | +5.4% | 0.72 | [0.07, 1.33] | -8.7% | 46 | 10.4 | $0 |
| tc_confirm3_hi50 | +29.7% | +5.4% | 0.72 | [0.07, 1.33] | -8.7% | 46 | 10.4 | $0 |
| tc_vol15 | +27.0% | +5.0% | 0.67 | [-0.02, 1.30] | -10.0% | 71 | 22.9 | $0 |
| tc_vol15_nocb | +27.0% | +5.0% | 0.67 | [-0.02, 1.30] | -10.0% | 71 | 22.9 | $0 |
| trend_core | +26.9% | +4.9% | 0.66 | [-0.03, 1.30] | -10.1% | 71 | 23.2 | $0 |
| tc_hi50 | +26.9% | +4.9% | 0.66 | [-0.03, 1.30] | -10.1% | 71 | 23.2 | $0 |
| trend_core_nocb | +26.9% | +4.9% | 0.66 | [-0.03, 1.30] | -10.1% | 71 | 23.2 | $0 |
| regime_defaults | +21.8% | +4.1% | 0.65 | [0.01, 1.32] | -12.0% | 244 | 49.3 | $0 |
| bench:buy_and_hold | +44.7% | +7.8% | 0.44 | [-0.19, 1.08] | -31.9% | 0 | 0.0 | $0 |
| bench:sma_200 | +40.2% | +7.1% | 0.52 | [-0.17, 1.15] | -21.1% | 0 | 0.0 | $0 |
| bench:random_entry | +45.1% | +7.8% | 0.55 | [-0.13, 1.20] | -25.5% | 0 | 0.0 | $0 |
