"""CLAIM 5 — the human loop is real, and measured.

Both halves, and the second is the one nobody builds.

**A field below its threshold cannot be published without a recorded human decision.** Asserted
structurally: `publishable` refuses without one, there is no override argument, and doctrine
rule 7's unopenable door is checked here too — a field with no provenance cannot be *approved*
into existence, only replaced by a value a human located themselves.

**The queue is a declared finite resource, and exceeding it fails the build.** This is where
claim 1 and claim 5 meet, and where the project's argument becomes arithmetic. The thresholds
derived in `evals/calibration/` imply a review volume; `contracts/review/capacity.yaml` declares
what four reviewers can absorb; and if the first exceeds the second **at the peak multiplier**,
this exits non-zero.

Everything here is **modelled**. Nothing in this repository has observed a reviewer, the
capacity figures are declared scenario parameters, and the projection says so wherever it
appears.
"""

from __future__ import annotations

import datetime as _datetime
import sys
from decimal import Decimal

from evals.harness import by_field, contracts, field_contract, score_all
from manifest.core.calibration import Outcome, derive
from manifest.core.review import (
    Capacity,
    Decision,
    Item,
    Reason,
    Record,
    integrity,
    project,
    publishable,
)


def _capacity() -> Capacity:
    declared = contracts().review
    return Capacity(
        reviewers=declared.reviewers,
        productive_hours_per_day=declared.productive_hours_per_day,
        seconds_per_decision=declared.seconds_per_decision,
        peak_multiplier=declared.peak_multiplier,
        documents_per_day=declared.documents_per_day,
        minimum_seconds_on_task=declared.minimum_seconds_on_task,
        sampled_rereview_rate=declared.sampled_rereview_rate,
        rubber_stamp_agreement_rate=declared.rubber_stamp_agreement_rate,
    )


def _structural_checks(capacity: Capacity) -> list[str]:
    """The half that is a property of the code rather than a measurement."""
    failures = []

    below = Item(
        document="customs_declaration",
        field="declared_value",
        reason=Reason.BELOW_THRESHOLD,
        value="81832.10",
        confidence=0.41,
        has_provenance=True,
    )
    if publishable(below, None)[0]:
        failures.append("a field below its threshold published with no recorded decision")
    if not publishable(
        below,
        Record(
            document=below.document,
            field=below.field,
            reviewer="reviewer-1",
            decision=Decision.APPROVED,
            value=below.value,
            seconds_on_task=Decimal("22"),
            agreed_with_model=True,
        ),
    )[0]:
        failures.append("a recorded approval did not publish; the queue would be a dead end")

    orphan = Item(
        document="bill_of_lading",
        field="gross_weight",
        reason=Reason.NO_PROVENANCE,
        value="27000",
        confidence=0.9,
        has_provenance=False,
    )
    approved = Record(
        document=orphan.document,
        field=orphan.field,
        reviewer="reviewer-2",
        decision=Decision.APPROVED,
        value=orphan.value,
        seconds_on_task=Decimal("40"),
        agreed_with_model=True,
    )
    if publishable(orphan, approved)[0]:
        failures.append(
            "a field with no provenance was approved into existence. Doctrine rule 7: nobody, "
            "including an approver, has the information the approval would be about"
        )
    supplied = Record(
        document=orphan.document,
        field=orphan.field,
        reviewer="reviewer-2",
        decision=Decision.SUPPLIED,
        value="27000",
        seconds_on_task=Decimal("55"),
        agreed_with_model=False,
    )
    if not publishable(orphan, supplied)[0]:
        failures.append(
            "a human who located the value themselves could not record it. The one unopenable "
            "door would have become a wall, and the work would move outside the system"
        )
    return failures


def main() -> int:
    capacity = _capacity()
    scored = score_all()
    grouped = by_field(scored)

    failures = _structural_checks(capacity)

    # ── What the derived thresholds cost the queue ───────────────────────────
    queued = 0
    total = 0
    by_reason: dict[str, int] = {}
    for name, entries in grouped.items():
        contract = field_contract(name)
        observations = [entry.observation for entry in entries]
        total += len(observations)

        if contract.always_review:
            queued += len(observations)
            by_reason["always_review"] = by_reason.get("always_review", 0) + len(observations)
            continue

        threshold = derive(name, observations, contract.error_budget or Decimal("0"))
        if threshold.always_review or threshold.value is None:
            queued += len(observations)
            by_reason["no_derivable_threshold"] = by_reason.get("no_derivable_threshold", 0) + len(
                observations
            )
            continue

        low = sum(
            1
            for entry in observations
            if entry.outcome is not Outcome.MISSING and entry.confidence < threshold.value
        )
        missing = sum(1 for entry in observations if entry.outcome is Outcome.MISSING)
        queued += low + missing
        by_reason["below_threshold"] = by_reason.get("below_threshold", 0) + low
        by_reason["abstained"] = by_reason.get("abstained", 0) + missing

    share = Decimal(queued) / Decimal(total or 1)
    fields_per_document = Decimal(total) / Decimal(len({(e.shipment, e.document) for e in scored}))
    projection = project(
        capacity,
        fields_per_document=fields_per_document,
        queued_share=share,
        by_reason={
            reason: Decimal(count) / Decimal(total or 1) for reason, count in by_reason.items()
        },
    )

    # ── Reviewer integrity, on a modelled set of decisions ───────────────────
    #
    # Modelled, and it has to be: no reviewer has ever used this system. What is being proved
    # is that the *detector* names the pattern, so the decisions below are constructed to
    # contain one of each — a rubber stamp, a contrarian, and somebody working normally.
    records = (
        [
            Record("d", "f", "reviewer-1", Decision.APPROVED, "v", Decimal("24"), True)
            for _ in range(200)
        ]
        + [
            Record("d", "f", "reviewer-2", Decision.APPROVED, "v", Decimal("2"), True)
            for _ in range(400)
        ]
        + [
            Record("d", "f", "reviewer-3", Decision.CORRECTED, "v", Decimal("30"), False)
            for _ in range(150)
        ]
        + [
            Record("d", "f", "reviewer-1", Decision.CORRECTED, "v", Decimal("31"), False)
            for _ in range(40)
        ]
    )
    reports = integrity(capacity, records)

    print("claim 5 — the human loop is real, and measured\n")
    print("  a. a field below its threshold cannot publish without a recorded decision")
    print("     structural checks              " + ("ok" if not failures else "FAILED"))
    print(
        "     a field with no provenance cannot be APPROVED, only SUPPLIED by a human who "
        "located it themselves (doctrine rule 7)"
    )

    print("\n  b. the queue is a declared finite resource — MODELLED, never measured")
    print(f"     declared capacity              {projection.capacity_per_day:,} decisions/day")
    print(
        f"     fields extracted               {projection.fields_per_day:,}/day "
        f"({fields_per_document:.1f} per document x {capacity.documents_per_day:,} documents)"
    )
    print(f"     of those, queued               {projection.queued_per_day:,}/day ({share:.1%})")
    for reason, count in sorted(projection.by_reason.items(), key=lambda item: -item[1]):
        print(f"        {reason:<26} {count:,}/day")
    print(f"     load at the mean               {projection.mean_load:.1f}x capacity")
    print(
        f"     load at the peak (x{capacity.peak_multiplier})          "
        f"{projection.peak_load:.1f}x capacity"
    )

    print("\n  c. reviewer integrity — the pattern named, not averaged away")
    for report in reports:
        print(
            f"     {report.reviewer:<12} {report.decisions:>4} decisions, "
            f"{report.agreement_rate:>6.1%} agreement, median {report.median_seconds}s"
        )
        for finding in report.findings:
            print(f"        FINDING: {finding}")

    if not projection.fits:
        # The gate has fired. It may only pass on a **named, dated, expiring acceptance** —
        # doctrine rule 6 — and the acceptance changes nothing about publication. Reading a
        # clock here is fine and is the point: this harness is not `core`, and the whole value
        # of an expiry is that the finding returns on its own.
        today = _datetime.date.today().isoformat()
        live = [entry for entry in contracts().acceptances.acceptances if entry.expires_on > today]
        lapsed = [
            entry for entry in contracts().acceptances.acceptances if entry.expires_on <= today
        ]
        print("\n  d. the capacity gate has fired, and it is accepted rather than silenced")
        for entry in live:
            print(f"     ACCEPTED  {entry.id}  by {entry.accepted_by}, expires {entry.expires_on}")
            print(f"       finding:  {' '.join(entry.finding.split())}")
            print(f"       cause:    {' '.join(entry.cause.split())}")
            print(f"       response: {' '.join(entry.response.split())}")
        for entry in lapsed:
            print(f"     LAPSED    {entry.id}  expired {entry.expires_on}")
        if not live:
            failures.append(
                f"the derived thresholds queue {projection.queued_per_day:,} fields a day "
                f"against a declared capacity of {projection.capacity_per_day:,} — "
                f"{projection.peak_load:.1f}x at the peak — and no unexpired acceptance covers "
                f"it. ADR-0001 enumerates the four permitted responses; raising a confidence "
                f"threshold to reduce queue volume without changing the error budget is not one "
                f"of them, because it inverts the derivation and turns a derived number into a "
                f"chosen one wearing a derivation's clothes"
            )
    if not any(report.findings for report in reports):
        failures.append(
            "the integrity report named no pattern on a set constructed to contain three. The "
            "detector is not detecting"
        )

    if failures:
        print("\nclaim 5: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(
        "\nclaim 5: nothing publishes below threshold without a recorded decision; the queue's "
        "capacity is declared and measured against; and where it is exceeded, the overage is "
        "accepted by name, with an expiry, and printed in full on every run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
