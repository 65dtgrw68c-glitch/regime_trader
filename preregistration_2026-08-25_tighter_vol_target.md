# Pre-registration #2 — tighter book vol target (12%)

Written 2026-08-25, before running the holdout evaluation for this candidate. States
the candidate, how its level was chosen, and the accept/reject rule.

## Epistemic status of this pre-registration — read first

This is **weaker evidence than `preregistration_2026-08-24_book_vol_target.md`**, and
the difference matters.

That first pre-registration picked its level (20%) from the original audit's in-sample
work and then tested it on data nobody had looked at. This one picks its level from the
dotcom-reconstruction sweep in `scripts/dotcom_reconstruction.py` — **a sweep I have
already seen** (recorded at the end of that first pre-registration file). So the level
here is not an independent discovery; it is a value selected because it satisfies a
design constraint on a scenario I have already examined.

That is still a legitimate way to set a risk parameter — you size a protection
mechanism against the worst case you can construct, then check what it costs in normal
times — but it is a *cost check*, not a *discovery*. The holdout can only tell me
whether this level is affordable. It cannot tell me the level is right, because the
level came from data I'd seen. Anyone reading this later should weight it accordingly.

To keep this as honest as the process allows: **the accept/reject rule below is copied
verbatim from pre-registration #1, unchanged.** It has not been loosened to help this
candidate pass. If 12% fails the same bar 20% passed, that is the answer.

## Candidate

Identical mechanism to pre-registration #1
(`core.portfolio_backtester.PortfolioBacktester.book_vol_target`, no-op at 0.0):

    scale = min(1, target_vol / realised_vol_21d)

**target_vol = 0.12**, `vol_target_lookback = 21`.

### Why 12% specifically

From the reconstruction sweep (2000-2026, synthetic pre-2006 QLD, real shipped
backtester):

| target_vol | reconstructed maxDD | clears the 35% HALT? |
|---:|---:|---|
| 20% (pre-reg #1's accepted candidate) | −41.58% | no — HALT fires 2002-12-04 |
| 15% | −36.77% | no — HALT fires 2003-01-17 |
| **12%** | **−32.41%** | **yes** |
| 10% | −29.10% | yes |

12% is **the loosest target that keeps the reconstructed book under the existing
`cb_max_drawdown_halt = 0.35`**, and loosest-that-clears is the selection rule because
every notch tighter costs return in normal markets (10% gives up a further ~43 bp CAGR
in the reconstruction for 3.3pp of drawdown that is already inside the threshold). That
rule is stated here before the holdout run, even though the table it reads from was
already visible.

## Baseline

Same book, same `PortfolioBacktester`, same span, same pinned costs
(`settings.config.BACKTEST`: 2 bp slippage, 0 bp commission), ^IRX cash series, with
`book_vol_target=0.0` — today's deployed configuration.

## Data and span

Unchanged from pre-registration #1: full history from the natural common-index start
(no `start_date` clip), metrics computed only on `index >= settings.config.HOLDOUT_START`
(2021-01-01). Pre-holdout bars serve only to warm the SMA-200 and the 21-day vol
lookback.

## Accept / reject rule — copied verbatim from pre-registration #1

Computed on the holdout slice only. `maxDD` compared as **magnitudes**.

**REJECT** if any of:
- candidate `maxDD` magnitude is worse (larger) than baseline's, or
- candidate `Sharpe` is more than 0.05 below baseline's, or
- candidate `CAGR` is more than 150 bp below baseline's.

**ACCEPT** otherwise.

An ACCEPT here means 12% is affordable on the holdout *and* clears the HALT in the
reconstruction — the combination pre-registration #1's 20% candidate could not deliver.
It would still not be an instruction to deploy: turning `book_vol_target` on in the
live config remains a separate owner decision, as does the sleeve weight.

A REJECT means the drawdown protection this level buys costs more in normal markets
than the project's own stated bar tolerates — in which case the honest conclusion is
that vol-targeting alone cannot close K1, and the sleeve weight (or the sleeve itself)
is the lever that has to move.

---

## Result — 2026-08-25, `scripts/vol_target_holdout_eval.py --target 0.12`

| | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| Baseline (`book_vol_target=0.0`) | 17.90% | 16.5% | 1.08 | −21.31% |
| Candidate (`book_vol_target=0.12`) | 14.92% | 13.5% | 1.10 | −17.47% |
| Delta | −2.98pp | −3.0pp | **+0.02** | **−3.84pp (magnitude, better)** |

Holdout: 2021-01-04 .. 2026-08-10 (1406 bars), pinned costs, ^IRX cash.

**VERDICT: REJECT** — on the CAGR limit alone. Sharpe *improved* (+0.02) and maxDD
magnitude improved by 3.84pp, but the CAGR give-up is 298 bp, roughly twice the
pre-registered 150 bp limit.

### Reading this result honestly

This is a REJECT under the pre-registered rule and it stands as one — the rule was
copied unchanged precisely so this outcome could not be argued away after the fact.

But the *shape* of the failure is worth stating plainly, because it is not the shape of
a bad idea. 12% vol-targeting improved every risk measure it touched: Sharpe up,
volatility down about a fifth, drawdown down by nearly four points, and in the
reconstruction it is the loosest level that keeps the book clear of its own HALT. It
failed on one axis only — it gives up return in a strong bull market, which is exactly
what a tight volatility cap is supposed to do in a period like 2021-2026.

So the rule and the candidate are disagreeing about something real: the 150 bp CAGR
limit was written for a *tweak*, and 12% vol-targeting is not a tweak — it is a
materially more conservative book. The rule correctly rejects it as a drop-in
replacement for the current configuration. It cannot tell you whether the more
conservative book is the better book, because that is a risk-appetite question, not a
backtest question.

### What this means for K1 — the honest summary

Across both pre-registrations, no vol-target level does both jobs:

| target | holdout verdict | reconstructed dotcom maxDD | clears 35% HALT? |
|---:|---|---:|---|
| 20% | ACCEPT (−76 bp CAGR) | −41.58% | no |
| 12% | REJECT (−298 bp CAGR) | −32.41% | yes |

The levels that are cheap enough to pass the project's own bar do not solve the tail;
the level that solves the tail is not cheap enough to pass. **Vol-targeting alone does
not close K1.** That is the finding, and it is a more useful one than either
pre-registration would have produced on its own.

What follows is an owner decision, not an analysis result. The remaining levers, with
what is known about each:

1. **Reduce the sleeve weight** (0.40 → 0.30 or 0.20). The audit measured this
   in-sample as Sharpe +0.03 / maxDD −3.4pp at 0.30, and it attacks K1 at its actual
   source — the leveraged instrument whose worst regime the sample cannot show. Not
   tested against the holdout or the reconstruction here. This is the lever I would
   examine next.
2. **Accept 12% and the return give-up**, treating the current book as too aggressive
   rather than the candidate as too expensive. Defensible; needs an explicit decision
   that a ~15% CAGR / 1.10 Sharpe / −17.5% maxDD book is preferred to a ~18% CAGR /
   1.08 Sharpe / −21.3% maxDD one. Note this is a genuinely close call on
   risk-adjusted terms — the candidate wins on Sharpe, Calmar and drawdown, and loses
   only on raw return.
3. **Raise `cb_max_drawdown_halt`** so the existing book has real headroom. Note what
   the project's own stated design rule implies here: the threshold was set to leave
   "~10 pp headroom over everything observed", and against the reconstruction the
   current book observes −52%. Applying that rule consistently would mean a threshold
   near 60%, which is not a protection mechanism any more. **This lever should be
   treated with suspicion** — it closes the finding by widening the tolerance rather
   than reducing the risk.
4. **Do nothing, and keep the sleeve.** Legitimate only if the owner explicitly accepts
   that a dotcom-shaped tech bear market would take the book to roughly −52% and then
   halt it permanently at the worst moment. K1 stays open, knowingly.
