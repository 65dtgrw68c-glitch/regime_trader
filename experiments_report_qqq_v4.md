# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1496 bars (2020-07-27 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_brake | +62.0% | +10.3% | 1.30 | [0.58, 2.02] | -7.3% | 25 | 8.2 | $0 |
| tc_confirm3 | +61.7% | +10.2% | 1.30 | [0.58, 2.02] | -7.2% | 48 | 8.3 | $0 |
| tc_confirm3_hi50 | +61.7% | +10.2% | 1.30 | [0.58, 2.02] | -7.2% | 48 | 8.3 | $0 |
| tc_vol15 | +56.0% | +9.4% | 1.21 | [0.47, 1.93] | -9.3% | 64 | 15.4 | $0 |
| tc_vol15_nocb | +56.0% | +9.4% | 1.21 | [0.47, 1.93] | -9.3% | 64 | 15.4 | $0 |
| trend_core | +55.7% | +9.4% | 1.20 | [0.47, 1.92] | -9.4% | 63 | 15.5 | $0 |
| tc_hi50 | +55.7% | +9.4% | 1.20 | [0.47, 1.92] | -9.4% | 63 | 15.5 | $0 |
| trend_core_nocb | +55.7% | +9.4% | 1.20 | [0.47, 1.92] | -9.4% | 63 | 15.5 | $0 |
| legacy_churn | +40.5% | +7.1% | 0.96 | [0.19, 1.74] | -16.1% | 1234 | 64.2 | $0 |
| regime_smooth_30 | +33.8% | +6.1% | 0.91 | [0.17, 1.61] | -15.6% | 252 | 18.8 | $0 |
| regime_defaults | +33.2% | +6.0% | 0.82 | [0.06, 1.58] | -17.9% | 325 | 60.0 | $0 |
| bench:buy_and_hold | +105.6% | +15.7% | 0.75 | [0.09, 1.44] | -35.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +106.5% | +15.8% | 1.03 | [0.31, 1.73] | -18.9% | 0 | 0.0 | $0 |
| bench:random_entry | +178.9% | +23.1% | 1.32 | [0.63, 2.03] | -26.8% | 0 | 0.0 | $0 |
