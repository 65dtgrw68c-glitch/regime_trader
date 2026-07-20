#!/bin/bash
# validate_candidates.sh — run harness on GLD, IEF, DBC candidates
# Usage: bash scripts/validate_candidates.sh
# Output: experiments_report_[ticker].md for each

set -e

CANDIDATES=("GLD" "IEF" "DBC")
BARS=7000
SEED=42

echo "Starting candidate harness validation..."
echo "Profile: pinned (trend_core, confirm_bars=3, vol_target=0.15)"
echo "Data: Yahoo 30y, next-open fills, ^IRX cash yield"
echo ""

for ticker in "${CANDIDATES[@]}"; do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Validating $ticker (${BARS} bars)..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    python scripts/run_experiments.py \
        --ticker "$ticker" \
        --yahoo \
        --bars "$BARS" \
        --seed "$SEED" \
        --out "experiments_report_${ticker}_candidate.md"
    echo ""
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Harness validation complete."
echo ""
echo "Individual reports:"
for ticker in "${CANDIDATES[@]}"; do
    echo "  • experiments_report_${ticker}_candidate.md"
done
echo ""
echo "Next: review each report against the acceptance criteria in docs/UNIVERSE_ONBOARDING.md"
echo ""

# If all candidates passed, run the joint-book check
echo "Running joint-book portfolio check..."
python scripts/portfolio_check.py \
    --tickers SPY QQQ GLD IEF DBC \
    --bars "$BARS" \
    --out "experiments_report_candidates_joint.md"

echo ""
echo "Joint-book report: experiments_report_candidates_joint.md"
echo ""
echo "Validation complete. Update settings/config.py UNIVERSE if criteria met."
