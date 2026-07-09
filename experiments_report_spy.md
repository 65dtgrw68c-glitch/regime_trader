# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1496 bars (2018-11-01 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| trend_core | +31.7% | +5.7% | 0.56 | -22.4% | 65 | 40.4 | $4,037 |
| tc_vol15 | +30.2% | +5.5% | 0.56 | -18.9% | 101 | 38.9 | $3,888 |
| tc_confirm3_brake | +11.2% | +2.2% | 0.25 | -23.8% | 22 | 20.7 | $2,071 |
| tc_confirm3 | +11.2% | +2.2% | 0.25 | -23.8% | 44 | 20.7 | $2,072 |
| tc_hi50 | +9.3% | +1.8% | 0.25 | -20.1% | 96 | 48.1 | $4,807 |
| legacy_churn | -0.9% | -0.2% | -0.01 | -12.0% | 1244 | 78.7 | $7,874 |
| tc_confirm3_hi50 | -3.8% | -0.8% | -0.05 | -21.3% | 76 | 33.2 | $3,319 |
| regime_smooth_30 | -3.2% | -0.7% | -0.13 | -12.2% | 288 | 20.6 | $2,059 |
| regime_defaults | -6.7% | -1.4% | -0.28 | -12.5% | 355 | 68.0 | $6,799 |
| bench:buy_and_hold | +70.4% | +11.4% | 0.72 | -25.4% | 0 | 0.0 | $0 |
| bench:sma_200 | +48.0% | +8.3% | 0.77 | -20.6% | 0 | 0.0 | $0 |
| bench:random_entry | +3.6% | +0.7% | 0.12 | -21.3% | 0 | 0.0 | $0 |
