# ADR-0002 — A threshold is derived from an error budget, and carries its own uncertainty

**Status:** accepted · **Date:** 2026-08-09

## Context

Claim 1 says no field is published below its confidence threshold, and that the threshold is
derived rather than chosen. The idea is the strongest one in the project and it has three ways
of collapsing back into the magic number it replaced. Each has to be closed here, before any
extraction code exists, because closing them afterwards means rewriting the extraction code.

**The score is not a probability.** An OCR or model confidence is a number the engine emits.
Whether 0.85 means "wrong 15% of the time" is an empirical question, and for every engine
considered here the answer is no. A threshold placed on an uncalibrated score is superstition
with a decimal point.

**A point estimate from a small sample is not a measurement.** If 40 labelled examples of a
field score above 0.9 and one is wrong, the observed error rate is 2.5%. The 95% upper bound on
that rate is about 13%. Publishing against a 3% budget on the strength of the 2.5% is not
engineering.

**A check that goes red for the wrong reason gets deleted.** "CI recomputes the threshold and
fails when it moves" has no tolerance in it. The first extra document moves a threshold, the
build goes red, somebody removes the check, and the claim is gone — not overruled, just quietly
absent.

And one framing that must survive to the README: **every figure here is a statement about a
distribution this repository authored.** The corpus is generated. Its difficulty is a parameter
somebody chose. A threshold derived against it is a threshold for that corpus.

## Decision

**1 · The threshold is the lowest score whose *upper confidence bound* on the error rate fits
the budget.** For a candidate threshold `t`, take the labelled fields scoring at or above `t`;
count `n` published and `k` wrong; compute the one-sided upper bound on the true error rate at
95% by the **Clopper–Pearson** method — exact, conservative, and correct at small `n` and at
`k = 0`, which is where the normal approximation is worst and where these decisions are made.
The derived threshold is the lowest `t` whose bound is at or below the field's declared budget.

Conservative is the correct failure direction. Being wrong here publishes wrong values.

**2 · Every reported figure carries its `n`.** A threshold without its sample size is unreadable:
0.87 at n=4,000 and 0.87 at n=40 are different claims. The scoreboard prints both.

**3 · If no threshold fits the budget, the field is `always-review`.** Not a high number. When
even `t = max` leaves an upper bound above the budget, the honest output is that the available
evidence does not support publishing this field automatically at all, and it is declared as
such. A 0.999 written because 0.999 sounds safe is doctrine rule 3's default wearing a decimal
point — and it is worse than the default, because it looks derived.

This is the rule with a real cost: `always-review` fields consume 100% of their volume from the
review queue, and ADR-0001's capacity gate will fail on them. That failure is the system working.

**4 · Calibration is measured and reported, not assumed.** A reliability curve per field class,
with **the count in every bin printed**, and expected calibration error computed over bins with
a declared minimum occupancy. ECE over 10 bins on 60 samples is noise with a name; where the
data does not support the metric, the harness says so instead of printing a number.

Calibration does not gate publication — the threshold does. It is reported because a badly
calibrated score with a conservative threshold is safe and *wasteful*, and the reliability curve
is the only thing that shows the waste.

**5 · CI recomputes from the committed recording, against a declared per-field tolerance.**
Thresholds are derived from `recordings/ocr/` (ADR-0005), never from a live engine run, so the
only thing that can move a threshold is a change in this repository. `contracts/documents/`
declares a tolerance per field; movement inside it passes, movement outside it fails and names
the field, the old value, the new value and both `n`s.

The tolerance is data and changing it is a reviewed change. It is not a licence to drift: a
threshold that moves within tolerance on every commit is itself a finding, and the harness
reports consecutive same-direction movement.

**6 · Nothing here is claimed about production.** The README states, on the face of the claim,
that the thresholds are derived against a synthetic distribution with exact ground truth. The
public dataset (Phase 1) is the out-of-distribution check, and where it carries no field-level
labels the only measurement available is the ISO 6346 check digit — a **falsifier**, giving a
*lower bound* on the error rate and never a confirmation (`docs/SCENARIO.md`).

**7 · The corpus must sit in a declared operating range or none of this means anything.** A
generator degraded too gently puts every confidence at the top of the range: the reliability
curve is flat, ECE is measuring nothing, and every threshold is trivially satisfiable. Too
harsh and everything abstains. Both report green. `corpus/envelope.yaml` declares the intended
confidence distribution and the acceptable abstention band per document type, and a test goes
red when the generator leaves it (`docs/DECISIONS.md` 20).

A relevant constraint from `docs/AWS-CONSTRAINTS.md`: both managed services document a
**15-pixel minimum character height** and full support for in-plane rotation. So a page that is
merely skewed should *not* produce abstentions, and a 6 pt footnote at 150 DPI legitimately
should. The envelope is set against those facts rather than against what makes the numbers look
good.

## Consequences

- The labelled set has to be big enough per field class, which is a requirement on the corpus
  generator, decided now rather than discovered when a threshold refuses to exist.
- Derivation runs over a monotone sweep of candidate thresholds. It is arithmetic over counts —
  pure, in `core/`, no dependency, fast enough to run on every commit.
- Clopper–Pearson needs the beta quantile function. `statistics` in the standard library does not
  provide it, and `core/` may not import SciPy. It is implemented in `core/` by bisection on the
  regularised incomplete beta function, with the accuracy asserted against published values in a
  test. Fifty lines and no dependency beats a dependency the core is not allowed to have.
- A field's error budget becomes the most consequential number in its contract, and a contract
  with no budget must fail to load — with a test asserting it (Phase 1).
- `always-review` is a first-class outcome everywhere: the contract loader, the queue projection,
  the scoreboard and the cost model each handle it explicitly rather than treating it as a
  threshold of 1.0.

## Sources consulted, 2026-08-09

- Clopper, C. J. and Pearson, E. S. (1934), *The use of confidence or fiducial limits illustrated
  in the case of the binomial*, Biometrika 26(4), 404–413 — the exact interval, and the reason it
  is the right one at `k = 0`.
- `docs/AWS-CONSTRAINTS.md` — the 15-pixel character floor and in-plane rotation support, which
  set the corpus envelope's endpoints.
