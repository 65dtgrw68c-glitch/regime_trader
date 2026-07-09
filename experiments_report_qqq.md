# Strategy experiment report

Data: **QQQ via Alpaca (IEX feed)**, 1495 bars (2020-07-27 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| trend_vol_15 | +9.8% | +1.9% | 0.39 | -8.4% | 359 | 61.8 | $6,183 |
| trend_vol_12 | +7.5% | +1.5% | 0.33 | -7.7% | 365 | 52.9 | $5,286 |
| trend_vol_10 | +5.1% | +1.0% | 0.27 | -8.0% | 342 | 43.6 | $4,365 |
| legacy_churn | +6.2% | +1.2% | 0.21 | -11.1% | 909 | 68.5 | $6,854 |
| gated_rebalance | +5.1% | +1.0% | 0.18 | -11.4% | 302 | 62.4 | $6,235 |
| trend_filter | +4.7% | +0.9% | 0.17 | -11.5% | 286 | 61.5 | $6,145 |
| bench:buy_and_hold | +98.3% | +14.9% | 0.72 | -35.6% | 0 | 0.0 | $0 |
| bench:sma_200 | +102.5% | +15.4% | 1.00 | -19.6% | 0 | 0.0 | $0 |
| bench:random_entry | +18.4% | +3.5% | 0.30 | -41.2% | 0 | 0.0 | $0 |
