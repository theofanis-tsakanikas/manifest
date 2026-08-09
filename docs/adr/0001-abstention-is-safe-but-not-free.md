# ADR-0001 — Abstention is the safe state, and abstention is not free

**Status:** accepted · **Date:** 2026-08-09 · **Documentation verified:** 2026-08-09

## Context

Attestor's first doctrine rule is *the safe state is no output*. Watermark had to revise it,
because on a grid silence is not safe — the substation still overloads. Here it needs revising
again, in a third direction.

Refusing to publish a field **is** safe. The field goes to a human, the human decides, nothing
wrong is published. The rule holds. What it omits is that the human is a real, countable,
exhaustible resource, and every abstention spends some of them.

The scenario's declared parameters (`docs/SCENARIO.md` — these are *declared*, not measured;
nothing in this repository has observed a reviewer):

| | |
|---|---|
| Documents per day | 18,000 |
| Pages per day | 55,000 |
| Reviewers | two per office, two offices — **4** |
| Peak | Monday mornings and quarter-end, ~3× the mean |

Take ~15 extracted fields per document and 20 seconds for a reviewer to open a crop, read it,
and decide. Four reviewers at six productive hours is 86,400 reviewer-seconds, which is
**4,320 decisions a day** against **270,000 extracted fields a day**.

**The review queue can absorb about 1.6% of extracted fields. On a peak day, about 0.5%.**

That number is the whole of this ADR. A confidence threshold that routes 5% of fields to review
is not a cautious system; it is a system that has silently decided its reviewers will work
three times their available hours. What actually happens then is not a backlog — backlogs are
visible. What happens is that reviewers start approving without looking, the agreement rate
with the model goes to 100%, and the control that every diagram shows as the safety net becomes
a rubber stamp with a login. The system then looks *safer* than one with no review at all,
while being less safe, because a recorded human decision is treated as evidence.

**Nobody builds this.** Human-in-the-loop is specified in every responsible-AI document and
implemented as a queue with no capacity model, no integrity measurement and no failure mode.
That is why claim 5 is worth more than it looks.

`docs/AWS-CONSTRAINTS.md`: Amazon A2I closed to new customers on 2026-07-30, so the queue is
ours to build. That is the better outcome — a managed worker pool gives a task UI, and every
property in this ADR is a property of the queue's *design*.

## Decision

**1 · Review capacity is declared data, in `contracts/review/capacity.yaml`.** Reviewer count,
productive hours, seconds per decision by field class, peak multiplier. It is an input to a
model, and each figure is labelled as a declared scenario parameter rather than a measurement.

**2 · Threshold derivation is capacity-constrained, and exceeding capacity fails the build.**
ADR-0002 derives a threshold per field from its error budget. Those thresholds imply a
projected review volume over the corpus. If the projection exceeds declared capacity — at the
**peak** multiplier, not the mean — CI fails, naming the fields that consume the most queue.

The failure is loud on purpose. A threshold that cannot be staffed is a design defect, and the
only thing worse than surfacing it is the alternative, which is that it is discovered by the
reviewers.

**3 · The permitted responses to a capacity failure are enumerated, and all of them are
declared changes.** When the build fails, exactly four things may be done:

| Response | What it costs, honestly |
|---|---|
| Publish fewer fields | The contract loses a field. Data minimisation makes this the *first* option, not the last |
| Improve the engine or escalate more | Money and latency, both modelled |
| Add reviewers | A change to the declared capacity, reviewed like any other |
| Raise the field's error budget | More wrong published values, accepted deliberately, by a named human, with an expiry |

What may **not** be done is raise the confidence threshold to reduce queue volume without
changing the error budget. That inverts the entire derivation: the threshold is an output of
the budget, and editing the output to fit the staffing is how a derived number becomes a chosen
one wearing a derivation's clothes. No model, no pipeline and no service principal may make any
of these four changes (doctrine rule 5).

**4 · A human decision counts as evidence only if the human was plausibly looking.** Measured,
per reviewer, over a window:

- **Time on task** — a decision faster than a declared floor is recorded as unexamined
- **Agreement rate with the model** — 100% is not a control; it is a reviewer who has stopped
  reading. Both tails are reported: a reviewer who agrees with everything and one who
  disagrees with everything are the same finding
- **Sampled re-review** — a fraction of decided items is re-queued to a different reviewer, and
  disagreement between two humans is the only measurement here that is about the *task* rather
  than about the reviewer

The report **names the pattern**. "Reviewer 3 approved 84% of items in under four seconds" is a
finding; "average review time 11.2s" is a number that hides it. A metric whose failure mode is
being averaged into invisibility is not a control.

**5 · The queue does not have a key for the door that has none.** Doctrine rule 7: a field with
no provenance cannot be overridden into existence. Such a field may still be *queued* — a human
can look at the page — but what the human returns is a value **they** located, which is a new
value with its own provenance, not an approval of the system's. The distinction is recorded,
because "approved" and "supplied by a human who found it themselves" are different facts about
where a published number came from.

## Consequences

- The capacity model must exist before the first threshold is derived. Phase 4 builds the queue;
  Phase 2 derives thresholds. The **contract** therefore lands in Phase 1 with the others, and
  Phase 2's calibration harness reads it and can already fail on it.
- A field declared `always-review` (ADR-0002, too little evidence to derive a threshold) consumes
  100% of its volume from the queue. This is the interaction that will actually break the budget,
  and it must be visible as a line item rather than folded into a total.
- The scoreboard needs both halves. "Claim 5: the decision cannot be bypassed" is the easy half.
  "Projected queue load at peak: N% of declared capacity" is the half nobody prints.
- These parameters are declared, so the number they produce is a **model**. Same rule as the cost
  figure: labelled as modelled everywhere it appears, never as an observation of real reviewers,
  because there are none.
- Reviewer-integrity metrics are personal data about identified employees. Purpose, retention and
  lawful basis are declared in the contract alongside them; a control that measures people and
  does not say why is a different problem arriving behind a good intention.

## Sources consulted, 2026-08-09

- `docs/SCENARIO.md` — volumes, reviewer count, peak multiplier (declared scenario parameters).
- [AWS service availability updates](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-service-availability/)
  — Amazon A2I among the SageMaker AI features closing to new customers on 2026-07-30.
