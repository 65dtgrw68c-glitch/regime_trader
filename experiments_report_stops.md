# Stop-loss / take-profit sweep

Per-trade stop/TP sweep on the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50, walk-forward 252/126, seed 42; cash yield ^IRX series; next-open fills; stops simulated intraday vs bar low/high (gap-through → open fill).

## SPY (1998-09-10 … 2026-07-10, 7000 bars)

| Config | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Stop exits | TP exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (off) | +258.6% | +4.9% | 0.84 | [0.55, 1.13] | -10.5% | 302 | 0 | 0 |
| sl 2% (legacy value) | +188.8% | +4.0% | 0.71 | [0.39, 1.04] | -14.7% | 517 | 128 | 0 |
| sl 2% + tp 4% (legacy pair) | +180.3% | +3.9% | 0.72 | [0.42, 1.03] | -13.2% | 936 | 237 | 148 |
| sl 5% | +260.0% | +4.9% | 0.85 | [0.55, 1.14] | -13.9% | 329 | 20 | 0 |
| sl 10% | +246.8% | +4.8% | 0.82 | [0.52, 1.11] | -10.5% | 302 | 2 | 0 |
| sl 15% (disaster stop) | +258.6% | +4.9% | 0.84 | [0.55, 1.13] | -10.5% | 302 | 0 | 0 |
| tp 4% only | +265.7% | +5.0% | 0.86 | [0.57, 1.16] | -10.5% | 451 | 0 | 100 |

## QQQ (1999-03-10 … 2026-07-10, 6876 bars)

| Config | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Stop exits | TP exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (off) | +380.3% | +6.2% | 0.84 | [0.50, 1.14] | -14.7% | 294 | 0 | 0 |
| sl 2% (legacy value) | +320.4% | +5.6% | 0.77 | [0.43, 1.09] | -15.1% | 588 | 174 | 0 |
| sl 2% + tp 4% (legacy pair) | +204.1% | +4.3% | 0.65 | [0.31, 0.95] | -17.7% | 1267 | 365 | 215 |
| sl 5% | +367.1% | +6.0% | 0.82 | [0.49, 1.12] | -14.2% | 355 | 42 | 0 |
| sl 10% | +368.5% | +6.1% | 0.82 | [0.47, 1.14] | -14.7% | 302 | 6 | 0 |
| sl 15% (disaster stop) | +355.1% | +5.9% | 0.81 | [0.47, 1.10] | -14.9% | 299 | 4 | 0 |
| tp 4% only | +368.4% | +6.1% | 0.83 | [0.48, 1.12] | -15.4% | 521 | 0 | 146 |
