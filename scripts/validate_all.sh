#!/bin/bash
# validate_all.sh — minimal reproducible validation gate
#
# Default:
#   - run the full test suite only
#
# Optional:
#   --portfolio   run production portfolio check
#   --candidates  run slower candidate onboarding harness
#   --bars N      set bar count for portfolio/candidate checks
#
# This script does not change trading logic. It is a safety/reproducibility gate.

set -euo pipefail

RUN_PORTFOLIO=0
RUN_CANDIDATES=0
BARS=2000

while [ "$#" -gt 0 ]; do
    case "$1" in
        --portfolio)
            RUN_PORTFOLIO=1
            shift
            ;;
        --candidates)
            RUN_CANDIDATES=1
            RUN_PORTFOLIO=1
            shift
            ;;
        --bars)
            BARS="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: bash scripts/validate_all.sh [--portfolio] [--candidates] [--bars N]"
            exit 2
            ;;
    esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Running test suite"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pytest -q

if [ "$RUN_PORTFOLIO" -eq 1 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Running production portfolio check (${BARS} bars)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    python scripts/portfolio_check.py \
        --tickers SPY QQQ GLD IEF \
        --bars "$BARS" \
        --out experiments_report_portfolio_validation.md

    echo ""
    echo "Portfolio report:"
    echo "  - experiments_report_portfolio_validation.md"
fi

if [ "$RUN_CANDIDATES" -eq 1 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Running candidate validation"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    bash scripts/validate_candidates.sh
fi

echo ""
echo "Validation complete."
