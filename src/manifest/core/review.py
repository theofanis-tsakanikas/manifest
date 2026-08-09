"""CLAIM 5 — the human loop is real, and measured.

ADR-0001. Two halves, and the second is the one nobody builds.

**A field below its threshold cannot be published without a recorded human decision.** That is
the easy half, and it is a structural property here: `publishable` refuses without a decision,
and there is no argument that overrides it.

**The queue is a declared finite resource, and exceeding it fails the build.** Four reviewers
at six productive hours and twenty seconds a decision is 4,320 decisions a day against 18,000
documents at roughly fifteen fields each — the queue absorbs about 1.6% of extracted fields,
and about 0.5% on a peak day. A threshold routing 5% to review has silently decided its
reviewers will work three times their hours, and what follows is not a backlog. Backlogs are
visible. What follows is a 100% agreement rate.

**And a human decision is evidence only if the human was plausibly looking.** Time on task,
agreement rate with the model in *both* tails, and sampled re-review. The report names the
pattern rather than averaging it away: "reviewer 3 approved 84% of items in under four seconds"
is a finding; "average review time 11.2s" is a number that hides it.

One rule from the doctrine that lands here and nowhere else. **A field with no provenance
cannot be overridden into existence** (rule 7). Such a field may still be *queued* — a human
can look at the page — but what they return is a value **they** located, with its own
provenance. That is a different fact from an approval, and the two are recorded differently,
because "approved" and "supplied by a human who found it themselves" say different things about
where a published number came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Decision(StrEnum):
    """What a reviewer did.

    `SUPPLIED` is separate from `APPROVED` on purpose — doctrine rule 7. An approval is a
    statement about the system's value; a supplied value is a new value with a human's
    provenance, and collapsing them would let a field with no provenance acquire one by being
    looked at.
    """

    APPROVED = "approved"
    CORRECTED = "corrected"
    SUPPLIED = "supplied"
    REJECTED = "rejected"


class Reason(StrEnum):
    """Why an item is in the queue. Reported per reason, because they have different fixes."""

    BELOW_THRESHOLD = "below_threshold"
    ALWAYS_REVIEW = "always_review"
    NO_PROVENANCE = "no_provenance"
    PROVENANCE_REFUSED = "provenance_refused"
    DISAGREEMENT = "disagreement"


@dataclass(frozen=True, slots=True)
class Capacity:
    """The declared finite resource. Every figure is a scenario parameter, not a measurement."""

    reviewers: int
    productive_hours_per_day: Decimal
    seconds_per_decision: Decimal
    peak_multiplier: Decimal
    documents_per_day: int
    minimum_seconds_on_task: Decimal
    sampled_rereview_rate: Decimal
    rubber_stamp_agreement_rate: Decimal

    @property
    def decisions_per_day(self) -> Decimal:
        return (
            Decimal(self.reviewers)
            * self.productive_hours_per_day
            * Decimal(3600)
            / self.seconds_per_decision
        )


@dataclass(frozen=True, slots=True)
class Item:
    """One field waiting for a human."""

    document: str
    field: str
    reason: Reason
    value: str | None
    confidence: float
    has_provenance: bool


@dataclass(frozen=True, slots=True)
class Record:
    """A recorded human decision. Without one, nothing below threshold publishes."""

    document: str
    field: str
    reviewer: str
    decision: Decision
    value: str | None
    seconds_on_task: Decimal
    #: Whether the reviewer's answer matched what the system proposed. Carried per decision
    #: because the agreement *rate* is meaningless without the denominator, and because both
    #: tails are findings.
    agreed_with_model: bool


def publishable(item: Item, decision: Record | None) -> tuple[bool, str]:
    """Whether this field may be published, and why not.

    The structural half of claim 5. There is no `force`, no `override`, no severity argument
    that changes the answer — a door with a key in the signature is a door that is open.
    """
    if decision is None:
        return False, (
            f"{item.document}.{item.field} is in the queue for {item.reason.value} and no "
            f"human decision is recorded. It does not publish"
        )
    if decision.decision is Decision.REJECTED:
        return False, "the reviewer rejected the value"
    if item.reason is Reason.NO_PROVENANCE and decision.decision is Decision.APPROVED:
        # Doctrine rule 7, and the one door with no key. Nobody — including an approver — has
        # the information the approval would be about.
        return False, (
            f"{item.document}.{item.field} has no provenance, so there is nothing to approve. "
            f"A human may supply a value they located themselves, which is a new value with "
            f"their provenance and is recorded as SUPPLIED — but a field the system cannot "
            f"point to on a page cannot be approved into existence"
        )
    return True, f"published on a recorded {decision.decision.value} by {decision.reviewer}"


@dataclass(frozen=True, slots=True)
class Projection:
    """What a set of thresholds costs the queue, as a model.

    Modelled, not measured. Nothing here has observed a reviewer, and the figure says so
    wherever it appears.
    """

    fields_per_day: int
    queued_per_day: int
    capacity_per_day: int
    peak_multiplier: Decimal
    by_reason: dict[str, int]

    @property
    def mean_load(self) -> Decimal:
        return Decimal(self.queued_per_day) / Decimal(self.capacity_per_day or 1)

    @property
    def peak_load(self) -> Decimal:
        """Capacity does not rise on a peak day. The volume does."""
        return self.mean_load * self.peak_multiplier

    @property
    def fits(self) -> bool:
        return self.peak_load <= 1


def project(
    capacity: Capacity,
    fields_per_document: Decimal,
    queued_share: Decimal,
    by_reason: dict[str, Decimal],
) -> Projection:
    """The daily review load a set of thresholds implies, at the declared volumes.

    Scored at the **peak**, not the mean. A queue sized for the mean is a queue that is three
    times over capacity on Monday morning, every Monday morning, and the reviewers absorb it by
    reading less.
    """
    fields = int(Decimal(capacity.documents_per_day) * fields_per_document)
    queued = int(Decimal(fields) * queued_share)
    return Projection(
        fields_per_day=fields,
        queued_per_day=queued,
        capacity_per_day=int(capacity.decisions_per_day),
        peak_multiplier=capacity.peak_multiplier,
        by_reason={
            reason: int(Decimal(fields) * share) for reason, share in sorted(by_reason.items())
        },
    )


@dataclass(frozen=True, slots=True)
class ReviewerIntegrity:
    """What one reviewer's decisions look like, and what that pattern is called."""

    reviewer: str
    decisions: int
    agreement_rate: Decimal
    unexamined: int
    median_seconds: Decimal
    findings: tuple[str, ...]


def integrity(
    capacity: Capacity, records: list[Record] | tuple[Record, ...]
) -> tuple[ReviewerIntegrity, ...]:
    """Per reviewer: the rate, the count, and the finding named in words.

    A metric whose failure mode is being averaged into invisibility is not a control, so this
    returns sentences rather than a number for somebody else to threshold.
    """
    grouped: dict[str, list[Record]] = {}
    for record in records:
        grouped.setdefault(record.reviewer, []).append(record)

    report = []
    for reviewer, decisions in sorted(grouped.items()):
        agreed = sum(1 for record in decisions if record.agreed_with_model)
        rate = Decimal(agreed) / Decimal(len(decisions))
        fast = [
            record
            for record in decisions
            if record.seconds_on_task < capacity.minimum_seconds_on_task
        ]
        seconds = sorted(record.seconds_on_task for record in decisions)
        findings = []

        if rate >= capacity.rubber_stamp_agreement_rate:
            findings.append(
                f"{reviewer} agreed with the system on {rate:.1%} of {len(decisions)} "
                f"decisions. A reviewer who never disagrees is not a control; they are a "
                f"rubber stamp with a login"
            )
        if rate <= 1 - capacity.rubber_stamp_agreement_rate:
            findings.append(
                f"{reviewer} disagreed with the system on {1 - rate:.1%} of {len(decisions)} "
                f"decisions. The same finding wearing the opposite sign: a reviewer who never "
                f"agrees is not reading either, and it is the tail nobody alerts on"
            )
        if fast:
            share = Decimal(len(fast)) / Decimal(len(decisions))
            findings.append(
                f"{reviewer} decided {len(fast)} of {len(decisions)} items ({share:.0%}) in "
                f"under {capacity.minimum_seconds_on_task}s, which is not enough time to read "
                f"a crop. This is a signal about the queue's design before it is a signal "
                f"about the reviewer"
            )

        report.append(
            ReviewerIntegrity(
                reviewer=reviewer,
                decisions=len(decisions),
                agreement_rate=rate,
                unexamined=len(fast),
                median_seconds=seconds[len(seconds) // 2],
                findings=tuple(findings),
            )
        )
    return tuple(report)
