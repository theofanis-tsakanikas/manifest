"""Retrieval over the tariff nomenclature, and the gate that refuses an ungrounded proposal.

**This is claim 2's argument, applied to text instead of pixels.**

Claim 2 says a published field must point at a place on a page, and the box is checked against
the page rather than against the record that produced it. A field whose value cannot be located
does not publish, and nobody — including an approver — can approve it into existence, because
there is nothing for the approval to be about.

A tariff classification has the same shape and nobody treats it that way. A model proposes
heading 8481.80 for a goods description, and the question that decides whether the proposal is
worth a reviewer's time is not *how confident is it* but **what in the nomenclature says so**. A
proposal that cannot point at the text it came from is a proposal with no provenance, and the
same rule applies: it does not go forward.

So classification here is a retrieval problem with a grounding gate:

1. Retrieve the nomenclature entries whose text bears on this goods description.
2. Propose from **those entries only** — never from the full heading list, because a proposal
   the retrieval did not surface is one nothing in the context supports.
3. Require the proposed heading's **distinguishing terms** to appear in the retrieved context.
   Not the heading code — a code is a label and matching it proves nothing. The terms that make
   this heading different from its neighbours.
4. A proposal that fails the gate is `UNGROUNDED`, which is a refusal and not a low score.

**Why the distinguishing terms rather than the whole description.** Every heading in a chapter
shares most of its words with its siblings — "of iron or steel", "other", "parts thereof". A
gate that required *any* overlap would pass on the shared vocabulary and never fire. What has to
be present is what makes this heading rather than the one beside it, which is precisely the
question a customs dispute turns on.

**What the retrieval is, and what it is not.** Lexical overlap over normalised text. Not
embeddings, and the reason is the same one `hs.py` gives for its scorer: what is being
demonstrated is the *gate*. A vector store would make the accuracy figure look like a claim
about production, and it would be a claim about a synthetic distribution this repository
generated either way. The gate does not get better or worse with the retriever — it gets
*exercised*, and an ungrounded proposal is refused whichever way the candidate was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from manifest.core.text import Rule, normalise

#: Comparison rules for retrieval. Case and separators fold; diacritics do not, because a
#: nomenclature is a legal text and two terms that differ by an accent are two terms.
_RULES: tuple[Rule, ...] = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE)

#: Shorter than this and a token is an abbreviation, a unit or noise — never the word a tariff
#: dispute turns on. Named rather than inline so the gate's strength is visible in one place.
SHORTEST_MEANINGFUL_TERM = 3

#: Words that carry no discriminating power in a tariff. Every heading in a chapter shares them,
#: so a gate that counted them would pass on vocabulary rather than on meaning.
#:
#: Declared as data rather than computed by frequency: a list derived from *this* nomenclature
#: would shrink as the nomenclature grows and the gate would quietly weaken, which is the shape
#: of a control that agrees with whatever it is given.
STOPWORDS: frozenset[str] = frozenset(
    {
        "of",
        "or",
        "and",
        "the",
        "other",
        "not",
        "for",
        "with",
        "in",
        "to",
        "parts",
        "thereof",
        "articles",
        "products",
        "goods",
        "whether",
        "elsewhere",
        "specified",
        "included",
        "kind",
        "used",
        "similar",
        "prepared",
        "a",
        "an",
        "by",
        "from",
        "than",
        "such",
        "as",
        "at",
        "on",
        "their",
        "its",
        "these",
        "those",
    }
)


class Grounding(StrEnum):
    """Whether the nomenclature supports this proposal."""

    #: The heading's distinguishing terms appear in the retrieved context.
    GROUNDED = "grounded"
    #: They do not. A refusal, not a low score — see the module docstring.
    UNGROUNDED = "ungrounded"
    #: Retrieval returned nothing to be grounded against. Distinct from `UNGROUNDED`, because
    #: the fixes differ: one is a proposal nothing supports, the other is a nomenclature with a
    #: hole in it, and merging them would hide the second behind the first.
    NO_CONTEXT = "no_context"


@dataclass(frozen=True, slots=True)
class Note:
    """One entry of the nomenclature: a heading, its text, and the note that explains it."""

    code: str
    description: str
    note: str

    @property
    def text(self) -> str:
        return f"{self.description} {self.note}"


@dataclass(frozen=True, slots=True)
class Retrieved:
    """What the retriever surfaced, with the score that surfaced it."""

    note: Note
    score: Decimal


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a proposed heading is supported by what was retrieved, and why."""

    code: str
    grounding: Grounding
    #: The distinguishing terms this heading was required to justify itself with.
    required: tuple[str, ...]
    #: Which of them the retrieved context actually carried.
    found: tuple[str, ...]
    reason: str

    @property
    def refuses(self) -> bool:
        return self.grounding is not Grounding.GROUNDED


def distinguishing_terms(code: str, notes: tuple[Note, ...]) -> tuple[str, ...]:
    """The words that make this heading different from every other one here.

    Computed against the rest of the nomenclature rather than against a stopword list alone: a
    term shared with one sibling is still discriminating, a term shared with all of them is not.
    The stopword list handles the vocabulary no tariff can discriminate on; this handles the
    vocabulary *this* nomenclature cannot.
    """
    subject = next((note for note in notes if note.code == code), None)
    if subject is None:
        return ()

    def words(text: str) -> set[str]:
        return {
            word
            for word in normalise(text, _RULES).split()
            if len(word) >= SHORTEST_MEANINGFUL_TERM and word not in STOPWORDS
        }

    mine = words(subject.text)
    everywhere = [words(note.text) for note in notes if note.code != code]
    if not everywhere:
        return tuple(sorted(mine))

    # A term that appears in *every* other entry discriminates nothing.
    universal = set.intersection(*everywhere) if everywhere else set()
    return tuple(sorted(mine - universal))


def _terms(text: str) -> set[str]:
    return {
        word
        for word in normalise(text, _RULES).split()
        if len(word) >= SHORTEST_MEANINGFUL_TERM and word not in STOPWORDS
    }


def retrieve(goods: str, notes: tuple[Note, ...], limit: int = 5) -> tuple[Retrieved, ...]:
    """The nomenclature entries whose text bears on this description, best first.

    **Term overlap weighted by rarity, not sequence similarity.** The first version of this
    compared character sequences with `SequenceMatcher`, and the result was worse than useless in
    a way worth recording: on a twelve-entry nomenclature it retrieved *bed linen* for "extra
    virgin olive oil". Sequence similarity over long strings is dominated by length and by shared
    boilerplate, and every entry in a nomenclature is mostly boilerplate.

    Worse, the harness passed. It counted dispositions and never asked whether the retrieved
    entries had anything to do with the goods, so a retriever returning noise satisfied a gate
    designed to check grounding. A control whose input is nonsense reports on nonsense.

    So: a term shared by every entry carries no information and a term unique to one carries a
    great deal, which is inverse document frequency and is the oldest idea in retrieval. No
    embedding model, for the reason the module docstring gives — what is being demonstrated is
    the gate, and the gate is exercised by whatever surfaced the candidate.
    """
    import math  # noqa: PLC0415 - the only arithmetic this module needs

    documents = [_terms(note.text) for note in notes]
    total = len(documents) or 1
    appearances: dict[str, int] = {}
    for document in documents:
        for term in document:
            appearances[term] = appearances.get(term, 0) + 1

    wanted = _terms(goods)
    scored: list[Retrieved] = []
    for note, document in zip(notes, documents, strict=True):
        shared = wanted & document
        # `log(total / appearances)` is zero for a term in every entry, which is the property
        # that matters: shared boilerplate contributes nothing at all rather than a little.
        weight = sum(math.log(total / appearances[term]) for term in shared)
        ceiling = sum(math.log(total / appearances[term]) for term in wanted if term in appearances)
        scored.append(
            Retrieved(
                note=note,
                score=Decimal(str(round(weight / ceiling, 4))) if ceiling else Decimal("0"),
            )
        )

    scored.sort(key=lambda entry: (-entry.score, entry.note.code))
    # A zero score means not one informative term is shared. Returning it as "retrieved" would
    # put an unrelated entry into the context and let a proposal ground itself against paper
    # about something else entirely.
    return tuple(entry for entry in scored[:limit] if entry.score > 0)


def check(
    *,
    code: str,
    context: tuple[Retrieved, ...],
    notes: tuple[Note, ...],
    minimum_terms: int,
) -> Verdict:
    """Whether the retrieved context supports proposing this heading.

    `minimum_terms` is required and has no default. It is the strength of the gate, it is a
    policy, and a module that chose one would be setting that policy where nobody reviews it —
    the same reason `core.drift.assess` refuses to default its window size.
    """
    if not context:
        return Verdict(
            code=code,
            grounding=Grounding.NO_CONTEXT,
            required=(),
            found=(),
            reason=(
                "retrieval returned nothing, so there is no context to ground against. Reported "
                "apart from `ungrounded` on purpose: this is a hole in the nomenclature, not a "
                "proposal nothing supports, and the two have opposite fixes"
            ),
        )

    required = distinguishing_terms(code, notes)
    if not required:
        return Verdict(
            code=code,
            grounding=Grounding.UNGROUNDED,
            required=(),
            found=(),
            reason=(
                f"heading {code} has no term that distinguishes it from the rest of this "
                f"nomenclature, so nothing retrieved could ever support choosing it over its "
                f"neighbours. That is a finding about the nomenclature and it refuses the "
                f"proposal either way"
            ),
        )

    haystack = normalise(" ".join(entry.note.text for entry in context), _RULES)
    found = tuple(term for term in required if term in haystack)

    if len(found) >= minimum_terms:
        return Verdict(
            code=code,
            grounding=Grounding.GROUNDED,
            required=required,
            found=found,
            reason=(
                f"{len(found)} of {len(required)} distinguishing term(s) for {code} appear in "
                f"the retrieved context: {', '.join(found[:5])}"
            ),
        )

    return Verdict(
        code=code,
        grounding=Grounding.UNGROUNDED,
        required=required,
        found=found,
        reason=(
            f"only {len(found)} of {len(required)} distinguishing term(s) for {code} appear in "
            f"the retrieved context, below the declared minimum of {minimum_terms}. The "
            f"proposal cannot point at the text it came from, so it does not go forward — the "
            f"same rule claim 2 applies to a field whose box cannot be located on the page"
        ),
    )
