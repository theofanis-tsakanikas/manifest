"""CLAIM 4 — cross-document disagreement is surfaced, never smoothed.

**Two numbers, and reporting only one of them would be the overclaim.**

*The reconciliation logic*, scored on the values that were actually printed. Every planted
mismatch must be found and nothing else may fire. This is the claim about the rules, the
tolerances and the unit handling, and it is the one that can be exactly right.

*The system end to end*, scored on the values the reader extracted. Here an extraction error
looks exactly like a disagreement — two documents genuinely say different things, because one
of them was misread. That number is worse, it is the honest one, and the gap between the two
is the cost of reading a page rather than being handed a record.

**The independence.** `corpus/plant.py` perturbs a fact about a shipment and cannot import the
contract layer (`scripts/check_planting_is_blind.py` reads the import graph). It does not know
which rule compares what it broke. So the expectation here is derived from ground truth by a
separate path: **which shipments carry a planted disagreement**, never "which rules should
fire". One altered weight may break two rules, and the generator does not know how many rules
exist.
"""

from __future__ import annotations

import argparse
import sys

from evals.harness import contracts, ground_truth, score_all
from manifest.contracts.loader import to_tolerance
from manifest.core.calibration import Outcome as ReadOutcome
from manifest.core.reconciliation import (
    NUMERIC_TYPES,
    Comparison,
    Severity,
    Side,
    reconcile,
    summarise,
)

#: Field types compared as numbers rather than as strings.


def _comparison(rule) -> Comparison:
    contract = contracts().document(rule.left.document).field(rule.left.field)
    return Comparison(
        rule_id=rule.id,
        severity=Severity(rule.severity),
        tolerance=to_tolerance(rule.tolerance),
        comparison=tuple(contract.comparison),
        numeric=contract.type.value in NUMERIC_TYPES,
    )


def _side(values: dict, document: str, field: str) -> Side:
    contract = contracts().document(document).field(field)
    return Side(
        document=document,
        field=field,
        value=values.get((document, field)),
        unit=contract.unit,
    )


def _run(values_by_shipment: dict[str, dict]) -> list:
    findings = []
    for shipment, values in sorted(values_by_shipment.items()):
        for rule in contracts().reconciliation.rules:
            findings.append(
                reconcile(
                    shipment,
                    _comparison(rule),
                    _side(values, rule.left.document, rule.left.field),
                    _side(values, rule.right.document, rule.right.field),
                )
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.parse_args()

    truth = ground_truth()

    # The expectation, derived from ground truth by a path that never touches a rule id.
    planted = {entry["shipment_id"] for entry in truth["planted_mismatches"]}

    printed: dict[str, dict] = {}
    for document in truth["documents"]:
        values = printed.setdefault(document["shipment_id"], {})
        for entry in document["fields"]:
            values[(document["document_id"], entry["field"])] = entry["value"]

    extracted: dict[str, dict] = {}
    for entry in score_all():
        values = extracted.setdefault(entry.shipment, {})
        values[(entry.document, entry.field)] = (
            entry.extracted.value if entry.outcome is not ReadOutcome.MISSING else None
        )

    on_printed = summarise(_run(printed))
    on_extracted = summarise(_run(extracted))

    found = set(on_printed["shipments_with_a_disagreement"])
    missed = planted - found
    spurious = found - planted

    print("claim 4 — cross-document disagreement\n")
    print(f"  shipments                 {len(printed)}")
    print(f"  planted disagreements     {len(planted)}")
    print("\n  A. the reconciliation logic, on the values that were printed")
    print(f"     found                  {len(found & planted)}/{len(planted)}")
    print(f"     false positives        {len(spurious)}")
    print(
        f"     rules applied          {on_printed['rules_applied']}  "
        f"(agree {on_printed['agree']}, disagree {on_printed['disagree']}, "
        f"not comparable {on_printed['not_comparable']})"
    )

    system_found = set(on_extracted["shipments_with_a_disagreement"])
    print("\n  B. the system end to end, on the values the reader extracted")
    print(f"     found                  {len(system_found & planted)}/{len(planted)}")
    print(f"     also flagged           {len(system_found - planted)} shipments")
    print(
        f"     not comparable         {on_extracted['not_comparable']} pairs — a side the "
        f"reader abstained on. An abstention is not an agreement and is never counted as one."
    )
    print(
        "\n     The gap between A and B is not a defect in the rules. It is what reading a "
        "degraded page costs: a misread value makes two documents genuinely disagree, and the "
        "system is right to say so. Claim 1's threshold is what stops those reaching a "
        "declaration; this figure is what they cost the review queue in the meantime."
    )
    print(
        "\n  Zero false positives above is a statement about a set this repository authored. "
        "The planting is blind to the contract; the expectation is derived from ground truth "
        "by a separate path; and both are enforced rather than intended."
    )

    failures = []
    if missed:
        failures.append(f"{len(missed)} planted disagreement(s) not found: {sorted(missed)[:8]}")
    if spurious:
        failures.append(
            f"{len(spurious)} shipment(s) flagged that carry no planted mismatch, on the "
            f"printed values: {sorted(spurious)[:8]}. On printed values a finding can only "
            f"come from a rule, so each one is a rule firing on documents that agree"
        )
    if failures:
        print("\nclaim 4: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(
        f"\nclaim 4: exactly {len(planted)} planted disagreements found, zero false positives "
        f"on the set that agrees."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
