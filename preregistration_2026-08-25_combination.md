# Pre-registration #3 — sleeve 30% + vol-target 20% (the combination)

Written 2026-08-25, before running any combination configuration. States the candidate,
why this specific pair, and the accept/reject rule.

## What this tests

`analysis_report_2026-08-25_k1_options.md` observed that the two available levers do
different jobs and are therefore not substitutes:

- **Sleeve reduction is static.** It scales the levered position down everywhere,
  proportionally, whether or not anything is going wrong.
- **The book vol-target is reactive.** It binds only once *realised* volatility rises,
  which is precisely the dotcom grind's signature.

The mechanism claim is that combining them is more *efficient* than either alone — that
for a given amount of return given up, the pair buys more tail reduction than either
lever buys by itself. This pre-registration tests that claim once, on one configuration.

## Candidate

**Sleeve weight 0.30 (core_scale 0.70), plus `book_vol_target = 0.20`,
`vol_target_lookback = 21`.**

### Why this exact pair, and why it is not a search

Both components are specified by rule, not chosen by scanning combinations:

- **`book_vol_target = 0.20`** is the candidate already accepted on this holdout in
  `preregistration_2026-08-24_book_vol_target.md`. It is carried over unchanged — not
  re-picked.
- **Sleeve 0.30** is exactly one notch down from the deployed 0.40, on the same
  0.40/0.30/0.20/0.10 ladder `scripts/sleeve_weight_scan.py` already reports. One notch
  is the smallest non-zero move available; taking the smallest move is the rule.

So this is "the accepted vol-target, plus the smallest available sleeve reduction". No
2-D scan was run, and none will be: `config.py` forbids re-optimising this split by grid
search, and picking the best cell of a grid is the failure mode the frozen holdout
exists to prevent. If this pair fails, the honest conclusion is recorded as a failure —
it is not the first cell of a search for a pair that passes.

**Epistemic status:** weaker than pre-registration #1, comparable to #2. The individual
behaviour of both components on this holdout is already known to me; what is *not* known
is how they interact. That interaction is the only thing this run can legitimately
inform.

## Baseline

Deployed configuration: sleeve 0.40 / core_scale 0.60, `book_vol_target = 0.0`. Same
span, same pinned costs (2 bp slippage, 0 bp commission), ^IRX cash series.

## Data and span

Unchanged from pre-registrations #1 and #2. Full history from the natural common-index
start (no `start_date` clip); metrics computed only on
`index >= settings.config.HOLDOUT_START` (2021-01-01). Pre-holdout bars warm the SMA-200
and the 21-day vol lookback only.

The reconstruction (`scripts/dotcom_reconstruction.py`, 2000-2026, synthetic pre-2006
QLD) is reported alongside but is **not** part of the accept/reject rule — same as in
#1 and #2, where it was reported separately after the holdout verdict.

## Accept / reject rule — copied verbatim from pre-registrations #1 and #2

Computed on the holdout slice only. `maxDD` compared as **magnitudes**.

**REJECT** if any of:
- candidate `maxDD` magnitude is worse (larger) than baseline's, or
- candidate `Sharpe` is more than 0.05 below baseline's, or
- candidate `CAGR` is more than 150 bp below baseline's.

**ACCEPT** otherwise.

## The secondary question, stated in advance

Whatever the verdict, one further comparison is fixed here so it cannot be
constructed after seeing the result. **Is the combination more efficient than the
single-lever options?** Efficiency is judged by comparing this candidate against the
already-measured rows in `analysis_report_2026-08-25_k1_options.md` at comparable
holdout CAGR cost: if the combination delivers a *smaller* reconstructed maxDD than any
single-lever option of similar or lower CAGR give-up, the mechanism claim is supported.
If it lands on or above that line, the two levers are substitutes after all and the
"combination" framing should be dropped from future work.

This comparison is descriptive. It carries no accept/reject power and cannot rescue a
rejected candidate.

---


## Result — 2026-08-25, `scripts/vol_target_holdout_eval.py --target 0.20 --sleeve 0.30`

| | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| Baseline (sleeve 40%, no vol-target) | 17.90% | 16.5% | 1.08 | −21.31% |
| Candidate (sleeve 30% + vol-target 20%) | 15.66% | 13.8% | 1.12 | −18.31% |
| Delta | **−2.24pp** | −2.7pp | **+0.04** | **−3.00pp (magnitude, better)** |

Holdout: 2021-01-04 .. 2026-08-10 (1406 bars), pinned costs, ^IRX cash.

**VERDICT: REJECT** — on the CAGR limit alone, as with #2. Sharpe improved (+0.04, the
best of any candidate so far) and maxDD magnitude improved by 3.00 pp, but the CAGR
give-up is 224 bp against the 150 bp limit.

### Reconstruction (reported, not part of the rule)

| | recon CAGR | recon Sharpe | recon maxDD | HALT fires? |
|---|---:|---:|---:|---|
| Baseline | 9.56% | 0.61 | −52.22% | YES 2000-07-28 |
| Candidate | 8.93% | 0.69 | −38.30% | YES 2003-01-17 |

The combination does **not** clear the −35% HALT. −38.30% is a large improvement on
baseline's −52.22%, and the breach is delayed by 2.4 years, but the threshold is still
crossed.

### The secondary question: is the combination more efficient? — NO

This is the part worth reading, and it went against the hypothesis.

Ordering every measured configuration by what it costs on the holdout against the tail
it buys in the reconstruction:

| Configuration | Holdout CAGR give-up | Recon maxDD |
|---|---:|---:|
| Vol-target 20% alone | −76 bp | −41.58% |
| Sleeve 30% alone | −186 bp | −45.39% |
| **Sleeve 30% + vol-target 20%** | **−224 bp** | **−38.30%** |
| Vol-target 12% alone | −298 bp | −32.41% |
| Sleeve 20% alone | −377 bp | −37.80% |
| Sleeve 10% alone | −574 bp | −29.41% |

The combination beats both *sleeve-only* options handily — sleeve 30% costs 38 bp less
and lands 7.1 pp worse; sleeve 20% costs 153 bp **more** and lands only 0.5 pp better.
Sleeve-only reduction is dominated across the board.

But that is the wrong comparison. Against the *vol-target-only* options, the combination
loses. Interpolating linearly between the two measured pure-vol-target points — 20% at
(−76 bp, −41.58%) and 12% at (−298 bp, −32.41%) — a pure vol-target costing the
combination's 224 bp would land near **−35.5%**, roughly 2.8 pp better than the
combination's −38.30% at the same price.

Per the criterion fixed above ("if it lands on or above that line, the two levers are
substitutes after all and the 'combination' framing should be dropped"): **the mechanism
claim is refuted.** Adding a static sleeve cut on top of the vol-target is a less
efficient way to spend return than simply tightening the vol-target. The reactive lever
does the static lever's job better than the static lever does.

Caveat, stated plainly: that comparison rests on a two-point linear interpolation, not a
measured pure-vol-target run at 224 bp. Confirming it exactly would mean another holdout
evaluation, which is deliberately not being run — the frontier gap (2.8 pp) is
comfortably larger than the curvature a single interpolation step is likely to hide, and
each additional run spends the frozen holdout a little further.

### What all three pre-registrations jointly establish

| Candidate | CAGR give-up | Verdict | Clears HALT? |
|---|---:|---|---|
| Vol-target 20% | −76 bp | ACCEPT | no |
| Sleeve 30% + vol-target 20% | −224 bp | REJECT | no |
| Vol-target 12% | −298 bp | REJECT | yes |

Two conclusions, both firmer than any single run supports:

1. **The vol-target is the efficient lever; the sleeve weight is not.** Every
   sleeve-only configuration is dominated, and adding a sleeve cut to a vol-target makes
   the package worse per basis point spent. If the book is going to be de-risked, the
   vol-target is the instrument to do it with.
2. **The 150 bp bar and the K1 problem are mismatched.** The bar was written to screen
   drop-in tweaks and it does that correctly. But nothing that materially reduces this
   tail is a tweak: every configuration that closes K1 costs more than the bar allows,
   and the only one cheap enough to pass does not close it. Closing K1 means accepting a
   materially different risk/return profile — an owner decision the bar was never
   designed to make.

**No further combinations or targets will be tested without an explicit new mandate.**
Continuing to try configurations until one passes is a grid search conducted one commit
at a time, and the frozen holdout is spent a little more with every run.
