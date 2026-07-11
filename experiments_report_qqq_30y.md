# Strategy experiment report

Data: **QQQ via Yahoo (30y, adjusted)**, 6876 bars (1999-03-10 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| regime_smooth_30 | +261.3% | +5.0% | 0.80 | [0.49, 1.11] | -16.2% | 1447 | 174.5 | $0 |
| tc_vol15 | +324.5% | +5.7% | 0.76 | [0.44, 1.04] | -14.6% | 412 | 160.1 | $0 |
| tc_vol15_nocb | +324.5% | +5.7% | 0.76 | [0.44, 1.04] | -14.6% | 412 | 160.1 | $0 |
| tc_confirm3_brake | +314.7% | +5.6% | 0.71 | [0.36, 1.01] | -21.0% | 169 | 80.2 | $0 |
| tc_confirm3 | +302.1% | +5.4% | 0.70 | [0.34, 1.01] | -21.5% | 293 | 81.6 | $0 |
| tc_confirm3_hi50 | +302.1% | +5.4% | 0.70 | [0.34, 1.01] | -21.5% | 293 | 81.6 | $0 |
| trend_core | +262.1% | +5.0% | 0.65 | [0.30, 0.95] | -27.2% | 404 | 142.6 | $0 |
| tc_hi50 | +262.1% | +5.0% | 0.65 | [0.30, 0.95] | -27.2% | 404 | 142.6 | $0 |
| trend_core_nocb | +262.1% | +5.0% | 0.65 | [0.30, 0.95] | -27.2% | 404 | 142.6 | $0 |
| regime_defaults | +196.0% | +4.2% | 0.64 | [0.32, 0.94] | -20.7% | 1557 | 413.5 | $0 |
| legacy_churn | +200.9% | +4.3% | 0.58 | [0.27, 0.88] | -19.8% | 6571 | 456.9 | $0 |
| bench:buy_and_hold | +673.9% | +8.1% | 0.43 | [0.14, 0.72] | -83.0% | 0 | 0.0 | $0 |
| bench:sma_200 | +695.7% | +8.2% | 0.56 | [0.24, 0.85] | -50.0% | 0 | 0.0 | $0 |
| bench:random_entry | +450.9% | +6.7% | 0.44 | [0.14, 0.74] | -54.4% | 0 | 0.0 | $0 |
