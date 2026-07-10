# Strategy experiment report

Data: **SPY via Yahoo (30y, adjusted)**, 7000 bars (1998-09-09 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tc_confirm3 | +249.5% | +4.8% | 0.82 | [0.52, 1.13] | -10.5% | 302 | 96.5 | $0 |
| tc_confirm3_hi50 | +249.5% | +4.8% | 0.82 | [0.52, 1.13] | -10.5% | 302 | 96.5 | $0 |
| tc_confirm3_brake | +250.0% | +4.8% | 0.82 | [0.52, 1.12] | -11.0% | 153 | 95.5 | $0 |
| trend_core | +212.3% | +4.3% | 0.77 | [0.46, 1.07] | -10.1% | 404 | 161.2 | $0 |
| tc_hi50 | +212.3% | +4.3% | 0.77 | [0.46, 1.07] | -10.1% | 404 | 161.2 | $0 |
| trend_core_nocb | +212.3% | +4.3% | 0.77 | [0.46, 1.07] | -10.1% | 404 | 161.2 | $0 |
| regime_smooth_30 | +180.5% | +3.9% | 0.77 | [0.47, 1.08] | -20.6% | 1429 | 154.4 | $0 |
| tc_vol15 | +209.9% | +4.3% | 0.77 | [0.46, 1.07] | -10.8% | 406 | 159.9 | $0 |
| tc_vol15_nocb | +209.9% | +4.3% | 0.77 | [0.46, 1.07] | -10.8% | 406 | 159.9 | $0 |
| legacy_churn | +196.1% | +4.1% | 0.71 | [0.40, 1.01] | -20.2% | 6611 | 475.1 | $0 |
| regime_defaults | +151.2% | +3.5% | 0.65 | [0.35, 0.95] | -21.0% | 1558 | 411.1 | $0 |
| bench:buy_and_hold | +795.0% | +8.5% | 0.52 | [0.23, 0.82] | -55.2% | 0 | 0.0 | $0 |
| bench:sma_200 | +492.2% | +6.9% | 0.63 | [0.33, 0.93] | -24.1% | 0 | 0.0 | $0 |
| bench:random_entry | +154.4% | +3.5% | 0.32 | [0.01, 0.66] | -50.9% | 0 | 0.0 | $0 |
