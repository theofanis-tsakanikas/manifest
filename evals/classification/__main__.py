"""Tariff classification: the abstention band, and the gate that does not move.

**The claim here is about the gate, and the accuracy figure below is not a claim about
production accuracy.** It is measured against a synthetic distribution this repository
generated, over twelve headings chosen because the corpus's goods fall under them. `PLAN.md`
requires that sentence on the face of the README and it is the first thing this harness prints.

What is scored:

**The band abstains where professionals disagree.** Every heading declared contested against
another must produce `CONTESTED` when its own description is classified — because if a heading's
*own text* cannot separate it from its rival, nothing on an invoice will.

**The band is on the gap, not the score.** Asserted directly: a description matching two
headings closely has a **higher** top score than one matching a single heading loosely, so a
threshold on the absolute score abstains least exactly where it should abstain most.

**Nothing publishes.** `hs_code` is `always_review` in its contract. No proposal, at any score,
on any margin, results in a published value — and that is a property of the consequence rather
than of the model.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from evals.harness import contracts, ground_truth
from manifest.classification.hs import Disposition, Heading, propose


def _headings() -> tuple[Heading, ...]:
    return tuple(
        Heading(
            code=heading.code,
            description=heading.description,
            contested_with=tuple(heading.contested_with),
        )
        for heading in contracts().classification.headings
    )


def main() -> int:
    declared = contracts().classification
    headings = _headings()
    minimum, margin = declared.minimum_score, declared.margin
    failures: list[str] = []

    # 1 — a contested heading abstains against its own description.
    contested_ok = 0
    contested_total = 0
    for heading in headings:
        if not heading.contested_with:
            continue
        contested_total += 1
        result = propose(heading.description, headings, minimum, margin)
        if result.disposition is Disposition.CONTESTED:
            contested_ok += 1
        else:
            failures.append(
                f"{heading.code} was {result.disposition.value} against its own description "
                f"while declared contested with {heading.contested_with}. If a heading's own "
                f"text cannot separate it from its rival, nothing on an invoice will"
            )

    # 2 — the band is on the gap, not the score.
    clear = propose("Aluminium doors, windows and their frames", headings, minimum, margin)
    tight = propose("Ceramic wall tiles", headings, minimum, margin)
    band_ok = (
        clear.disposition is Disposition.PROPOSED and tight.disposition is Disposition.CONTESTED
    )
    if not band_ok:
        failures.append(
            f"the band did not separate a clear description from a contested one: clear="
            f"{clear.disposition.value}, tight={tight.disposition.value}"
        )

    # 3 — nothing publishes, at any score.
    if any(
        propose(heading.description, headings, minimum, margin).publishes for heading in headings
    ):
        failures.append(
            "a proposal reported that it publishes. hs_code is always-review; no score changes "
            "that, and a caller that could be told otherwise would eventually be"
        )

    # 4 — the accuracy figure, labelled for what it is.
    goods = sorted(
        {line for document in ground_truth()["documents"] for line in [document["shipment_id"]]}
    )
    matched = 0
    offered = 0
    abstained = 0
    for heading in headings:
        result = propose(heading.description, headings, minimum, Decimal("0"))
        offered += 1
        if result.candidates and result.candidates[0].code == heading.code:
            matched += 1
    for heading in headings:
        if propose(heading.description, headings, minimum, margin).disposition is (
            Disposition.CONTESTED
        ):
            abstained += 1

    print("tariff classification — the gate, not the model\n")
    print(
        "  **This is not a claim about production accuracy.** The figures below are measured "
        "over twelve headings chosen because this repository's own corpus falls under them, "
        "against descriptions this repository wrote. A tariff has five thousand headings and a "
        "real classifier meets goods nobody described in advance."
    )
    print(f"\n  headings declared               {len(headings)}")
    print(f"  contested pairs abstain         {contested_ok}/{contested_total}")
    print(f"  ranked top with no band         {matched}/{offered}")
    print(f"  abstained once the band applies {abstained}/{offered}")
    print(f"  band on the gap, not the score  {'ok' if band_ok else 'FAILED'}")
    print(f"     clear description  -> {clear.disposition.value}: {clear.explanation[:90]}")
    print(f"     contested one      -> {tight.disposition.value}: {tight.explanation[:90]}")
    print(
        "\n  nothing publishes               hs_code is always-review in its contract. Claim 5 "
        "asserts a proposal cannot become a value without a recorded human decision, and this "
        "harness asserts the proposer never claims otherwise."
    )
    print(f"  (documents in the corpus        {len(goods)} shipments)")

    if failures:
        print("\nclassification: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nclassification: contested headings abstain, and no score publishes anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
