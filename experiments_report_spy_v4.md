# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1497 bars (2018-11-01 … 2026-07-10)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| trend_core | +42.7% | +7.5% | 1.30 | [0.58, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_hi50 | +42.7% | +7.5% | 1.30 | [0.58, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_vol15 | +42.7% | +7.5% | 1.30 | [0.58, 2.01] | -7.3% | 72 | 20.1 | $0 |
| trend_core_nocb | +42.7% | +7.5% | 1.30 | [0.58, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_vol15_nocb | +42.7% | +7.5% | 1.30 | [0.58, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_confirm3_brake | +42.3% | +7.4% | 1.28 | [0.53, 2.03] | -7.7% | 30 | 12.7 | $0 |
| tc_confirm3 | +41.8% | +7.3% | 1.27 | [0.52, 2.03] | -7.7% | 58 | 12.8 | $0 |
| tc_confirm3_hi50 | +41.8% | +7.3% | 1.27 | [0.52, 2.03] | -7.7% | 58 | 12.8 | $0 |
| legacy_churn | +38.3% | +6.8% | 1.12 | [0.44, 1.95] | -8.9% | 1235 | 63.8 | $0 |
| regime_defaults | +37.1% | +6.6% | 1.08 | [0.44, 1.85] | -8.9% | 279 | 60.6 | $0 |
| regime_smooth_30 | +34.4% | +6.2% | 1.07 | [0.43, 1.82] | -7.6% | 255 | 21.1 | $0 |
| bench:buy_and_hold | +83.2% | +13.0% | 0.81 | [0.19, 1.51] | -24.5% | 0 | 0.0 | $0 |
| bench:sma_200 | +66.1% | +10.8% | 0.96 | [0.24, 1.67] | -16.9% | 0 | 0.0 | $0 |
| bench:random_entry | +52.0% | +8.9% | 0.80 | [0.12, 1.53] | -18.2% | 0 | 0.0 | $0 |
