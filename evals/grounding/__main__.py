"""Classification with retrieval, and the gate that refuses a proposal nothing supports.

**Claim 2's argument, applied to text.** A published field must point at a place on a page, and
a field whose value cannot be located does not publish — nobody, including an approver, can
approve it into existence, because there is nothing for the approval to be about. A tariff
classification has the same shape and is almost never treated that way: what decides whether a
proposal is worth a reviewer's time is not how confident it is but **what in the nomenclature
says so**.

Four things are measured here, and the last two are the ones that matter.

1. **A grounded proposal names the terms that grounded it.** Not the heading code — a code is a
   label and matching one proves nothing — but the words that make this heading different from
   the one beside it.
2. **A proposal from outside the retrieved context is refused.** A candidate the retrieval never
   surfaced is one nothing in the context supports.
3. **Every contested pair still abstains**, and the grounding gate must not rescue either
   member. This is the trap: retrieval makes both members of a contested pair look well
   supported, because they *are* — the whole reason they are contested is that the nomenclature
   backs both readings. A grounding gate that turned "well supported" into "proposable" would
   have used evidence to defeat an abstention.
4. **Goods nothing in the nomenclature covers are refused rather than assigned the nearest
   heading.** Offering the best of a bad set is the unhelpful thing that looks helpful.

Nothing here publishes. `hs_code` is `always_review`, and `Proposal.publishes` is `False`
unconditionally, so every figure below is about what reaches a *human* — never about what
reaches a record.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from manifest.classification.grounding import Grounding, Note, check, distinguishing_terms, retrieve
from manifest.classification.hs import Disposition, Heading, propose

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "classification"

#: Goods descriptions taken from the corpus's own commercial invoices, plus two that nothing in
#: this nomenclature covers. The second group is the point: a classifier is judged as much by
#: what it declines as by what it proposes.
GOODS: tuple[tuple[str, str | None], ...] = (
    ("Glazed ceramic wall tiles, 200x200mm, white", "690722"),
    ("Unglazed terracotta floor tiles, quarry finish", "690721"),
    ("Extra virgin olive oil, cold pressed, 5L tins", "150910"),
    ("Refined olive oil, deodorised, bulk", "150990"),
    ("Printed cotton bed linen, double, floral design", "630221"),
    ("Aluminium window frames with thresholds", "761010"),
    ("Electric storage water heater, 80 litre", "851610"),
    ("Woven polypropylene strip sacks for cement packing", "630533"),
    ("Galvanised steel wire rope, 12mm, uninsulated", "731210"),
    # Nothing here covers these.
    ("Live ornamental tropical fish, freshwater", None),
    ("Second-hand diesel locomotive parts", None),
)


def _load() -> tuple[tuple[Note, ...], tuple[Heading, ...], dict]:
    notes_file = yaml.safe_load((CONTRACTS / "notes.yaml").read_text(encoding="utf-8"))
    headings_file = yaml.safe_load((CONTRACTS / "headings.yaml").read_text(encoding="utf-8"))

    described = {entry["code"]: entry["description"] for entry in headings_file["headings"]}
    notes = tuple(
        Note(code=entry["code"], description=described[entry["code"]], note=entry["note"])
        for entry in notes_file["notes"]
        if entry["code"] in described
    )
    headings = tuple(
        Heading(
            code=entry["code"],
            description=entry["description"],
            contested_with=tuple(entry.get("contested_with", ())),
        )
        for entry in headings_file["headings"]
    )
    return notes, headings, {**notes_file, **headings_file}


def main() -> int:
    notes, headings, settings = _load()
    minimum_terms = int(settings["minimum_grounding_terms"])
    limit = int(settings["retrieval_limit"])
    problems: list[str] = []

    missing_notes = {heading.code for heading in headings} - {note.code for note in notes}
    if missing_notes:
        problems.append(
            f"headings {sorted(missing_notes)} have no note. A heading the retriever can never "
            f"surface is one no proposal can be grounded against, and it would look like the "
            f"classifier declining rather than like the nomenclature having a hole in it"
        )

    print("classification with retrieval — a proposal must point at the text it came from\n")
    print(f"  nomenclature      {len(notes)} entries — AUTHORED, not a tariff reference")
    print("                    see contracts/classification/notes.yaml")
    print(f"  retrieval limit   {limit}")
    print(f"  grounding needs   {minimum_terms} distinguishing term(s) present\n")

    grounded = ungrounded = abstained = declined = 0
    declared_contests = margin_contests = 0

    for goods, expected in GOODS:
        context = retrieve(goods, notes, limit=limit)
        # **Proposed from the retrieved context only.** Ranking the full heading list and then
        # checking grounding afterwards would let a proposal exist that retrieval never
        # surfaced — the shape where a model answers from memory and the context is decoration.
        surfaced = tuple(
            heading
            for heading in headings
            if heading.code in {entry.note.code for entry in context}
        )
        proposal = propose(
            goods,
            surfaced,
            minimum_score=Decimal(str(settings["minimum_score"])),
            margin=Decimal(str(settings["margin"])),
        )

        if proposal.disposition is Disposition.NO_PROPOSAL:
            declined += 1
            print(f"  {goods[:52]:52} declined")
            if expected is not None:
                problems.append(
                    f"{goods!r}: nothing proposed, and {expected} is in this nomenclature. A "
                    f"classifier that declines what it covers sends work to a human for no "
                    f"reason, which is queue volume bought with nothing"
                )
            continue

        if proposal.disposition is Disposition.CONTESTED:
            abstained += 1
            pair = [candidate.code for candidate in proposal.candidates[:2]]
            # **Two different reasons to abstain, and they are worth telling apart.**
            #
            # A *declared* contest is the system working exactly as intended: professionals
            # argue these pairs and nothing in a goods description resolves them. A *margin*
            # contest is two headings that happen to score within the band — which is correct
            # and conservative, and is also a signal about the retriever rather than about the
            # trade. Reporting them as one number would hide retrieval quality behind a
            # doctrine.
            declared = tuple(
                heading.contested_with for heading in headings if heading.code == pair[0]
            )
            by_declaration = bool(declared) and pair[1] in declared[0]
            reason = "declared" if by_declaration else "margin"
            if by_declaration:
                declared_contests += 1
            else:
                margin_contests += 1
            print(f"  {goods[:52]:52} contested ({', '.join(pair)}) — {reason}")
            # The trap: the gate must not rescue a contested pair. Both members are genuinely
            # well supported — that is *why* they are contested — so a grounding check that
            # promoted one would be evidence defeating an abstention.
            for candidate in proposal.candidates[:2]:
                verdict = check(
                    code=candidate.code, context=context, notes=notes, minimum_terms=minimum_terms
                )
                if verdict.grounding is Grounding.GROUNDED and proposal.publishes:
                    problems.append(
                        f"{goods!r}: {candidate.code} is grounded and the proposal publishes. "
                        f"Grounding is permission to show a human, never permission to publish"
                    )
            continue

        top = proposal.candidates[0]
        verdict = check(code=top.code, context=context, notes=notes, minimum_terms=minimum_terms)
        if verdict.refuses:
            ungrounded += 1
            print(f"  {goods[:52]:52} UNGROUNDED ({top.code})")
            if expected == top.code:
                problems.append(
                    f"{goods!r}: {top.code} is the right heading and the grounding gate refused "
                    f"it — {verdict.reason}. A gate that refuses correct proposals is one "
                    f"somebody mutes within a week"
                )
            continue

        grounded += 1
        shown = ", ".join(verdict.found[:3])
        print(f"  {goods[:52]:52} {top.code}  grounded on: {shown}")

        # **Every term the verdict claims it found must actually be in the context.**
        #
        # Without this the gate can report `grounded` on terms that are not there — which is
        # what happens the moment somebody "simplifies" the check to look for the heading *code*
        # in the context instead of the terms. The code is in the note by construction, so that
        # version always passes, and `found` becomes a list of words nobody looked for.
        haystack = " ".join(entry.note.text for entry in context).casefold()
        absent = [term for term in verdict.found if term not in haystack]
        if absent:
            problems.append(
                f"{goods!r}: {top.code} was grounded on {absent}, and those terms are not in "
                f"the retrieved context. The gate is reporting a justification it did not "
                f"check — the failure is not a wrong answer, it is a control describing work "
                f"it did not do"
            )
        if expected is not None and top.code != expected:
            problems.append(
                f"{goods!r}: proposed {top.code}, expected {expected}. Reported rather than "
                f"tuned away: this is a lexical retriever over an authored nomenclature, and "
                f"the figure is a statement about both"
            )

    print(
        f"\n  grounded {grounded}   ungrounded {ungrounded}   contested {abstained} "
        f"({declared_contests} declared, {margin_contests} by margin)   declined {declined}\n"
    )
    if margin_contests:
        print(
            f"  {margin_contests} abstention(s) came from the margin rather than from a declared\n"
            f"  contest — two headings scoring within the band. Conservative and correct, and\n"
            f"  also a statement about this retriever rather than about the trade. Separated so\n"
            f"  that improving retrieval shows up as this number falling, instead of hiding\n"
            f"  inside a doctrine that says abstention is safe.\n"
        )

    # **Retrieval has to be selective, or grounding is decoration.**
    #
    # A context wide enough to contain the whole nomenclature grounds every heading in it. So
    # the goods nothing here covers must retrieve *nothing* — and that is a property of the
    # retriever, checked separately from the classifier's disposition, because a classifier that
    # declines for the right reason and a retriever that returns everything look identical from
    # the outside.
    for goods, expected in GOODS:
        if expected is not None:
            continue
        surfaced_for_uncovered = retrieve(goods, notes, limit=limit)
        if surfaced_for_uncovered:
            problems.append(
                f"{goods!r} is covered by nothing in this nomenclature and retrieval returned "
                f"{len(surfaced_for_uncovered)} entry(ies): "
                f"{', '.join(entry.note.code for entry in surfaced_for_uncovered[:3])}. A "
                f"retriever that returns something for everything makes the grounding gate "
                f"decoration — every heading in a wide context grounds"
            )

    # A proposal from outside the retrieved context.
    outside = check(
        code="999999",
        context=retrieve(GOODS[0][0], notes, limit=limit),
        notes=notes,
        minimum_terms=minimum_terms,
    )
    print(f"  a heading the retrieval never surfaced: {outside.grounding.value}")
    if not outside.refuses:
        problems.append(
            "a heading absent from the nomenclature was grounded. The gate is then checking "
            "nothing: any code would pass, and the context would be decoration"
        )

    # And the gate with nothing retrieved.
    empty = check(code="690722", context=(), notes=notes, minimum_terms=minimum_terms)
    print(f"  the gate with an empty context:         {empty.grounding.value}")
    if empty.grounding is not Grounding.NO_CONTEXT:
        problems.append(
            "an empty context did not report `no_context`. It is distinct from `ungrounded` "
            "because the fixes differ — a hole in the nomenclature against a proposal nothing "
            "supports — and merging them hides the first behind the second"
        )

    # Every contested pair must have terms that tell its members apart, or the abstention is
    # being carried by the declaration alone and the retrieval adds nothing.
    for heading in headings:
        for other in heading.contested_with:
            mine = set(distinguishing_terms(heading.code, notes))
            theirs = set(distinguishing_terms(other, notes))
            if not (mine - theirs):
                problems.append(
                    f"{heading.code} and {other} are declared contested and share every "
                    f"distinguishing term. The abstention is carried entirely by the "
                    f"declaration, and the retrieval is not contributing to it"
                )

    if problems:
        print("\ngrounded classification: FAILED\n")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(
        "\ngrounded classification: a proposal that cannot point at the text it came from does\n"
        "  not go forward, contested pairs still abstain with both members well supported, and\n"
        "  nothing publishes on any score."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
