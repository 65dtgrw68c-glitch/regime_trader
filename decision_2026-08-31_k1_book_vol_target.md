# Decision — adopt `book_vol_target = 0.12`, closing K1

2026-08-31. Owner decision on finding K1 of the 2026-08-24 audit, taken against
`analysis_report_2026-08-25_k1_options.md` plus one measurement that memo did not
contain. **This overrides a pre-registered rejection.** That is stated up front because
it is the part a reader should scrutinise hardest.

Reproduce: `scripts/k1_return_ledger.py`.

## The question that was asked

Which available lever maximises return?

The K1 memo cannot answer it, and says so — it prices every option in *holdout* CAGR,
where the honest answer is "none of them, all six cost between 76 and 775 bp". On that
column alone the return-maximising action is to do nothing.

That column is incomplete, and in a specific, correctable way.

## What was missing

The memo's two columns measure different things and neither is a return over the tail:

- **Holdout CAGR** is realised return over 5.6 years containing no dotcom-shaped regime.
- **Reconstruction maxDD** is a *drawdown*, and it is computed on the **un-halted**
  return stream — the path the book would take if the −35% circuit breaker were not
  there.

But the breaker is there, and `RiskManager` never re-arms it. In the reconstruction the
deployed book does not "draw down 52% and recover". It goes flat on 2000-07-28 and stays
flat for twenty-six years. Comparing options on a path the deployed configuration would
never have been allowed to travel is not a return comparison.

`scripts/k1_return_ledger.py` adds the missing column: post-HALT realised CAGR and
terminal wealth, 2000-2026, for **exactly the eight configurations the memo already
measured**. No new candidates were introduced — that restriction is the point, and it is
what makes the conclusion admissible rather than a search.

## The ledger

| Option | Holdout CAGR | Holdout Sharpe | Recon CAGR (HALT applied) | × capital, 26y | HALT |
|---|---:|---:|---:|---:|---|
| Deployed (sleeve 40%, no VT) | 17.90% | 1.08 | −0.75% | **0.82×** | 2000-07-28 |
| Vol-target 20% | 17.14% | 1.07 | −1.22% | **0.72×** | 2002-12-04 |
| Sleeve 30% | 16.04% | 1.13 | −0.88% | **0.79×** | 2001-12-20 |
| Sleeve 30% + VT 20% | 15.66% | 1.12 | −1.24% | **0.72×** | 2003-01-17 |
| **Vol-target 12%** | **14.92%** | **1.10** | **8.65%** | **9.04×** | **never** |
| Sleeve 20% | 14.13% | 1.20 | −1.05% | **0.76×** | 2003-01-17 |
| Sleeve 10% | 12.16% | 1.28 | 7.31% | **6.51×** | never |
| Sleeve 0% (core only) | 10.15% | 1.36 | 6.40% | **5.19×** | never |

## What it says

**1. The ranking inverts.** Ordered by holdout CAGR, vol-target 12% is second-worst of
the surviving options. Ordered by what the book is actually worth after the regime the
sample cannot contain, it is first — by 11× over the deployed configuration.

**2. Partial mitigation of a sticky breaker is worse than none.** Vol-target 20%, sleeve
30%, sleeve 20% and the combination all soften the drawdown without preventing the
breach. The HALT therefore fires *later and at a lower equity level*, and every one of
them ends up below the deployed book: 0.72×–0.79× against 0.82×. Option B — the one
candidate that passed the pre-registered rule, adopted as "harm reduction" — is the
single worst row in the table. **A breaker you still trip is not a breaker you have
partly avoided.**

**3. Among the three survivors, 0.12 is the best on both windows at once.** It keeps the
most holdout return (14.92% vs 12.16% and 10.15%) *and* the most reconstructed terminal
wealth (9.04× vs 6.51× and 5.19×). It does not require choosing between the two columns.

## The override, stated plainly

`book_vol_target = 0.12` was **REJECTED** on 2026-08-25
(`preregistration_2026-08-25_tighter_vol_target.md`): 298 bp of holdout CAGR give-up
against a 150 bp limit. That verdict is correct on its own terms and **has not been
retuned**. The rule stands exactly as written; the limit was not moved.

It is overridden because the rule scores candidates on 2021-2026 CAGR, and that window
contains no event in which this book's HALT fires. The rule is therefore structurally
blind to the term that dominates long-run return. It was built to stop a parameter being
fitted to the holdout — a job it did, twice — not to price a tail it cannot observe.

Two things keep this from being selection dressed up as a decision:

- **0.12 was specified and rejected BEFORE the ledger existed.** It was not chosen from
  the ledger; it was already on the table, with its price already paid and recorded.
- **The ledger introduced no new candidates.** Every row is a configuration already
  measured and published in the K1 memo. Only the metric is new.

What would have made this illegitimate: scanning vol-target levels until one produced a
good terminal multiple. That was not done and must not be — same rule as `SLEEVES`.

## Known fragility

**0.12 clears the −35% HALT by 2.6 pp** (reconstructed maxDD −32.41%). That margin rests
on a model: QLD before 2006-06 is synthetic (2× QQQ less 3.19%/yr drag, fit from the real
overlap). If the real tail were ~3 pp deeper, this configuration halts too and lands
with the 0.72–0.82× rows. **This is a materially better bet, not a guarantee.**

Three further limits worth naming:

- The 9.04× vs 0.82× gap assumes the HALT is never cleared. `scripts/clear_halt.py`
  exists and an operator would presumably use it. The comparison prices the *designed*
  behaviour, which is the right thing to design against, but a real operator would not
  sit flat for 26 years.
- The reconstruction's core book holds no bonds before 2002-07 and no gold before
  2004-11, because IEF and GLD did not exist. The dotcom-era core is thinner than
  today's book, which if anything overstates the drawdown.
- The holdout give-up is real and will be paid in normal markets. 298 bp/yr is the
  standing cost of this decision, visible immediately; the benefit is contingent and may
  never arrive.

## What changed in the code

| File | Change |
|---|---|
| `settings/config.py` | New `BOOK_VOL_TARGET = {"target": 0.12, "lookback": 21}`, with the reasoning above inline. |
| `core/sleeves.py` | New `vol_target_scale()` — the single shared implementation, next to `compose_book()` for the same reason. |
| `core/portfolio_backtester.py` | `_vol_target_scale()` now delegates to `core.sleeves`. Constructor default changed from a hardcoded `0.0` to *the deployed config value*. |
| `core/risk_manager.py` | Tracks trailing daily book returns, persisted across the daily `--once` restarts, retained long enough for a 21-day window, and **not** wiped by `clear_lock()`. Guards against cash transfers entering the window. |
| `main.py` | `_compute_live_target_book()` applies the scale after `compose_book()` and before `validate_book()` — the same place the backtester applies it. |

Two design points are load-bearing:

**The scale lives in one function.** A backtest-only copy of this maths is how the live
book silently diverged from every validated report in 2026-08 (Befund 0). Live and
backtest now call `core.sleeves.vol_target_scale`, and
`TestBookVolTarget::test_live_and_backtest_produce_the_same_scale` fails if that stops
being true.

**The default now points at the deployed book.** Previously `book_vol_target=0.0` was a
hardcoded default that happened to match production. It no longer would, so it now reads
config: an evaluation that says nothing evaluates the book that actually trades. Scripts
that deliberately measure something else (`sleeve_check.py`, `sleeve_weight_scan.py`, the
execution-accounting golden test) pin `0.0` explicitly, with a comment saying why.

**The realised-vol window survives a halt clear.** `clear_lock()` wipes the weekly
breaker's baseline by design. It must not wipe the vol window: resuming after a crash
with an empty window would run the book at full exposure for a whole lookback period,
starting the day after the crash that caused the halt.

## Status of K1

**Closed**, with the fragility above recorded rather than resolved. The reconstruction no
longer breaches the HALT; the book survives the regime its sample could not contain.

Not closed by this: the reconstruction is still a model. If real pre-2006 2×-QQQ data
ever becomes available, re-running `scripts/k1_return_ledger.py` against it is the test
that could overturn this decision.
