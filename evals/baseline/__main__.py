"""What the discipline buys, against the system nobody builds on purpose.

Every other harness here proves that a control **refuses** correctly. None of them answers the
question a person paying for this would ask first: *compared to what?*

So: the same reader, the same corpus, the same extraction — and three publication policies.

- **naive** — publishes every value the reader found. No threshold, no abstention, no gate.
- **chosen** — publishes above a single hand-picked 0.85: what a threshold looks like when it
  comes from a meeting instead of from a budget.
- **derived** — this system. A per-field threshold from that field's declared error budget, and
  always-review where none fits.

Three numbers per policy, and the third is the one that matters:

- **published** — how many values reached a record
- **published-and-wrong** — how many of those were not what the page said
- **queued** — how many went to a human instead

**The comparison is deliberately unflattering to this system on one axis.** The derived policy
publishes *less* and queues *more*, and the queue is a real cost paid by real people. Showing the
wrong-rate without the queue volume beside it would be the same selective reporting the cost
model refuses. Both are printed, per policy, always.

**What this is a statement about.** A corpus this repository generated, read by one reader at one
version. It is not a claim about production, about other document sets, or about other readers,
and the ratio between policies is more transportable than any of the absolute figures. Said here
because a "3× fewer errors" line lifted out of this output and put on a CV would be exactly the
measured-sounding number this project exists to argue against.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.harness import by_field, field_contract, score_all
from manifest.core.calibration import Outcome, derive

#: The number somebody writes when nobody derives one. Not a straw man — it is what a threshold
#: looks like when it comes from a meeting instead of from a budget.
CHOSEN = 0.85


@dataclass(frozen=True, slots=True)
class Result:
    policy: str
    published: int
    wrong: int
    queued: int
    #: Fields whose contract declared a budget this policy did not meet. The derived policy has
    #: none by construction; the other two are measured against the same budgets, because a
    #: comparison against no budget at all would let the naive policy win by not being judged.
    budgets_missed: tuple[str, ...]

    @property
    def wrong_rate(self) -> float:
        return self.wrong / self.published if self.published else 0.0

    def line(self) -> str:
        return (
            f"    {self.policy:9} published {self.published:6,}   "
            f"published-and-wrong {self.wrong:5,} ({self.wrong_rate:6.2%})   "
            f"queued {self.queued:6,}"
        )


def _evaluate(policy: str, scored: dict, threshold_for) -> Result:
    published = wrong = queued = 0
    missed: list[str] = []

    for field, entries in sorted(scored.items()):
        threshold = threshold_for(field)
        field_published = field_wrong = 0

        for entry in entries:
            if entry.outcome is Outcome.MISSING:
                # Never found. Not published and not queued for a *confidence* reason — it is a
                # fact about the page, and counting it either way would flatter one policy.
                continue
            confidence = entry.extracted.confidence
            if threshold is None or confidence is None or confidence < threshold:
                queued += 1
                continue
            published += 1
            field_published += 1
            if entry.outcome is Outcome.WRONG:
                wrong += 1
                field_wrong += 1

        budget = field_contract(field).error_budget
        if budget is not None and field_published:
            observed = field_wrong / field_published
            if observed > float(budget):
                missed.append(f"{field} ({observed:.2%} > {float(budget):.2%})")

    return Result(policy, published, wrong, queued, tuple(sorted(missed)))


def main() -> int:
    scored = by_field(score_all())

    derived: dict[str, float | None] = {}
    for field, entries in sorted(scored.items()):
        budget = field_contract(field).error_budget
        if budget is None:
            derived[field] = None
            continue
        derived[field] = derive(field, [entry.observation for entry in entries], budget).value

    results = [
        _evaluate("naive", scored, lambda _field: 0.0),
        _evaluate("chosen", scored, lambda _field: CHOSEN),
        _evaluate("derived", scored, derived.get),
    ]

    print("what the discipline buys — three publication policies, one reader, one corpus\n")
    print("    naive    publish everything the reader found")
    print(f"    chosen   publish above a hand-picked {CHOSEN}")
    print("    derived  per-field, from the field's declared error budget\n")
    for result in results:
        print(result.line())

    naive, chosen, final = results
    print()
    if final.wrong and naive.wrong:
        print(
            f"    Against naive: {naive.wrong / final.wrong:.1f}x fewer published-and-wrong "
            f"values, at the cost of {final.queued - naive.queued:,} more items in the queue."
        )
    elif naive.wrong:
        print(
            f"    Against naive: {naive.wrong:,} published-and-wrong values become none, at the "
            f"cost of {final.queued - naive.queued:,} more items in the queue."
        )
    print(
        f"    Against a chosen {CHOSEN}: {chosen.wrong:,} published-and-wrong becomes "
        f"{final.wrong:,}, and {chosen.queued:,} queued becomes {final.queued:,}."
    )

    print("\n  budgets missed — fields published above the error rate their contract declares:\n")
    problems: list[str] = []
    for result in results:
        if result.budgets_missed:
            shown = ", ".join(result.budgets_missed[:4])
            more = (
                f" (+{len(result.budgets_missed) - 4} more)"
                if len(result.budgets_missed) > 4
                else ""
            )
            print(f"    {result.policy:9} {len(result.budgets_missed):2} field(s): {shown}{more}")
        else:
            print(f"    {result.policy:9}  none")

    if results[2].budgets_missed:
        problems.append(
            f"the derived policy missed {len(results[2].budgets_missed)} budget(s): "
            f"{', '.join(results[2].budgets_missed)}. The derivation exists to make this "
            f"impossible — a field publishing above its declared error rate under its own "
            f"derived threshold means the threshold was derived from evidence that does not "
            f"describe what was then published against it"
        )

    print(
        "\n  Every figure above is a statement about a corpus this repository generated, read by\n"
        "  one reader at one version. The ratio between policies transports further than any of\n"
        "  the absolute numbers; neither is a claim about production."
    )

    if problems:
        print("\nbaseline: FAILED\n")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(
        "\nbaseline: the derived policy meets every budget it is judged "
        "against. The other two do not."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
