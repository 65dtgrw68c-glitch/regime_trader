# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| hysteresis_5 | +21.3% | +4.0% | 0.64 | -11.6% | 288 | 61.5 | $6,152 |
| trend_vol_15 | +9.8% | +1.9% | 0.39 | -8.4% | 359 | 61.9 | $6,188 |
| trend_vol_12 | +7.5% | +1.5% | 0.33 | -7.7% | 365 | 52.9 | $5,290 |
| trend_vol_10 | +5.1% | +1.0% | 0.27 | -8.0% | 342 | 43.7 | $4,368 |
| legacy_churn | +6.2% | +1.2% | 0.21 | -11.1% | 909 | 68.6 | $6,864 |
| gated_rebalance | +5.1% | +1.0% | 0.18 | -11.4% | 302 | 62.4 | $6,235 |
| brake_15 | +4.9% | +1.0% | 0.18 | -11.5% | 285 | 61.6 | $6,158 |
| trend_filter | +4.7% | +0.9% | 0.17 | -11.5% | 286 | 61.5 | $6,145 |
| all_dampers | -1.0% | -0.2% | -0.02 | -13.6% | 199 | 18.3 | $1,829 |
| smooth_30 | -2.1% | -0.4% | -0.04 | -13.3% | 248 | 22.9 | $2,285 |
| bench:buy_and_hold | +98.5% | +14.9% | 0.72 | -35.6% | 0 | 0.0 | $0 |
| bench:sma_200 | +102.6% | +15.4% | 1.00 | -19.6% | 0 | 0.0 | $0 |
| bench:random_entry | +18.5% | +3.5% | 0.30 | -41.2% | 0 | 0.0 | $0 |
