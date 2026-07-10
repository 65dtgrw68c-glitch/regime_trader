# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1496 bars (2018-11-01 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
Costs: commission=0.0000, slippage=0.0002 per fill (charged to equity)  
Execution: decisions on bar close fill at the NEXT bar's open (benchmarks use the SAME timing, costless); idle cash earns ^IRX daily series (flat fallback 2.0% p.a.), credited to strategy and sma_200/random benchmarks alike  
Exposure cap (RISK.max_position_size): 0.50 — strategy rows are capped at this fraction of equity, benchmarks run at 100%.  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| trend_core | +42.4% | +7.4% | 1.29 | [0.57, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_hi50 | +42.4% | +7.4% | 1.29 | [0.57, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_vol15 | +42.4% | +7.4% | 1.29 | [0.57, 2.01] | -7.3% | 72 | 20.1 | $0 |
| trend_core_nocb | +42.4% | +7.4% | 1.29 | [0.57, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_vol15_nocb | +42.4% | +7.4% | 1.29 | [0.57, 2.01] | -7.3% | 72 | 20.1 | $0 |
| tc_confirm3_brake | +42.0% | +7.4% | 1.27 | [0.50, 2.03] | -7.7% | 30 | 12.7 | $0 |
| tc_confirm3 | +41.4% | +7.3% | 1.27 | [0.50, 2.03] | -7.7% | 58 | 12.8 | $0 |
| tc_confirm3_hi50 | +41.4% | +7.3% | 1.27 | [0.50, 2.03] | -7.7% | 58 | 12.8 | $0 |
| legacy_churn | +38.0% | +6.7% | 1.12 | [0.42, 1.95] | -8.9% | 1234 | 63.6 | $0 |
| regime_defaults | +36.8% | +6.5% | 1.07 | [0.42, 1.86] | -8.9% | 278 | 60.5 | $0 |
| regime_smooth_30 | +34.1% | +6.1% | 1.07 | [0.42, 1.81] | -7.6% | 255 | 21.1 | $0 |
| bench:buy_and_hold | +82.4% | +12.9% | 0.80 | [0.17, 1.53] | -24.5% | 0 | 0.0 | $0 |
| bench:sma_200 | +65.4% | +10.7% | 0.95 | [0.22, 1.64] | -16.9% | 0 | 0.0 | $0 |
| bench:random_entry | +51.4% | +8.8% | 0.79 | [0.14, 1.53] | -18.2% | 0 | 0.0 | $0 |
