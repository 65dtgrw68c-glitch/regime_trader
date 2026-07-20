# Asset Universe Onboarding Process

This document describes how to validate and add new assets to the trading system. The process is designed to prevent overfitting, ensure structural robustness, and maintain clear audit trails.

## Core Principle

**One pinned strategy profile for all assets.** Never tune parameters per-asset. The current profile (`trend_core=True`, `trend_confirm_bars=3`, `vol_target=0.15`) was validated on SPY and QQQ across 30 years of history and is applied to all new candidates unchanged.

## Pre-Registration Checklist

Before running any harness, create a GitHub issue or discussion that documents:

1. **Asset ticker** (e.g., `GLD`)
2. **Asset class** (e.g., `gold`, `bonds`, `commod`)
3. **Data availability** — at least 15 years of daily OHLCV on Yahoo Finance
4. **Economic rationale** — why this asset diversifies the book (e.g., "GLD has 0.06 correlation to SPY in crises vs 0.95 for QQQ")
5. **Expected outcome** — rough Sharpe/DD range from a quick literature scan
6. **Acceptance criteria** (pre-fixed decision rule)

### Acceptance Criteria Template

```
Asset will be marked validated=True if ALL of the following are met on the 
harness run (PINNED profile, --yahoo, 20+ years, next-open fills, ^IRX cash):

1. Trend-Sharpe >= B&H-Sharpe − 0.05
2. Trend-MaxDD <= B&H-MaxDD
3. For diversifiers (gold, bonds): crisis correlation to SPY < 0.5
4. Total returns in the expected range (if known)
5. No structural breaks in recent 2 years

If ANY criterion fails, the asset stays validated=False pending investigation.
```

## Step 1: Harness Validation

Run the pinned profile on the candidate asset using `scripts/run_experiments.py`:

```bash
# Example: validate GLD with the pinned profile
python scripts/run_experiments.py --ticker GLD --yahoo --bars 7000

# Output: experiments_report_gld.md
# Check the walk-forward Sharpe, max DD, and recent performance
```

### What to Look For

- **Walk-forward Sharpe** (pinned profile vs B&H): should not degrade more than 0.05 from baseline
- **Max DD**: trend rule should improve or match the buy-and-hold drawdown
- **Structural stability**: look at the yearly table — no unexpected collapses
- **Recent 2y performance**: should show the same signal quality as the historical average

### If Acceptance Criteria Fail

Document why and store for future review. Do NOT adjust parameters. Instead:
- Is the signal fundamentally unsuitable for this asset? (e.g., vol products with contango decay)
- Is the cap too small to allow the signal to work? (try recalibrating the budget, not the profile)
- Is the data stale or incomplete? (check Yahoo for gaps)

## Step 2: Joint-Book Portfolio Check

Once individual harness passes, test the asset in a joint portfolio:

```bash
# Example: validate GLD + IEF together with the existing SPY/QQQ book
python scripts/portfolio_check.py --tickers SPY QQQ GLD IEF --bars 7000 --out experiments_report_4asset.md

# Output: experiments_report_4asset.md
# Check Sharpe/DD of the joint book vs the baseline SPY+QQQ portfolio
```

### Decision Rules

The joint portfolio should meet **at least one** of:

1. **Higher Sharpe** than the SPY+QQQ baseline (without significantly worse DD)
2. **Lower max DD** than SPY+QQQ (even if Sharpe is lower)
3. **Better risk-adjusted returns** in crisis windows (2008, 2020, 2022)

If the joint book gets worse on all three measures, the asset is not additive.

## Step 3: Update the Universe Config

Once harness passes AND joint-book criterion is met, update `settings/config.py`:

```python
UNIVERSE = {
    "assets": {
        # ... existing assets ...
        "GLD": {"asset_class": "gold", "validated": True},  # ← changed from False
        "IEF": {"asset_class": "bonds", "validated": True},
    },
    # ... rest of config ...
}
```

This automatically activates the asset in `TICKERS` list.

## Step 4: Test in Paper Trading

- Set the paper account to use `class_caps` that include the new asset
- Run for at least 2 weeks of live data (20 trading days)
- Monitor:
  - Order submission and fills
  - Risk manager approvals / rejections
  - Allocator behavior (weights trending, not whipsawing)
  - Joint drawdown vs expectations

## Live Deployment

Once paper-trading confirms behavior, the asset is ready for live trading:

```python
"GLD": {"asset_class": "gold", "validated": True}
```

The system will begin trading it on the next startup.

---

## Example: GLD Validation (from the 30y scan)

### Pre-Registration

- **Ticker:** GLD
- **Asset class:** gold
- **Data:** Yahoo, 2004–2026 (22 years)
- **Rationale:** 0.06 correlation to SPY overall, ≤0.22 in all crisis windows
- **Expected:** Sharpe 0.60–0.65, DD −30 to −40%
- **Criterion:** Trend Sharpe ≥ B&H − 0.05 AND Trend DD ≤ B&H

### Harness Result

```
GLD 30y (pinned, yahoo, next-open, 2 bps):
  B&H Sharpe: 0.63  Trend Sharpe: 0.61  ✓ (within 0.05)
  B&H DD: −46%      Trend DD: −34%      ✓ (better)
  Recent 2y: Sharpe 0.58                ✓ (stable)
→ ACCEPTED
```

### Joint-Book Result

```
SPY+QQQ+GLD (equal budgets, inverse vol):
  SPY+QQQ Sharpe: 0.75 / DD −20.8%
  SPY+QQQ+GLD Sharpe: 0.82 / DD −18.2%    ✓ (higher Sharpe, lower DD)
→ APPROVED for live
```

---

## Do-Not-Tune Guarantees

Once an asset is validated with the pinned profile:

- **No SMA-window tuning** per asset (always 200)
- **No confirm_bars tuning** per asset (always 3)
- **No vol_target tuning** per asset (always 0.15)
- **No stop-loss tuning** per asset (always 0)

These guarantees are the foundation of the anti-overfitting discipline. Violating them voids the entire validation and requires re-running from scratch on the original ticker set.

## Tracked Assets (Reference)

As of the 2026-07-10 scan:

| Ticker | Asset Class | Validated | Rationale | Status |
|--------|-------------|-----------|-----------|--------|
| SPY    | equity      | Yes       | Primary US large-cap | Live |
| QQQ    | equity      | Yes       | Primary US tech | Live |
| GLD    | gold        | Pending   | Crisis hedge, 0.06 corr | Harness: ✓ |
| IEF    | bonds       | Pending   | Duration hedge, −0.29 corr | Harness: ✓ |
| DBC    | commod      | Pending   | Inflation hedge, 2022 edge | Harness: ✓ |
| EFA    | equity      | Rejected  | 0.96 crisis corr → no diversification | – |
| EEM    | equity      | Rejected  | Weaker trend, same crisis corr as EFA | – |
| HYG    | credit      | Rejected  | Carry-driven, higher coupons than trend | – |
| VNQ    | realestate  | Rejected  | Trend not an edge | – |

---

## Audit Trail

Every asset addition is recorded:

1. **Pre-reg issue**: GitHub issue #XXX documents the decision rule
2. **Harness run**: `experiments_report_[ticker].md` with full backtest
3. **Joint-book run**: `experiments_report_[ticker]_combo.md` with portfolio impact
4. **Config commit**: Git commit message references pre-reg issue + harness results
5. **Paper-trading period**: Marked in logs and dashboard

This trail makes future decisions reproducible and defensible.
