# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3_brake | +61.7% | +10.2% | 1.30 | [0.57, 2.02] | -7.3% | 25 | 8.2 | $0 |
| tc_confirm3 | +61.4% | +10.2% | 1.30 | [0.57, 2.02] | -7.2% | 48 | 8.3 | $0 |
| tc_confirm3_hi50 | +61.4% | +10.2% | 1.30 | [0.57, 2.02] | -7.2% | 48 | 8.3 | $0 |
| tc_vol15 | +55.8% | +9.4% | 1.20 | [0.48, 1.91] | -9.3% | 64 | 15.4 | $0 |
| tc_vol15_nocb | +55.8% | +9.4% | 1.20 | [0.48, 1.91] | -9.3% | 64 | 15.4 | $0 |
| trend_core | +55.4% | +9.4% | 1.19 | [0.47, 1.90] | -9.4% | 63 | 15.5 | $0 |
| tc_hi50 | +55.4% | +9.4% | 1.19 | [0.47, 1.90] | -9.4% | 63 | 15.5 | $0 |
| trend_core_nocb | +55.4% | +9.4% | 1.19 | [0.47, 1.90] | -9.4% | 63 | 15.5 | $0 |
| legacy_churn | +35.9% | +6.4% | 0.91 | [0.10, 1.70] | -18.8% | 1192 | 59.4 | $0 |
| regime_smooth_30 | +33.7% | +6.1% | 0.91 | [0.18, 1.61] | -15.6% | 252 | 18.8 | $0 |
| regime_defaults | +30.0% | +5.5% | 0.80 | [-0.00, 1.60] | -19.8% | 308 | 55.8 | $0 |
| bench:buy_and_hold | +104.9% | +15.7% | 0.75 | [0.09, 1.44] | -35.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +105.9% | +15.8% | 1.02 | [0.32, 1.72] | -18.9% | 0 | 0.0 | $0 |
| bench:random_entry | +177.2% | +23.0% | 1.31 | [0.61, 2.04] | -26.8% | 0 | 0.0 | $0 |
