# Stop-loss / take-profit sweep

Per-trade stop/TP sweep on the pinned profile `{'trend_core': True, 'trend_confirm_bars': 3, 'vol_target': 0.15}`, cap 0.50, walk-forward 252/126, seed 42; cash yield ^IRX series; next-open fills; stops simulated intraday vs bar low/high (gap-through → open fill).

## SPY (1998-09-10 … 2026-07-10, 7000 bars)

| Config | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Stop exits | TP exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (off) | +259.8% | +4.9% | 0.84 | [0.55, 1.13] | -10.3% | 303 | 0 | 0 |
| sl 2% (legacy value) | +183.5% | +4.0% | 0.70 | [0.37, 1.02] | -14.7% | 524 | 130 | 0 |
| sl 2% + tp 4% (legacy pair) | +178.1% | +3.9% | 0.72 | [0.41, 1.02] | -13.9% | 941 | 239 | 148 |
| sl 5% | +251.6% | +4.8% | 0.83 | [0.53, 1.12] | -13.9% | 332 | 20 | 0 |
| sl 10% | +247.8% | +4.8% | 0.82 | [0.53, 1.12] | -10.3% | 303 | 2 | 0 |
| sl 15% (disaster stop) | +259.8% | +4.9% | 0.84 | [0.55, 1.13] | -10.3% | 303 | 0 | 0 |
| tp 4% only | +266.8% | +5.0% | 0.86 | [0.57, 1.16] | -10.3% | 452 | 0 | 100 |

## QQQ (1999-03-10 … 2026-07-10, 6876 bars)

| Config | Total return | CAGR | Sharpe | Sharpe 90% CI | Max DD | Trades | Stop exits | TP exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (off) | +367.3% | +6.0% | 0.81 | [0.47, 1.09] | -14.7% | 307 | 0 | 0 |
| sl 2% (legacy value) | +318.8% | +5.6% | 0.77 | [0.42, 1.08] | -15.1% | 608 | 180 | 0 |
| sl 2% + tp 4% (legacy pair) | +193.7% | +4.2% | 0.62 | [0.29, 0.93] | -16.7% | 1346 | 394 | 224 |
| sl 5% | +342.7% | +5.8% | 0.78 | [0.45, 1.07] | -15.2% | 382 | 48 | 0 |
| sl 10% | +314.8% | +5.6% | 0.74 | [0.40, 1.04] | -18.0% | 328 | 10 | 0 |
| sl 15% (disaster stop) | +327.8% | +5.7% | 0.76 | [0.41, 1.05] | -14.9% | 315 | 5 | 0 |
| tp 4% only | +369.2% | +6.1% | 0.81 | [0.48, 1.11] | -15.4% | 543 | 0 | 152 |
