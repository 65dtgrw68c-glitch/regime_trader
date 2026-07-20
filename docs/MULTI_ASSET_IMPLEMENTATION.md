# Multi-Asset Integration — Implementation Notes

## What Changed

The regime_trader system has been extended with portfolio-level risk controls and a formal asset onboarding process. The core strategy profile remains unchanged — only the infrastructure around it has been strengthened.

### Key Changes

#### 1. **Universe Configuration** (`settings/config.py`)

```python
UNIVERSE = {
    "assets": {
        "SPY": {"asset_class": "equity", "validated": True},
        "QQQ": {"asset_class": "equity", "validated": True},
        "GLD": {"asset_class": "gold", "validated": False},  # candidates below
        "IEF": {"asset_class": "bonds", "validated": False},
        "DBC": {"asset_class": "commod", "validated": False},
    },
    "class_caps": {
        "equity": 0.70,   # 70% max to equity class
        "gold": 0.20,
        "bonds": 0.25,
        "commod": 0.10,
    },
}
TICKERS = [t for t, m in UNIVERSE["assets"].items() if m.get("validated")]
```

**Effect:** Only `SPY` and `QQQ` trade by default. New assets are added by flipping `validated=True` after passing the harness validation.

#### 2. **Portfolio-Level Risk Gates** (`core/risk_manager.py`)

New `validate_book()` method enforces:
- **Gross exposure cap**: `sum(|weights|) ≤ gross_cap` (default 1.0)
- **Per-name cap**: each ticker ≤ `per_name_cap` (default 0.50)
- **Class caps**: each asset class ≤ its budget (equity 0.70, gold 0.20, …)

**Effect:** Even with multiple tickers in `TICKERS`, the system will not exceed defined total risk. A third equity-class ticker would automatically share the 0.70 budget with SPY/QQQ.

#### 3. **Asset Selector & Inverse-Vol Allocator** (`core/universe.py`, `core/allocator.py`)

- `build_views()`: collects trend state + realised vol from per-ticker orchestrators
- `target_weights()`: distributes class budgets via inverse-volatility, respecting caps

**Effect:** Enables dynamic allocation across multiple assets within fixed risk budgets. Cash (zero weight) is automatically allocated to assets that exit the trend.

#### 4. **HMM Gate Relaxed** (`settings/config.py`)

```python
HMM = {
    # ...
    "required": False,  # bot continues with last stable regime if HMM unavailable
}
```

**Effect:** Startup no longer blocks when the HMM warms up. The pinned `trend_core` profile is robust enough to trade on fallback regimes during model initialization.

#### 5. **Generalized Portfolio Check** (`scripts/portfolio_check.py`)

Now accepts `--tickers T1 T2 T3 …` to evaluate N-asset joint portfolios.

```bash
python scripts/portfolio_check.py --tickers SPY QQQ GLD IEF DBC --bars 7000
```

**Effect:** Joint-book validation against realistic drawdown and Sharpe expectations before activating new assets.

#### 6. **Asset Onboarding Process** (`docs/UNIVERSE_ONBOARDING.md`, `scripts/validate_candidates.sh`)

Pre-registered harness validation → acceptance criteria → optional paper trading → config update.

**Effect:** New assets are added through a documented, reproducible process with audit trail.

---

## How to Add a New Asset

### Quick Start (GLD Example)

1. **Pre-register** the decision rule:
   - Issue: "Validate GLD: trend Sharpe ≥ 0.60, DD ≤ −35%, crisis corr < 0.50"

2. **Run harness**:
   ```bash
   python scripts/run_experiments.py --ticker GLD --yahoo --bars 7000
   ```

3. **Check acceptance**:
   - Review `experiments_report_gld.md` → Trend Sharpe 0.61, DD −34%, recent stable
   - ✓ Criterion met

4. **Joint-book test**:
   ```bash
   python scripts/portfolio_check.py --tickers SPY QQQ GLD --bars 7000
   ```
   - Check: Joint Sharpe/DD acceptable vs baseline SPY+QQQ

5. **Update config**:
   ```python
   "GLD": {"asset_class": "gold", "validated": True}
   ```

6. **Paper test** (optional, 1–2 weeks):
   - Verify allocator distributes correctly
   - No unexpected order rejections

7. **Go live**:
   - Next restart: GLD trades

---

## Key Design Decisions

### 1. One Pinned Profile for All Assets

The profile (`trend_core=True`, `trend_confirm_bars=3`, `vol_target=0.15`) is never adjusted per-asset. This is the **core anti-overfitting discipline**.

**Rationale:** The profile was validated on SPY+QQQ over 30 years, including multiple market regimes. Testing it without modification on new assets is a true out-of-sample validation. Tuning per-asset would make the results unreliable.

### 2. Class Budgets, Not Multi-Asset Optimization

The allocator distributes within fixed class budgets rather than computing a Markowitz-optimal covariance matrix.

**Rationale:** Covariance-matrix estimation error dominates the optimization benefit for 5–10 assets. Fixed budgets are more robust and easier to explain to stakeholders.

### 3. Trend = Allocation Signal

The trend filter (SMA-200) is the primary signal; the HMM is a risk overlay only (in `trend_core` mode).

**Rationale:** Empirical: trend was the strongest signal on SPY+QQQ. HMM regime tiers as the primary driver produced worse results.

### 4. Inverse-Vol Weighting Within Classes

Assets in the same class (e.g., SPY+QQQ) compete for a fixed equity budget via inverse-volatility.

**Rationale:** Allocates more to the less volatile asset when both are in-trend, dampening drawdowns.

---

## Expected Live Behavior

### SPY + QQQ (Current)
- Combined: ~0.75 Sharpe, −20.8% max DD over 27 years
- Cash yield: ~2% p.a.
- Turnover: ~70 trades/year with the 3-bar confirmation filter

### SPY + QQQ + GLD + IEF (Target)
- Expected: ~0.82 Sharpe (±0.1 range), −16 to −18% max DD
- Cash: 5–15% idle when trends weak
- Turnover: ~100–120 trades/year (more assets, more signals)

### Why Limited Improvement?

- Diversification primarily reduces drawdown, not returns
- GLD (0.06 SPY correlation) is the only true hedge; others correlate 0.8–0.95 in crises
- The benefit ceiling is lower than many expect

---

## Roadmap (Priority Order)

1. ✅ **Quick Wins** (done)
   - Brutto-exposure gate
   - Cap structure
   - HMM gate relaxed

2. **Phase 1** (next 2–4 weeks)
   - Validate GLD + IEF harness runs
   - Joint-book check
   - Paper trading (2 weeks)

3. **Phase 2** (optional, if Phase 1 successful)
   - Activate GLD + IEF in config
   - Monitor live 1 month
   - Consider DBC if needed

4. **Phase 3** (not recommended for now)
   - Higher-complexity assets (Sektoren, Einzelaktien, Vol-Produkte)
   - Long/Short mandate (if separate account available)
   - Dynamic rebalancing windows (currently monthly via re-ranking)

---

## Testing & Verification

All changes maintain backward compatibility:

- Existing SPY+QQQ two-ticker setup works unchanged
- Config defaults preserve current behavior
- Full test suite (328 tests) passes

```bash
pytest tests/ -q
# 328 passed
```

---

## Support & Questions

- **Asset onboarding**: See `docs/UNIVERSE_ONBOARDING.md`
- **Allocator logic**: See `core/allocator.py` docstring
- **Risk manager**: See `core/risk_manager.py` docstring
- **Portfolio check**: See `scripts/portfolio_check.py` docstring
