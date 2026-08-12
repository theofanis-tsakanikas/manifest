"""Mapping a multilingual model's structured proposal into fields with real provenance.

Tier 2 of the cascade. Written against the documented `Converse` response shape
([Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html),
read 2026-08-10); its fixtures are authored from that schema and marked as authored.

This module has a different shape from the other two adapters, and the difference is the whole
argument for it.

**A model does not read a page. It proposes values.** The other adapters map a reader's output
into pages, lines and words, because that is what a reader emits. A model emits an answer. So
this returns `Proposal` objects — a field, a value, and where that value was found *by somebody
else* — rather than a `ReadDocument`.

**It carries no confidence, and it must refuse to carry one it is offered.**

A model will happily return `{"value": "MSCU1234567", "confidence": 0.97}` if the prompt asks
for it, and that number is the single most dangerous artefact this system could accept. It is
not a measured frequency over a labelled distribution; it is a token the model emitted because
the prompt made it likely. Claim 1 derives thresholds from distributions of scores that mean
something, and a self-reported score would enter that derivation looking identical to one that
does. `_refuse_self_reported_confidence` exists so that asking for it is a *loud* failure, not
a silent one — because the failure mode is a future prompt change nobody reviews.

**Its provenance comes from tier 0, not from itself.**

Claim 2 requires a page, a box and a version for every published field, and requires the box to
be checkable against the page. A model asked for coordinates will produce plausible ones. So
the box is never taken from the model: the proposed value is located in the tier-0 reading's
own words, whose boxes were measured by a reader that actually looked at pixels. A value the
model proposes that cannot be found in the tier-0 words gets **no provenance and therefore
cannot publish** — doctrine rule 7, the door with no key.

That is a real constraint and it bites: it means tier 2 cannot rescue a value that tier 0 did
not see at all. What it *can* do is choose correctly among what tier 0 saw — which field a
token belongs to, which of three dates is the shipment date, whether a Greek abbreviation is a
port or a party. That is the work, and it is the work the routing contract sends it.

**So both upper tiers are unscored, and the consequence is declared rather than discovered.**
The per-page OCR service reports confidence; the document-automation service does not; a model
does not. Escalating a page is therefore a decision to spend a human on it. `contracts/cascade/`
says so where the tiers are declared, and `evals/review/` counts the cost against capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manifest.core.document import ReadDocument
from manifest.core.geometry import Box
from manifest.core.text import DEFAULT_RULES, Rule, normalise

#: Keys a model might return alongside a value that look like a score and are not one. Matched
#: case-insensitively. The list is deliberately broad: the point is to fail on the *attempt*,
#: and a near-miss spelling that slipped through would be worse than no check at all.
_SCORE_LIKE: frozenset[str] = frozenset(
    {
        "confidence",
        "confidence_score",
        "score",
        "certainty",
        "probability",
        "likelihood",
        "p",
        "logprob",
        "log_prob",
        "self_confidence",
    }
)


#: How a proposed value is matched against the page when *locating* it.
#:
#: Deliberately looser than the rules a field's contract declares for *comparing* two values,
#: because they answer different questions. Comparing asks "are these the same value?", and the
#: answer decides whether two documents disagree — so it is declared per field, in a contract,
#: where a reviewer can see it. Locating asks "is this string printed here?", where a model
#: writing `MSCU 1234567` and a page printing `MSCU1234567` is the same ink.
#:
#: `CASE` **and** `DIACRITICS` are both here, and it takes both. Greek drops its accents in
#: upper case, so a page printing `ΠΕΙΡΑΙΑΣ` and a model returning `Πειραιάς` are the same port
#: — and case-folding alone does not reconcile them, because `ΠΕΙΡΑΙΑΣ`.casefold() is
#: `πειραιασ` while `Πειραιάς`.casefold() keeps its `ά`. Without both rules the value fails to
#: locate, arrives with no provenance and is queued: a correct reading turned into review
#: volume, which is the expensive kind of wrong.
#:
#: `ALPHANUMERIC` is deliberately *not* here. It would match a party name to a different party
#: name that differs only in punctuation, and a wrong box is worse than no box: no box abstains
#: loudly, a wrong box publishes with provenance that points at somebody else's value.
LOCATING_RULES: tuple[Rule, ...] = (*DEFAULT_RULES, Rule.CASE, Rule.DIACRITICS, Rule.SEPARATORS)


class ResponseError(ValueError):
    """A response that does not match the documented schema, or that carries a forbidden field."""


@dataclass(frozen=True, slots=True)
class Proposal:
    """One field the model proposed, and the provenance somebody else measured for it.

    `confidence` is absent by construction — there is no field for it, so there is nowhere for
    one to be quietly added later without this class changing and a reviewer seeing it.
    """

    field: str
    value: str
    #: `None` where the value could not be located in the tier-0 reading. Such a proposal
    #: cannot publish and cannot be approved into existence; it may only be superseded by a
    #: value a human located themselves, which is a new value with their provenance.
    page: int | None
    box: Box | None
    reason: str

    @property
    def has_provenance(self) -> bool:
        return self.box is not None


def proposals(
    *,
    response: dict[str, Any],
    grounding: ReadDocument,
) -> tuple[Proposal, ...]:
    """The model's proposal, grounded in the tier-0 reading's geometry.

    `grounding` is required, not optional. A signature that allowed it to be omitted would
    allow a caller to obtain proposals with no provenance at all and publish them, and the
    whole of claim 2 would rest on every call site remembering to pass an argument.
    """
    proposed = _proposed_object(response)
    return tuple(_proposal(field, value, grounding) for field, value in sorted(proposed.items()))


#: The name of the tool the request forces the model to call. It appears here and in
#: `handlers/escalate.py`, and the two must agree; a mismatch is a reply this refuses by name
#: rather than a value quietly missing.
TOOL_NAME = "propose_fields"


def _proposed_object(response: dict[str, Any]) -> dict[str, Any]:
    """The proposal, from the tool the request obliged the model to call.

    **Read from a `toolUse` block rather than parsed out of prose, and that is the fix for a
    real failure rather than a preference.** The first Greek page to reach this tier came back
    with a reply whose first character was not `{`, and this module refused it — correctly, and
    with a good reason: *"a partial parse of a truncated reply produces a subset of fields with
    no indication that it is a subset"*. But refusing every reply is not a working tier.

    The instinct is to strip a code fence. That is repair, which this module argues against in
    its own docstring, and it works until the day a model explains itself first. The alternative
    is not to ask politely for JSON — it is to make prose unavailable: `Converse` with a
    `toolConfig` and a forced `toolChoice` returns `toolUse.input` as an object the service has
    already validated against the schema the request declared. There is no text to parse and no
    fence to strip.

    What is kept from before is everything that was about *meaning* rather than syntax: the
    `max_tokens` refusal, because a truncated tool call is as partial as truncated JSON and says
    so no more loudly, and the refusal of a self-reported score, which is enforced per field.
    """
    try:
        content = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise ResponseError("no `output.message.content` in this response") from exc

    if not isinstance(content, list) or not content:
        raise ResponseError("`output.message.content` is empty")

    # Refused on the flag rather than on the shape. A tool call cut off at the token limit can
    # still arrive as a well-formed object with fields missing, and nothing in it says so.
    if response.get("stopReason") == "max_tokens":
        raise ResponseError(
            "the reply stopped at the token limit, so it is a prefix of an answer. A prefix "
            "that happens to parse is the most dangerous shape this can take: fields are "
            "missing and nothing in the value says so"
        )

    uses = [
        block["toolUse"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("toolUse"), dict)
    ]
    if not uses:
        raise ResponseError(
            f"no `toolUse` block in the reply. The request forces `{TOOL_NAME}` with "
            f"`toolChoice`, so a reply without one is the model declining the schema rather "
            f"than answering in another format — and reading its prose instead is how a "
            f"proposal with no structure becomes a field value"
        )
    if len(uses) > 1:
        raise ResponseError(
            f"{len(uses)} tool calls in one reply. The request asks for one; merging several "
            f"would silently pick a winner per field, and which one won would be invisible"
        )

    proposed = uses[0].get("input")
    if not isinstance(proposed, dict):
        raise ResponseError(
            f"`toolUse.input` is a {type(proposed).__name__}, not an object of field proposals"
        )
    # One level of nesting is allowed, because the schema declares `fields` as its property and
    # a model that returns the object bare is answering the same question. Anything deeper is
    # not unwrapped: a guess about which key holds the answer is the repair this avoids.
    inner = proposed.get("fields")
    return inner if isinstance(inner, dict) else proposed


def _proposal(field: str, value: Any, grounding: ReadDocument) -> Proposal:
    if isinstance(value, dict):
        _refuse_self_reported_confidence(field, value)
        value = value.get("value")

    if value is None or not str(value).strip():
        return Proposal(
            field=field,
            value="",
            page=None,
            box=None,
            reason="the model returned no value for this field, which is a fact about the page",
        )

    text = str(value).strip()
    located = _locate(text, grounding)
    if located is None:
        return Proposal(
            field=field,
            value=text,
            page=None,
            box=None,
            reason=(
                f"the model proposed {text!r} and it does not appear in the tier-0 reading of "
                f"this document. The box is not taken from the model, so this value has no "
                f"provenance and cannot publish — a model asked for coordinates returns "
                f"plausible ones, and a plausible box is claim 2 defeated politely"
            ),
        )

    page, box = located
    return Proposal(
        field=field,
        value=text,
        page=page,
        box=box,
        reason=f"located in the tier-0 reading on page {page}",
    )


def _refuse_self_reported_confidence(field: str, value: dict[str, Any]) -> None:
    """A model's own score is refused loudly, at the boundary.

    Not dropped. Dropping it would let a prompt change introduce one, have it silently ignored,
    and leave the next reader of the prompt believing the system uses it. Raising makes the
    prompt change fail the moment it is made, which is the only time anybody is looking.
    """
    offending = sorted(key for key in value if key.strip().lower() in _SCORE_LIKE)
    if offending:
        raise ResponseError(
            f"the proposal for {field!r} carries {offending}, a self-reported score. It is "
            f"refused rather than ignored: it is not a measured frequency over a labelled "
            f"distribution, it is a token the prompt made likely, and it would enter claim 1's "
            f"derivation looking exactly like a score that means something. If a tier-2 "
            f"confidence is wanted, it has to be *derived* from labelled outcomes the same way "
            f"every other threshold in this repository is"
        )


def _locate(value: str, grounding: ReadDocument) -> tuple[int, Box] | None:
    """Where this value appears in a reading that measured its own geometry.

    Matches over the normalised form under `LOCATING_RULES`, because the model returns a value
    as a human would write it and the page prints it as the page prints it. See that constant
    for why locating is looser than comparing, and for the one rule deliberately left out.
    """
    target = normalise(value, LOCATING_RULES)
    if not target:
        return None

    for page in grounding.pages:
        words = list(page.words)
        for start in range(len(words)):
            for end in range(start + 1, min(start + 12, len(words)) + 1):
                span = words[start:end]
                if normalise(" ".join(word.text for word in span), LOCATING_RULES) == target:
                    return page.number, Box.hull([word.box for word in span])
    return None


def unlocated(found: tuple[Proposal, ...]) -> tuple[Proposal, ...]:
    """The proposals with no provenance, for the caller that has to queue them.

    Offered as a function rather than left to each caller's comprehension so that "which of
    these cannot publish" has exactly one answer in this repository.
    """
    return tuple(proposal for proposal in found if proposal.value and not proposal.has_provenance)
