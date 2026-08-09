"""Proposing a tariff heading, and refusing to be confident about a contested one.

**Nothing here publishes.** `hs_code` is `always_review` in its contract, so every proposal
reaches a human. What this module decides is a different question: whether the proposal is
worth *offering*, and how it should be presented to the person who has to decide.

Three decisions, and the second is the one that makes this different from a classifier.

**The reference set is data, and every entry cites its heading.** `contracts/classification/`
carries the headings this system proposes from. A classifier whose classes live in code is a
classifier nobody can audit against the tariff.

**A margin, not a score.** The abstention band is on the *gap between the top two candidates*,
not on the top candidate's absolute score. A description that matches one heading at 0.62 and
nothing else at above 0.30 is a clear proposal; one that matches two headings at 0.61 and 0.60
is the contested case, and its top score is higher. Thresholding the absolute score gets that
exactly backwards — which is how a classifier ends up most confident precisely where
professionals disagree.

**A heading declared contested abstains regardless.** Some pairs are argued in the trade
literature and nothing in a description resolves them. Those are declared in the contract, and
a proposal that lands in one is offered as *two* candidates with the argument for each, rather
than as a winner.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Final

from manifest.core.text import Rule, normalise

_RULES: Final = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE)


class Disposition(StrEnum):
    """What the proposer decided to offer."""

    #: One candidate stands clear of the rest. Offered as a proposal — still to a human.
    PROPOSED = "proposed"
    #: Two or more candidates are within the margin, or the heading is declared contested.
    #: Offered as candidates with no winner.
    CONTESTED = "contested"
    #: Nothing matched well enough to be worth a person's time as a suggestion.
    NO_PROPOSAL = "no_proposal"


@dataclass(frozen=True, slots=True)
class Heading:
    """One tariff heading this system may propose."""

    code: str
    description: str
    #: Headings this one is argued against in practice. Symmetric by convention, and the loader
    #: checks that it actually is — a one-sided contest is a contest that only fires when the
    #: description happens to match the side that declared it.
    contested_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    code: str
    description: str
    score: Decimal


@dataclass(frozen=True, slots=True)
class Proposal:
    """What to show the person who has to classify these goods.

    `explanation` is written for them. A classifier that returns a code and a number makes the
    reviewer reconstruct the reasoning; one that says *why* these two headings are close is the
    difference between a decision and a rubber stamp — ADR-0001's integrity metrics are about
    exactly that difference.
    """

    goods: str
    disposition: Disposition
    candidates: tuple[Candidate, ...]
    margin: Decimal
    explanation: str

    @property
    def publishes(self) -> bool:
        """Always false, and it is a property rather than a decision.

        `hs_code` is `always_review`. This exists so that a caller reaching for "can I publish
        this?" gets an answer with a reason rather than being tempted to compare a score against
        a threshold that does not exist for this field.
        """
        return False


def propose(
    goods: str,
    headings: tuple[Heading, ...],
    minimum_score: Decimal,
    margin: Decimal,
    limit: int = 3,
) -> Proposal:
    """Rank the headings against a goods description.

    Similarity over normalised text — deliberately simple, and the reason is in the module
    docstring: what is being demonstrated is the *gate*, and a more elaborate scorer would make
    the accuracy figure look like a claim about production rather than about a synthetic
    distribution this repository generated.
    """
    scored = sorted(
        (
            Candidate(
                code=heading.code,
                description=heading.description,
                score=Decimal(
                    str(
                        round(
                            SequenceMatcher(
                                None,
                                normalise(goods, _RULES),
                                normalise(heading.description, _RULES),
                            ).ratio(),
                            4,
                        )
                    )
                ),
            )
            for heading in headings
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    top = scored[:limit]

    if not top or top[0].score < minimum_score:
        return Proposal(
            goods=goods,
            disposition=Disposition.NO_PROPOSAL,
            candidates=tuple(top),
            margin=Decimal("0"),
            explanation=(
                f"nothing scored above {minimum_score}. Offering the best of a bad set wastes "
                f"the reviewer's time in the direction that looks helpful"
            ),
        )

    gap = top[0].score - top[1].score if len(top) > 1 else Decimal("1")
    by_code = {heading.code: heading for heading in headings}
    declared_contest = {
        candidate.code
        for candidate in top[:2]
        if candidate.code in by_code
        and any(other.code in by_code[candidate.code].contested_with for other in top[:2])
    }

    if declared_contest:
        return Proposal(
            goods=goods,
            disposition=Disposition.CONTESTED,
            candidates=tuple(top),
            margin=gap,
            explanation=(
                f"{' and '.join(sorted(declared_contest))} are declared contested against each "
                f"other in contracts/classification/. Nothing in a goods description resolves "
                f"them, so both are offered and neither is called a winner"
            ),
        )

    if gap < margin:
        return Proposal(
            goods=goods,
            disposition=Disposition.CONTESTED,
            candidates=tuple(top),
            margin=gap,
            explanation=(
                f"{top[0].code} and {top[1].code} are {gap} apart, inside a margin of {margin}. "
                f"The band is on the **gap**, not on the top score — a description matching two "
                f"headings closely has a *higher* top score than one matching a single heading "
                f"loosely, so thresholding the score alone would be most confident exactly where "
                f"professionals disagree"
            ),
        )

    return Proposal(
        goods=goods,
        disposition=Disposition.PROPOSED,
        candidates=tuple(top),
        margin=gap,
        explanation=(
            f"{top[0].code} at {top[0].score}, clear of the next by {gap}. This is a proposal "
            f"for a human, not a classification: the field is always-review and does not "
            f"publish on any score"
        ),
    )
