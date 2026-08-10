"""Routing a page to the cheapest reader that can meet its fields' error budgets.

ADR-0004. The cascade is not a quality ladder. `docs/AWS-CONSTRAINTS.md`, verified 2026-08-09:
the managed readers document the same six input languages and Greek and Dutch are outside all
of them. So this is routing between engines with **different competences**, and the eligibility
is declared per language in `contracts/cascade/routing.yaml`.

**A page whose language has no eligible higher tier abstains rather than escalating.** Sending a
Greek page to a service that does not read Greek does not fail loudly — it returns a
confident-looking result over a language the model never saw, and that score then enters a
threshold derived on the assumption that it means something. Failing to route is a bug; routing
to an ineligible reader is a fabricated result.

**Escalation is on confidence, never on a preprocessing rejection.** Both managed readers share
their preprocessing limits — the same 15-pixel character floor, the same 10,000-pixel ceiling,
the same in-plane rotation support — so a page one rejects for any of those, the other rejects
for the same reason. Escalating there is a second bill for the same answer.

**What this can prove, and where it stops.** That the routing rule sends the low-confidence
pages up, and that the pages it *keeps* at tier 0 meet their fields' budgets. It cannot say
what a higher tier would have read, because no higher tier is ever called. Any sentence of the
form *"accuracy held at X for Y% of the cost"* is unavailable in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Route(StrEnum):
    KEEP = "keep"
    ESCALATE = "escalate"
    #: No eligible higher tier for this page's language. The page goes to a human, and the
    #: reason is recorded — it is a coverage fact, not a reader failure.
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class Decision:
    page: str
    language: str
    tier: int
    route: Route
    to_tier: int | None
    reason: str


def route(
    *,
    page: str,
    language: str,
    confidence: float | None,
    threshold: float | None,
    eligible: tuple[int, ...],
    current_tier: int = 0,
) -> Decision:
    """Where this page goes next.

    `threshold` is `None` for a field the derivation declared always-review; such a page is
    never *kept*, because there is no score at which it may publish. Treating `None` as "no
    threshold to clear" and keeping the page is the mistake that would make an always-review
    field publish, and it is a one-character difference from the correct behaviour.

    `confidence` is `None` where the current tier's reader reports none. Such a page is never
    kept either, and for a sharper reason: there is no evidence that it *should* be. A reader
    that returns text without a score has not said the reading is good; it has said nothing,
    and nothing does not clear a threshold.
    """
    higher = tuple(tier for tier in eligible if tier > current_tier)

    if threshold is not None and confidence is not None and confidence >= threshold:
        return Decision(
            page=page,
            language=language,
            tier=current_tier,
            route=Route.KEEP,
            to_tier=None,
            reason=(
                f"read at {confidence:.3f}, at or above the derived threshold of "
                f"{threshold:.3f}; this page does not need a more expensive reader"
            ),
        )

    if not higher:
        return Decision(
            page=page,
            language=language,
            tier=current_tier,
            route=Route.ABSTAIN,
            to_tier=None,
            reason=(
                f"no tier above {current_tier} reads {language!r} (contracts/cascade/"
                f"routing.yaml). This page goes to a human. Sending it to a reader that does "
                f"not have the language would return a confident-looking result over text the "
                f"model never saw"
            ),
        )

    # Formatted once, here, because `None` has no `.3f` and a bare f-string would raise on the
    # exact input this branch exists to handle.
    read = "read with no confidence reported" if confidence is None else f"read at {confidence:.3f}"

    return Decision(
        page=page,
        language=language,
        tier=current_tier,
        route=Route.ESCALATE,
        to_tier=min(higher),
        reason=(
            read
            + (
                f", below the derived threshold of {threshold:.3f}"
                if threshold is not None and confidence is not None
                else (
                    " on a field with no derivable threshold"
                    if confidence is not None
                    else "; a reader that returns text without a score has not said the "
                    "reading is good, it has said nothing"
                )
            )
            + f"; escalating to tier {min(higher)}"
        ),
    )


def distribution(decisions: list[Decision] | tuple[Decision, ...]) -> dict[int, int]:
    """How many pages each tier would read.

    The **measured** half of claim 7's cost model. A page kept at tier 0 is read once; a page
    escalated is read at tier 0 *and* at its target, because the cheap read happened before the
    decision to escalate could be made. Counting only the target would understate the modelled
    cost, which is the direction a cost model must never be wrong in.
    """
    counts: dict[int, int] = {}
    for decision in decisions:
        counts[decision.tier] = counts.get(decision.tier, 0) + 1
        if decision.route is Route.ESCALATE and decision.to_tier is not None:
            counts[decision.to_tier] = counts.get(decision.to_tier, 0) + 1
    return counts
