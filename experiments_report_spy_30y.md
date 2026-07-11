# Strategy experiment report

Data: **SPY via Yahoo (30y, adjusted)**, 7000 bars (1998-09-10 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| regime_smooth_30 | +268.1% | +5.0% | 0.86 | [0.56, 1.19] | -13.2% | 1527 | 205.9 | $0 |
| tc_confirm3 | +259.1% | +4.9% | 0.84 | [0.54, 1.13] | -10.3% | 301 | 99.0 | $0 |
| tc_confirm3_hi50 | +259.1% | +4.9% | 0.84 | [0.54, 1.13] | -10.3% | 301 | 99.0 | $0 |
| tc_confirm3_brake | +255.8% | +4.9% | 0.83 | [0.53, 1.12] | -11.1% | 157 | 99.0 | $0 |
| tc_vol15 | +209.8% | +4.3% | 0.77 | [0.45, 1.06] | -12.5% | 409 | 165.5 | $0 |
| tc_vol15_nocb | +209.8% | +4.3% | 0.77 | [0.45, 1.06] | -12.5% | 409 | 165.5 | $0 |
| trend_core | +208.9% | +4.3% | 0.76 | [0.45, 1.06] | -12.7% | 407 | 165.8 | $0 |
| tc_hi50 | +208.9% | +4.3% | 0.76 | [0.45, 1.06] | -12.7% | 407 | 165.8 | $0 |
| trend_core_nocb | +208.9% | +4.3% | 0.76 | [0.45, 1.06] | -12.7% | 407 | 165.8 | $0 |
| regime_defaults | +208.8% | +4.3% | 0.70 | [0.39, 1.02] | -14.9% | 1449 | 486.4 | $0 |
| legacy_churn | +157.4% | +3.6% | 0.54 | [0.24, 0.86] | -25.1% | 6688 | 454.7 | $0 |
| bench:buy_and_hold | +791.4% | +8.5% | 0.52 | [0.23, 0.81] | -55.2% | 0 | 0.0 | $0 |
| bench:sma_200 | +495.0% | +6.9% | 0.63 | [0.32, 0.92] | -24.1% | 0 | 0.0 | $0 |
| bench:random_entry | +290.4% | +5.2% | 0.43 | [0.12, 0.75] | -47.4% | 0 | 0.0 | $0 |
