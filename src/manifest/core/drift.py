"""The operating envelope, applied to what is arriving rather than to what was generated.

`corpus/envelope.yaml` declares the confidence distribution and abstention band this system's
numbers are only meaningful inside, and a test turns the build red when the **generator** leaves
it. That test exists because a corpus degraded too gently makes every threshold trivially
satisfiable and a corpus degraded too hard makes everything abstain — and both report green.

The same argument applies with more force to production, where nothing was checking at all.

**A document-extraction system does not fail with an error.** It fails when the new scanning
supplier ships 150 dpi instead of 300, or a customer starts sending phone photographs, or a
counterparty changes its invoice template. The reader keeps returning text. The confidences
drift down. The abstention rate doubles. Every threshold is still met, every gate still passes,
every dashboard is still green — and the queue fills with work nobody planned for, which
doctrine rule 1 says is a failure of the system rather than of the reviewers.

**The thresholds are the reason this matters more here than elsewhere.** Every one of them was
derived from a distribution recorded at one moment. A threshold is a statement of the form *"at
this score, on documents like the ones we measured, the published-and-wrong rate fits the
budget"*. When the documents stop being like the ones that were measured, the threshold does not
become wrong loudly. It becomes **unsupported**, silently, and continues to publish.

So this module answers one question: *does what is arriving still resemble what the thresholds
were derived from?* It reports a **finding**, never an adjustment. Nothing here moves a
threshold, and nothing here relaxes a band — an envelope that widened to accommodate the traffic
would be a control agreeing with whatever happened, which is no control at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    """Whether the arriving distribution still resembles the recorded one."""

    INSIDE = "inside"
    #: Outside the declared band. A finding to act on, never an adjustment to make here.
    DRIFTED = "drifted"
    #: Too few observations to say. **Not** `INSIDE` — the distinction is the point: a window
    #: with nine documents in it has no opinion, and reporting one as "inside" is how a quiet
    #: Sunday reads as evidence that nothing changed.
    UNDECIDED = "undecided"


@dataclass(frozen=True, slots=True)
class Band:
    """The declared acceptable range for one measure, from `corpus/envelope.yaml`."""

    name: str
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if not self.lower <= self.upper:
            raise ValueError(f"band {self.name!r} has its bounds the wrong way round")

    def holds(self, value: float) -> bool:
        return self.lower <= value <= self.upper


@dataclass(frozen=True, slots=True)
class Window:
    """What arrived, summarised. The input to a drift decision and nothing more.

    Deliberately small. A richer summary — per-field rates, per-supplier splits — invites this
    module to decide *why* the traffic changed, and why is an operator's question. What a
    threshold needs to know is whether its own supporting distribution still describes reality.
    """

    documents: int
    #: Every word confidence the reader reported in this window. Unscored readings contribute
    #: nothing here and are counted separately: a reader that reports no score cannot drift, and
    #: averaging it in as a zero would manufacture drift out of a routing change.
    confidences: tuple[float, ...]
    unscored_documents: int
    abstained_fields: int
    total_fields: int

    @property
    def abstention_rate(self) -> float:
        return self.abstained_fields / self.total_fields if self.total_fields else 0.0

    @property
    def median_confidence(self) -> float:
        if not self.confidences:
            return 0.0
        ordered = sorted(self.confidences)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2


@dataclass(frozen=True, slots=True)
class Finding:
    """One measure, its value, and whether the declared band still holds."""

    measure: str
    verdict: Verdict
    observed: float
    band: Band
    documents: int
    reason: str

    @property
    def drifted(self) -> bool:
        return self.verdict is Verdict.DRIFTED


def assess(
    *,
    window: Window,
    confidence_band: Band,
    abstention_band: Band,
    minimum_documents: int,
) -> tuple[Finding, ...]:
    """Compare an arriving window against the declared envelope.

    `minimum_documents` is required and has no default. Below it the answer is `UNDECIDED`, and
    the absence of a default is deliberate: the number is a property of the traffic — a broker
    handling four hundred documents a day and one handling four need different windows — and a
    module that chose one would be setting an operational policy in a place nobody reviews.
    """
    findings: list[Finding] = []

    if window.documents < minimum_documents:
        undecided = (
            f"{window.documents} document(s) in this window, below the declared minimum of "
            f"{minimum_documents}. No verdict: a window this small has no opinion, and calling "
            f"it 'inside' would make a quiet day read as evidence that nothing changed"
        )
        return (
            Finding(
                measure="median_confidence",
                verdict=Verdict.UNDECIDED,
                observed=window.median_confidence,
                band=confidence_band,
                documents=window.documents,
                reason=undecided,
            ),
            Finding(
                measure="abstention_rate",
                verdict=Verdict.UNDECIDED,
                observed=window.abstention_rate,
                band=abstention_band,
                documents=window.documents,
                reason=undecided,
            ),
        )

    median = window.median_confidence
    inside = confidence_band.holds(median)
    findings.append(
        Finding(
            measure="median_confidence",
            verdict=Verdict.INSIDE if inside else Verdict.DRIFTED,
            observed=median,
            band=confidence_band,
            documents=window.documents,
            reason=(
                f"median word confidence {median:.3f} is inside the declared "
                f"[{confidence_band.lower:.3f}, {confidence_band.upper:.3f}]"
                if inside
                else (
                    f"median word confidence {median:.3f} is outside the declared "
                    f"[{confidence_band.lower:.3f}, {confidence_band.upper:.3f}]. Every "
                    f"threshold in this system was derived from a distribution that no longer "
                    f"describes what is arriving — they are not wrong, they are unsupported, "
                    f"and they will go on publishing"
                )
            ),
        )
    )

    rate = window.abstention_rate
    inside = abstention_band.holds(rate)
    findings.append(
        Finding(
            measure="abstention_rate",
            verdict=Verdict.INSIDE if inside else Verdict.DRIFTED,
            observed=rate,
            band=abstention_band,
            documents=window.documents,
            reason=(
                f"abstention rate {rate:.1%} is inside the declared "
                f"[{abstention_band.lower:.1%}, {abstention_band.upper:.1%}]"
                if inside
                else (
                    f"abstention rate {rate:.1%} is outside the declared "
                    f"[{abstention_band.lower:.1%}, {abstention_band.upper:.1%}]. Above the "
                    f"band this is queue volume nobody planned for; below it, it is a reader "
                    f"suddenly confident about pages it was not confident about before, which "
                    f"is the more alarming direction and the one nobody watches"
                )
            ),
        )
    )

    return tuple(findings)
