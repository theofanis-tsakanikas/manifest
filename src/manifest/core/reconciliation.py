"""CLAIM 4 — cross-document disagreement is surfaced, never smoothed.

The same shipment is described three times by three parties. Weight, container number, value
and package count must reconcile, and the disagreements are what the operator is paid to catch.

**Nothing here resolves anything.** A rule that fires produces a finding with both values, both
documents and the tolerance it broke. It never picks a side, never averages, never prefers the
carrier's number to the shipper's. Smoothing a disagreement is the failure this claim is named
after, and the way it arrives is always a helpful-looking function that returns "the" value.

**An abstention is not an agreement.** If either side is missing — the reader abstained, the
document does not carry the field — the rule cannot fire, and that is reported as
`NOT_COMPARABLE` rather than as a pass. A pair where one side is absent silently counting as
agreement is how a claim-4 harness reports zero mismatches on a corpus it could not read.

**The tolerance comes from the contract and there is no default.** A comparison whose author
did not decide what "agree" means is a comparison nobody has thought about, and the contract
loader refuses one.

The independence claim 4 rests on is structural and lives elsewhere: `corpus/plant.py` perturbs
a *fact about a shipment* and does not import the contract layer, so it cannot know which rule
will find what it broke. `scripts/check_planting_is_blind.py` reads the import graph and
refuses if that ever stops being true.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from manifest.core.quantity import (
    Quantity,
    QuantityError,
    Tolerance,
    Unit,
    agrees,
    dimension_of,
    parse,
)
from manifest.core.text import Rule, compare


class Outcome(StrEnum):
    """What a rule found."""

    AGREE = "agree"
    DISAGREE = "disagree"
    #: One side is missing, or the two are not of a kind that can be compared. **Not a pass.**
    NOT_COMPARABLE = "not_comparable"


class Severity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


#: The declared field types compared as quantities rather than as text.
#:
#: In `core` because two places held it — `evals/reconciliation` and, once the estate grew a
#: reconciliation step, `handlers/reconcile` — and a field that became numeric in one of them
#: would be compared as a string in the other. A string comparison of "1,000 KGS" against
#: "1000 kg" disagrees, which is a finding about formatting sent to somebody paid to catch
#: findings about shipments.
NUMERIC_TYPES: frozenset[str] = frozenset({"quantity", "money"})


@dataclass(frozen=True, slots=True)
class Side:
    """One end of a comparison: what a document says, or the fact that it says nothing."""

    document: str
    field: str
    value: str | None
    #: For a quantity: the unit the document states it in, from the field's contract.
    unit: Unit | None = None

    @property
    def present(self) -> bool:
        return self.value is not None and self.value.strip() != ""


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule, applied to one shipment.

    `explanation` is written for the person who has to act on it. ADR-0001 costs the review
    queue twenty seconds a decision, and "gross_weight disagrees" spends most of them on
    working out *by how much*.
    """

    shipment: str
    rule: str
    left: Side
    right: Side
    outcome: Outcome
    severity: Severity
    explanation: str

    @property
    def is_disagreement(self) -> bool:
        return self.outcome is Outcome.DISAGREE


class Rule_(Protocol):
    """The shape of a reconciliation rule, as the contract loader produces it."""

    id: str
    severity: str


@dataclass(frozen=True, slots=True)
class Comparison:
    """A rule reduced to what the comparison needs, so this module imports no contract type."""

    rule_id: str
    severity: Severity
    #: `None` where the rule demands exactness — see `contracts.loader.to_tolerance`.
    tolerance: Tolerance | None
    #: For a text comparison: the field's declared normalisation rules.
    comparison: tuple[Rule, ...]
    #: True where both sides are quantities and the tolerance is dimensional.
    numeric: bool


def reconcile(shipment: str, comparison: Comparison, left: Side, right: Side) -> Finding:
    """Apply one rule to one shipment's pair of values."""
    if not left.present or not right.present:
        missing = [side.document for side in (left, right) if not side.present]
        return Finding(
            shipment=shipment,
            rule=comparison.rule_id,
            left=left,
            right=right,
            outcome=Outcome.NOT_COMPARABLE,
            severity=comparison.severity,
            explanation=(
                f"nothing to compare: {', '.join(missing)} did not yield a value. An abstention "
                f"is not an agreement, and counting it as one is how a harness reports zero "
                f"mismatches on a corpus it could not read"
            ),
        )

    if comparison.numeric:
        return _numeric(shipment, comparison, left, right)
    return _textual(shipment, comparison, left, right)


def _numeric(shipment: str, comparison: Comparison, left: Side, right: Side) -> Finding:
    try:
        left_quantity = _quantity(left)
        right_quantity = _quantity(right)
    except QuantityError as exc:
        return Finding(
            shipment=shipment,
            rule=comparison.rule_id,
            left=left,
            right=right,
            outcome=Outcome.NOT_COMPARABLE,
            severity=comparison.severity,
            explanation=(
                f"a value could not be read as a number: {exc}. This is an extraction problem "
                f"and it is reported as one — calling it a disagreement would send a reviewer "
                f"to compare two documents when the fault is in one reading"
            ),
        )

    if comparison.tolerance is None:
        agree = (
            left_quantity.to(left_quantity.unit).amount
            == right_quantity.to(left_quantity.unit).amount
        )
        return Finding(
            shipment=shipment,
            rule=comparison.rule_id,
            left=left,
            right=right,
            outcome=Outcome.AGREE if agree else Outcome.DISAGREE,
            severity=comparison.severity,
            explanation=(
                f"{left_quantity} against {right_quantity}, compared exactly"
                + ("" if agree else " — they differ")
            ),
        )

    agreement = agrees(left_quantity, right_quantity, comparison.tolerance)
    return Finding(
        shipment=shipment,
        rule=comparison.rule_id,
        left=left,
        right=right,
        outcome=Outcome.AGREE if agreement.agree else Outcome.DISAGREE,
        severity=comparison.severity,
        explanation=f"{left_quantity} against {right_quantity}: {agreement.explanation}",
    )


#: Unit tokens as they are printed on these documents, mapped to the unit they name. A page
#: says `KGS`, not `kg`, and `CTNS` rather than `ctn`.
_PRINTED_UNITS: dict[str, Unit] = {
    "KG": Unit.KILOGRAM, "KGS": Unit.KILOGRAM, "KILOS": Unit.KILOGRAM,
    "LB": Unit.POUND, "LBS": Unit.POUND, "POUNDS": Unit.POUND,
    "T": Unit.TONNE, "MT": Unit.TONNE, "TON": Unit.TONNE, "TONS": Unit.TONNE,
    "M3": Unit.CUBIC_METRE, "CBM": Unit.CUBIC_METRE,
    "FT3": Unit.CUBIC_FOOT, "CFT": Unit.CUBIC_FOOT,
    "CTN": Unit.CARTON, "CTNS": Unit.CARTON, "CARTONS": Unit.CARTON,
    "PCS": Unit.PIECE, "PC": Unit.PIECE, "PIECES": Unit.PIECE,
    "PLT": Unit.PALLET, "PLTS": Unit.PALLET, "PALLETS": Unit.PALLET,
}  # fmt: skip


def _quantity(side: Side) -> Quantity:
    """The side's value as a quantity, in the unit the **document** states.

    The first version of this trusted the contract's declared unit and dropped the unit token
    printed on the page. It was wrong, and claim 4's harness proved it: 384 shipments that
    agree were reported as disagreeing, every one of them a packing list stating a weight in
    pounds on an otherwise metric page. `docs/SCENARIO.md` names that pathology in as many
    words, and a contract that declares one unit per field cannot express it.

    So the printed token decides, and the contract's unit is the fallback for a document that
    prints a bare number. The token is only honoured when it names a unit of the **declared
    dimension** — a reader returning `KG5` for `KGS`, or a mass field whose token reads `CTNS`,
    falls back rather than silently changing what is being compared. That is the conservative
    direction: a wrong fallback compares the right dimension in the wrong unit and disagrees
    loudly; a wrong token would compare two different dimensions and could agree by accident.
    """
    assert side.value is not None
    text = side.value.strip()

    kept: list[str] = []
    for index, character in enumerate(text):
        if character.isdigit() or character in ".,- ":
            kept.append(character)
        elif kept and any(entry.isdigit() for entry in kept):
            text = text[index:]
            break
    else:
        text = ""
    if not any(character.isdigit() for character in kept):
        raise QuantityError(f"{side.value!r} carries no number")

    declared = side.unit or Unit.PIECE
    token = "".join(character for character in text if character.isalpha()).upper()
    printed = _PRINTED_UNITS.get(token)
    unit = (
        printed
        if printed is not None and dimension_of(printed) is dimension_of(declared)
        else declared
    )
    return parse("".join(kept), unit)


def _textual(shipment: str, comparison: Comparison, left: Side, right: Side) -> Finding:
    assert left.value is not None and right.value is not None
    result = compare(left.value, right.value, comparison.comparison)
    return Finding(
        shipment=shipment,
        rule=comparison.rule_id,
        left=left,
        right=right,
        outcome=Outcome.AGREE if result.agree else Outcome.DISAGREE,
        severity=comparison.severity,
        explanation=(
            f"{left.value!r} against {right.value!r}"
            + ("" if result.agree else f": {result.explanation}")
        ),
    )


def summarise(findings: list[Finding]) -> dict[str, object]:
    """Counts, by outcome and severity, and the shipments carrying a disagreement.

    The shipment set is what claim 4 is scored on. Counting *rule firings* would make the score
    depend on how many rules happen to compare a perturbed field — one altered weight breaks
    its agreement with the bill of lading and may break its agreement with the invoice — and
    the generator does not know how many rules exist. Counting shipments is a statement about
    the world rather than about the contract.
    """
    disagreeing = {finding.shipment for finding in findings if finding.is_disagreement}
    return {
        "rules_applied": len(findings),
        "agree": sum(1 for finding in findings if finding.outcome is Outcome.AGREE),
        "disagree": sum(1 for finding in findings if finding.is_disagreement),
        "not_comparable": sum(
            1 for finding in findings if finding.outcome is Outcome.NOT_COMPARABLE
        ),
        "blocking": sum(
            1
            for finding in findings
            if finding.is_disagreement and finding.severity is Severity.BLOCKING
        ),
        "shipments_with_a_disagreement": sorted(disagreeing),
    }


def zero() -> Decimal:
    """Zero, as a Decimal, so callers never introduce a float into this module."""
    return Decimal(0)
