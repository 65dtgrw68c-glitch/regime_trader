# Pre-registration — book-level vol target vs. the deployed 60/40 book

Written 2026-08-24, before running `scripts/vol_target_holdout_eval.py` or looking at
any holdout-window result. This file states the candidate, the data split and the
accept/reject rule; nothing below may be changed after the evaluation script has run.

## Why

`analysis_report_2026-08-24_audit.md` Section D found that the SMA-200 trend filter
this book runs on separates dates by forward realised VOLATILITY (11.4% above the
average, 28.2% below), not by forward return — the mechanism the whole strategy leans
on is volatility timing, not return prediction. Section F measured a book-level
vol-target as the one candidate change that improved Sharpe and maxDD together across
a 12–20% target range and, critically, was the only tested change that kept the book's
drawdown under its own −35% HALT threshold in a reconstructed 2000-2026 (dotcom-inclusive)
history. Both of those measurements were made on data already used to pick the book's
existing structure (core_scale, sleeve weight, HALT threshold) — this pre-registration
exists so the SAME question gets answered once more, on data that structure was never
tuned against.

## Candidate

Scale every target weight `compose_book()` produces by

    scale = min(1, target_vol / realised_vol_21d)

where `realised_vol_21d` is the annualised standard deviation of the BOOK's own
trailing 21 daily returns — the returns this same backtest run already produced up to
the decision bar, so the estimate is causal by construction (implemented as
`core.portfolio_backtester.PortfolioBacktester.book_vol_target` /
`_vol_target_scale`, no-op at the default `book_vol_target=0.0`).

**target_vol = 0.20** (20% annualised). Chosen, not swept: Section F measured 12/15/20%
in-sample with near-identical Sharpe (0.88 across all three — a flat surface, the
signature of a real effect rather than a tuned one), and 20% is specifically the value
validated against the reconstructed dotcom drawdown in finding K1 (book maxDD −52.1% →
−34.7%, HALT no longer fires). Testing multiple targets against the holdout and keeping
the best would be exactly the in-sample selection this exercise is meant to avoid.

`vol_target_lookback = 21` (trading days) — unchanged from Section F, not tuned here.

## Baseline

The same book, run through the same `PortfolioBacktester`, same span, same costs, same
cash model, with `book_vol_target=0.0` (today's deployed configuration).

## Data and span

- Yahoo adjusted daily bars, `data_cache/yahoo/` (SPY, QQQ, GLD, IEF, QLD, TBILL) — the
  same source `scripts/reproduce_headline.py` and `scripts/sleeve_check.py` use.
- Both variants run over the FULL available history from the natural common-index
  start (no `start_date` clip — Phase 0 / finding H3 showed clipping shifts the
  effective simulation start past the requested date because of the 200-bar SMA
  warmup). Clipping would also cut off the pre-holdout history the vol-target's own
  21-day lookback needs to be initialised by the time the holdout window starts.
- Metrics below are computed only on the slice `index >= settings.config.HOLDOUT_START`
  (2021-01-01) of each run's returns — the frozen holdout. Everything before that date
  is used purely to warm up the SMA-200 and the 21-day vol lookback; no metric from
  before 2021-01-01 feeds into the decision.
- 2 bp slippage, 0 bp commission (`settings.config.BACKTEST`), ^IRX cash series — same
  pinned costs `reproduce_headline.py` uses.

## Accept / reject rule

Computed on the holdout slice only, `CAGR`/`Sharpe`/`maxDD` defined identically to
`scripts/reproduce_headline.py` (annualised from daily returns, `maxDD` as
peak-to-trough on the compounded equity curve, both variants' `maxDD` compared as
**magnitudes** — "not worse" means the candidate's drawdown is no larger in absolute
size than baseline's, independent of the minus sign both carry).

**REJECT** if any of:
- candidate `maxDD` magnitude is worse (larger) than baseline's, or
- candidate `Sharpe` is more than 0.05 below baseline's, or
- candidate `CAGR` is more than 150 bp below baseline's.

**ACCEPT** otherwise (i.e. `Sharpe ≥ baseline − 0.05` AND `maxDD` magnitude ≤
baseline's, with the CAGR check as an additional guard against a technically-passing
candidate that gave up too much return).

An ACCEPT is a green light to consider offering `book_vol_target` as a real,
still-default-off toggle in the deployed config and to revisit the HALT threshold
against the reconstructed history (finding K1) — not an automatic deployment. Sleeve
weight and any cash-yield changes remain separate owner decisions, out of scope here.

A REJECT means the candidate is closed, the same way the 2026-08-01 report closed the
vol-gate hypothesis: written up, not silently dropped or re-tried with a different
target.

---

## Result — 2026-08-24, `scripts/vol_target_holdout_eval.py`

| | CAGR | Vol | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| Baseline (`book_vol_target=0.0`) | 17.90% | 16.5% | 1.08 | −21.31% |
| Candidate (`book_vol_target=0.20`) | 17.14% | 16.0% | 1.07 | −21.02% |
| Delta | −0.76pp | | −0.01 | −0.29pp (magnitude, better) |

Holdout: 2021-01-04 .. 2026-08-10 (1406 bars), pinned costs (2 bp slippage, 0 bp
commission), ^IRX cash.

**VERDICT: ACCEPT** — Sharpe give-up (0.01) is within the 0.05 limit, maxDD magnitude
is not worse (21.02% ≤ 21.31%), CAGR give-up (76 bp) is within the 150 bp limit. All
three pre-registered conditions pass.

**Read this result for what it actually says, not for what the in-sample numbers in
`analysis_report_2026-08-24_audit.md` Section F implied it would say.** On the true
holdout the effect is small in every direction — Sharpe, maxDD and CAGR all move by
less than one point of precision that matters. 2021-2026 never produced the choppy,
elevated-realised-vol regime (a slow grind, not a fast V) that the vol-target is
designed to catch; 2022 was a moderate-vol decline, COVID and the 2025 tariff shock
were both short and sharp rather than sustained. The holdout simply didn't contain the
scenario this candidate exists for.

That means this ACCEPT is a "does no meaningful harm" result, not a "materially
improves the deployed book" result. The actual case for `book_vol_target=0.20` remains
what it was in Section F/K1: the reconstructed 2000-2002 dotcom scenario, where the
sleeve's leverage compounds an extended high-vol decline the SMA-200 alone doesn't
exit fast enough from. That reconstruction has NOT been re-run against this
implementation (`core.portfolio_backtester.PortfolioBacktester.book_vol_target`,
committed alongside this file) — the original audit's dotcom numbers were produced by
a separate scratchpad script this repo does not have. Re-validating that scenario, and
then resetting `cb_max_drawdown_halt` against it, are the two remaining steps this
pre-registration explicitly deferred to "if accepted" and did not do here.

**Not done as part of this evaluation, deliberately out of scope per the rule above:**
turning `book_vol_target` on in the deployed live config; re-running the dotcom
reconstruction against this implementation; resetting the HALT threshold. The dotcom
re-run is done separately below.

---

## Dotcom re-validation — 2026-08-24, `scripts/dotcom_reconstruction.py`

This is not part of the accept/reject rule above (that rule only covers the frozen
holdout) — it is a separate check of the audit's finding K1, which cited a
reconstructed dotcom scenario, not the holdout, as the actual justification for this
candidate. The audit's own dotcom numbers came from a scratchpad script this repo does
not have, so they were re-derived independently, against the real shipped
`core.portfolio_backtester.PortfolioBacktester` (same `compute_daily_targets` and
`_vol_target_scale` as everywhere else in this session), not copied.

**Calibration check (confirms the reconstruction, not the candidate):** a synthetic
QLD built as `2×QQQ − 3.19%/yr drag` (fit from the real QQQ/QLD overlap, independent of
the audit's quoted 3.27%/yr) reproduces real QLD's 2006-2026 CAGR almost exactly
(25.34% vs 25.34%) and maxDD closely (−82.60% vs −83.13%). The baseline (no vol-target)
book maxDD in the reconstruction is −52.22%, matching the audit's quoted −52.1%, and
the HALT does fire (2000-07-28) — both consistent with the original finding.

**The candidate's claim does not hold up.** The audit stated `book_vol_target=0.20`
keeps the reconstructed book's drawdown at −34.7% and the HALT does not fire. Re-run
here: maxDD −41.58%, **HALT still fires** (2002-12-04, ~2.4 years later than baseline,
not prevented). A full sweep for context (informational only — these were not
pre-registered or tested against the holdout, and picking one after seeing this table
would be exactly the in-sample selection this whole exercise exists to avoid):

| target_vol | CAGR | Sharpe | maxDD | HALT fires? |
|---:|---:|---:|---:|---|
| 0% (baseline) | 9.56% | 0.61 | −52.22% | YES 2000-07-28 |
| 10% | 8.22% | 0.70 | −29.10% | no |
| 12% | 8.65% | 0.69 | −32.41% | no |
| 15% | 9.11% | 0.68 | −36.77% | YES 2003-01-17 |
| **20% (accepted candidate)** | **9.49%** | **0.66** | **−41.58%** | **YES 2002-12-04** |
| 25% | 9.77% | 0.65 | −45.04% | YES 2001-12-31 |
| 30% | 9.85% | 0.65 | −47.22% | YES 2001-12-20 |

Only ~10-12% targets keep the reconstructed book under the existing 35% HALT threshold
— roughly half the 20% level this candidate was pre-registered and holdout-tested at.

**Net assessment.** `book_vol_target=0.20` is not nothing: it cuts the dotcom-scenario
maxDD by ~11 points (−52% → −42%), the calendar-2000 loss from −17.9% to −13.2%, and
delays the HALT by over two years. But it does not deliver the specific "HALT no
longer fires" result the original K1 finding used to justify itself, at least not in
this independent reconstruction. Two things follow: (1) K1 remains only partially
addressed by the accepted candidate — the sleeve's dotcom-scenario tail risk is smaller
with vol-targeting on, not solved; (2) a tighter target (~10-12%) is a DIFFERENT,
untested candidate — it has not been pre-registered or run against the 2021-2026
holdout, so it must not be treated as validated just because it happens to clear the
HALT in this one reconstruction. Resetting `cb_max_drawdown_halt` on the strength of
the 20% candidate alone is not supported by this data.
