# Strategy experiment report

Data: **SPY via Alpaca (IEX feed)**, 1496 bars (2018-11-01 … 2026-07-09)  
Walk-forward: train=252 / test=126, seed=42  
⚠️ Benchmarks ignore costs. Do not tune until the best row looks good — confirm any winner out-of-sample before going live.

| Variant | Total return | CAGR | Sharpe | Max DD | Trades | Turnover× | Est. commission |
|---|---:|---:|---:|---:|---:|---:|---:|
| gated_rebalance | -3.7% | -0.8% | -0.19 | -7.6% | 301 | 53.7 | $5,374 |
| legacy_churn | -4.5% | -0.9% | -0.24 | -8.2% | 1059 | 61.4 | $6,141 |
| trend_vol_12 | -4.9% | -1.0% | -0.35 | -8.1% | 319 | 47.9 | $4,789 |
| trend_vol_10 | -4.7% | -1.0% | -0.38 | -7.3% | 323 | 43.2 | $4,319 |
| trend_filter | -6.4% | -1.3% | -0.38 | -10.9% | 308 | 52.9 | $5,285 |
| trend_vol_15 | -7.7% | -1.6% | -0.52 | -10.3% | 286 | 47.5 | $4,753 |
| bench:buy_and_hold | +70.3% | +11.4% | 0.72 | -25.4% | 0 | 0.0 | $0 |
| bench:sma_200 | +47.9% | +8.3% | 0.77 | -20.6% | 0 | 0.0 | $0 |
| bench:random_entry | +3.6% | +0.7% | 0.12 | -21.3% | 0 | 0.0 | $0 |
