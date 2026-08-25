# K1 — the sleeve tail risk: what every available lever actually costs

2026-08-25. Consolidates the five measurements run against finding K1 of the
2026-08-24 audit. **This memo does not choose. It puts the price tag on each option so
the owner can.**

Reproduce: `scripts/dotcom_reconstruction.py`, `scripts/sleeve_weight_scan.py`,
`scripts/vol_target_holdout_eval.py [--target N] [--sleeve N]`.

## The finding, restated precisely

The book holds 40% in QLD, a 2x-QQQ ETF that has only existed since 2006-06. Every
number the 60/40 split was chosen on comes from 2007-2026 — a window that structurally
cannot contain the regime this instrument is worst in: an extended, choppy tech bear
market where the SMA-200 whipsaws and daily-reset leverage compounds the damage.

Rebuilt independently against the real shipped backtester (synthetic pre-2006 QLD, drag
3.19%/yr fit from the real QQQ/QLD overlap; the reconstruction reproduces real QLD's
2006-2026 CAGR to 25.34% vs 25.34% and its maxDD to −82.6% vs −83.1%, so the machinery
is sound):

> In a dotcom-shaped market the deployed book draws down **−52.2%**, breaches its own
> −35% HALT on **2000-07-28**, and — because the HALT is sticky by design — stays flat
> from that day forward. Not "has a bad year". Stops, permanently, at the bottom, until
> a human intervenes.

That is the risk. Everything below is about what it costs to reduce it.

## Every lever, measured on both windows

Holdout = 2021-01-04..2026-08-10, frozen before any of this work (see
`settings.config.HOLDOUT_START`). Reconstruction = 2000-2026 with synthetic pre-2006
QLD. Pinned costs throughout (2 bp slippage, ^IRX cash).

| Option | Holdout CAGR | Holdout Sharpe | Holdout maxDD | Recon maxDD | HALT fires? |
|---|---:|---:|---:|---:|---|
| **Deployed (sleeve 40%, no vol-target)** | **17.90%** | **1.08** | **−21.31%** | **−52.22%** | **YES 2000-07** |
| Vol-target 20% | 17.14% | 1.07 | −21.02% | −41.58% | YES 2002-12 |
| Sleeve 30% | 16.04% | 1.13 | −18.37% | −45.39% | YES 2001-12 |
| Sleeve 30% + vol-target 20% | 15.66% | 1.12 | −18.31% | −38.30% | YES 2003-01 |
| Vol-target 12% | 14.92% | 1.10 | −17.47% | −32.41% | no |
| Sleeve 20% | 14.13% | 1.20 | −15.35% | −37.80% | YES 2003-01 |
| Sleeve 10% | 12.16% | 1.28 | −12.25% | −29.41% | no |
| Sleeve 0% (core only) | 10.15% | 1.36 | −9.07% | −20.18% | no |

## Three things this table says

**1. Everything that closes K1 fails the project's own bar.** Only two rows clear the
HALT: vol-target 12% (−298 bp CAGR vs deployed) and sleeve 10% (−574 bp). The
pre-registered accept/reject rule — reused unchanged across both pre-registrations —
allows a 150 bp CAGR give-up. Both blow through it. The one option that comfortably
passes the rule (vol-target 20%, −76 bp) leaves the reconstructed drawdown at −41.6%
and the HALT still firing.

There is no cheap fix. **The book's 17.9% headline return is, in part, payment for
carrying this tail.**

**2. The sleeve buys absolute return by selling risk-adjusted return.** Holdout Sharpe
is perfectly monotonic against sleeve weight and points the wrong way: 1.36 at 0% → 1.08
at 40%. Same for drawdown, both windows. This is not a new discovery — `config.py`'s
SLEEVES comment already says the blend "buys index-beating absolute return by giving up
0.18 Sharpe and doubling the drawdown", and calls it an explicit owner decision. The
reconstruction just shows the drawdown half of that trade is worse than the sample
suggested: not "doubled" (−11.8% → −24.8%) but, in the regime the sample omits,
roughly quadrupled (−20.2% → −52.2%).

**3. The two levers do different jobs and are not substitutes.** At comparable holdout
CAGR (~14-15%), vol-target 12% and sleeve 20% diverge sharply:

- **Sleeve 20%** wins the holdout — Sharpe 1.20 vs 1.10, maxDD −15.4% vs −17.5%.
- **Vol-target 12%** wins the tail — recon maxDD −32.4% vs −37.8%, and clears the HALT
  where sleeve 20% does not.

That suggested a combination might dominate either alone, since cutting the sleeve is a
static reduction while the vol-target is *reactive*. **That hypothesis was
pre-registered, tested on 2026-08-25, and refuted** — see
`preregistration_2026-08-25_combination.md`. Sleeve 30% + vol-target 20% costs 224 bp
for a −38.30% reconstructed drawdown, while a pure vol-target at the same price
interpolates to roughly −35.5%. Adding a static sleeve cut on top of a vol-target
spends return less efficiently than simply tightening the vol-target.

The corrected reading: **the vol-target is the efficient lever and the sleeve weight is
not.** Every sleeve-only configuration in the table above is dominated by some
vol-target configuration that costs less and protects better. Only one combination was
tested and no more will be — `config.py` forbids re-optimising this split by grid
search, and trying pairs until one passes is that search conducted one commit at a
time.

## The options, with the case against each

**A. Do nothing.** Legitimate — but only as an explicit, recorded decision that a
dotcom-shaped bear market takes the book to ~−52% and then halts it permanently at the
worst possible moment. It is not legitimate as a default from inaction. K1 stays open,
knowingly.

**B. Vol-target 20% (the one candidate that passed).** Cheap (−76 bp), improves the
tail meaningfully (−52.2% → −41.6%), needs no change to the sleeve. But it does **not**
close K1 — the HALT still fires, just 2.4 years later. Best framed as harm reduction,
not a fix.

**C. Cut the sleeve to 10-20%.** Attacks the problem at its source and improves every
risk metric on both windows. Costs 376-574 bp of holdout CAGR. At 20% the HALT still
fires in the reconstruction; only at 10% does it clear.

**D. Vol-target 12%.** Clears the HALT, keeps the sleeve, costs 298 bp. Rejected by the
pre-registered rule, but note the rejection was one-sided: Sharpe *improved* (+0.02) and
drawdown improved (−3.8 pp). The rule correctly rejects it as a drop-in swap while being
unable to answer whether the more conservative book is the better book — a risk-appetite
question, not a backtest question.

**E. Raise `cb_max_drawdown_halt` so the current book has headroom.** **Treat this one
with suspicion.** The threshold's own stated design rule is "~10 pp headroom over
everything observed". Applied consistently to the reconstruction's −52.2%, that implies
a threshold near 60% — which is not a protection mechanism, it is its removal. This
option closes the finding by widening the tolerance rather than reducing the risk. If
chosen, it should be chosen with that stated plainly.

**F. Combination — tested 2026-08-25, and it does not help.** See
`preregistration_2026-08-25_combination.md`. Sleeve 30% + vol-target 20% costs 224 bp
and reaches a −38.30% reconstructed drawdown, still short of clearing the HALT. More
importantly it is *less efficient* than pure vol-targeting: a vol-target alone at the
same 224 bp cost would land near −35.5%. The mechanism claim in point 3 above is
therefore refuted — the two levers behave as substitutes, and the reactive one is
strictly the better buy. Sleeve-only configurations are dominated outright.

## What is NOT recommended, and why

Picking whichever row of the table looks best. Every number in the holdout column comes
from a single 5.6-year window that happened to be a strong bull market with two short
sharp drawdowns — the regime that flatters high sleeve weights and penalises any
volatility cap. Every number in the reconstruction column comes from a model, not from
prices that traded. Neither column alone is a decision, and the table is small enough to
overfit by eye.

The honest summary is the one in point 1: **this is a risk-appetite decision that the
data can price but cannot make.** The measurements narrow it to a real trade-off between
roughly 300-600 bp of expected annual return and a tail that, if the reconstruction is
even approximately right, ends the strategy's life the one time it matters most.

## Status of K1

**Open.** Partially mitigated if option B is adopted; closed only under C (at 10%), D,
or a validated F. No configuration change has been made — `book_vol_target` remains
`0.0` (off) and `SLEEVES` remains 0.60/0.40 as deployed.
