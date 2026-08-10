"""Comparing two strings that came off a page, without deciding they agree.

Three places need to ask "are these the same value?": provenance verification comparing a
published field against a re-read crop (ADR-0003), reconciliation comparing a field across two
documents (claim 4), and reprocessing comparing an old record against a new one (claim 3).

All three are the same trap. Normalisation is how a comparison stops meaning anything: strip
enough and every string equals every other. So the rules here are **named, separate, and
declared per field in a contract** — never a single `normalise()` that accumulates whatever
made the last failure go away.

The rule that decides the shape of this module: **normalisation is only ever applied at
comparison time, never on ingestion.** `Word.text` holds what the reader emitted. A
representation that normalises on the way in has destroyed the evidence of what was on the
page, which is the one thing claim 2 exists to keep.

One asymmetry worth stating. Unicode normalisation (NFC/NFKC) and case folding are safe on any
field, because two byte sequences that render identically are the same mark on paper. Removing
separators is *not* safe on any field — it is safe on a number and destructive on a name — so
it is opt-in per field and never in the default set.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: Legal-form suffixes that mean the same company. Entity resolution (claim 6) uses this;
#: field comparison does not, because "NORTHBRIDGE BV" and "NORTHBRIDGE" are the same *party*
#: and are not the same *string on a page*, and conflating the two questions is how a
#: reconciliation mismatch gets normalised away.
LEGAL_FORMS: Final[frozenset[str]] = frozenset(
    {
        "bv", "b.v.", "nv", "n.v.", "gmbh", "ag", "sa", "s.a.", "srl", "s.r.l.", "spa", "s.p.a.",
        "ltd", "limited", "plc", "llc", "inc", "corp", "co", "kg", "ohg", "oy", "ab", "as",
        "aps", "sarl", "s.a.r.l.", "sas", "bvba", "cv", "vof", "ae", "epe", "ike", "oe", "ee",
    }
)  # fmt: skip

#: Characters an OCR reader confuses for one another, as *directed* observations rather than a
#: symmetric map. Used to explain a disagreement, never to resolve one: if a published `0` and
#: a re-read `O` differ, the answer is that they differ and a human looks. Folding them
#: together would make claim 2 unable to see the single most common OCR error there is.
KNOWN_CONFUSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("0", "O"), ("O", "0"), ("1", "l"), ("l", "1"), ("1", "I"), ("I", "1"),
    ("5", "S"), ("S", "5"), ("8", "B"), ("B", "8"), ("2", "Z"), ("Z", "2"),
    ("6", "G"), ("G", "6"), ("€", "E"), ("E", "€"), ("¥", "Y"), ("Y", "¥"),
    ("rn", "m"), ("m", "rn"), ("cl", "d"), ("d", "cl"),
)  # fmt: skip

_WHITESPACE: Final = re.compile(r"\s+")
# The typographic apostrophe sits in this set beside the ASCII one on purpose: it is
# what a reader returns from a typeset page, and a rule that removed only the ASCII
# form would leave the two spellings of a name as two different parties.
_SEPARATORS: Final = re.compile(r"[\s .,'’_-]+")  # noqa: RUF001
_NOT_ALNUM: Final = re.compile(r"[^0-9A-Za-z]+")


class Rule(StrEnum):
    """One named normalisation. A field's contract declares which apply, in this order.

    Each is a decision about what "the same value" means for that field, and each is a place a
    real disagreement can be hidden — which is why they are declared data rather than a call
    somebody added while fixing a test.
    """

    #: Canonical Unicode composition. Always safe: two encodings of one rendered character.
    UNICODE = "unicode"
    #: Collapse runs of whitespace to one space, and trim. Always safe on a page.
    WHITESPACE = "whitespace"
    #: Case-fold. Safe on a code, a country, a port. Not applied to free text by default.
    CASE = "case"
    #: Drop separators — spaces, dots, commas, apostrophes, hyphens. Safe on an identifier
    #: (`MSKU 123456 7` is `MSKU1234567`); destructive on a name (`O'Brien` becomes `OBrien`
    #: and `Smith-Jones` becomes `SmithJones`, which are different parties).
    SEPARATORS = "separators"
    #: Keep only letters and digits. The strongest and the most dangerous; for identifiers only.
    ALPHANUMERIC = "alphanumeric"
    #: Strip a trailing legal form. **Entity resolution only.** Never on a field comparison.
    LEGAL_FORM = "legal_form"
    #: Drop combining marks — accents, diaereses, cedillas.
    #:
    #: Greek is why this exists and Greek is why it is not in `DEFAULT_RULES`. Greek drops its
    #: accents in upper case as a matter of orthography, so a page printing `ΠΕΙΡΑΙΑΣ` and a
    #: person writing `Πειραιάς` are recording the same port, and case-folding alone does not
    #: reconcile them: `ΠΕΙΡΑΙΑΣ`.casefold() is `πειραιασ` while `Πειραιάς`.casefold() keeps
    #: its `ά`. Any system reading Greek documents that does not handle this has a systematic
    #: mismatch on every capitalised proper noun on every page.
    #:
    #: Destructive where a mark distinguishes two words rather than two renderings of one —
    #: French `pêcheur` and `pécheur` are a fisherman and a sinner. So it is opt-in, declared
    #: per field, and it belongs on ports, countries and codes rather than on free text.
    DIACRITICS = "diacritics"


#: What a field gets when its contract says nothing. Deliberately the two rules that cannot
#: change which value a string denotes.
DEFAULT_RULES: Final[tuple[Rule, ...]] = (Rule.UNICODE, Rule.WHITESPACE)


def normalise(value: str, rules: tuple[Rule, ...] | list[Rule] = DEFAULT_RULES) -> str:
    """Apply the named rules, in the order given, and return the result.

    Order is the caller's, not this function's, because it is observable: case-folding before
    dropping separators and after it give the same answer, but stripping a legal form after
    dropping separators cannot work at all — the suffix boundary is gone.
    """
    result = value
    for rule in rules:
        result = _APPLY[rule](result)
    return result


def _unicode(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _whitespace(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _case(value: str) -> str:
    # `casefold`, not `lower`. Greek final sigma is the reason: `ΟΔΥΣΣΕΥΣ`.lower() is
    # `οδυσσευσ` while the naturally written `Οδυσσεύς`.lower() ends in `ς`, and the two
    # would not compare equal. `casefold` maps both to `σ`. On a corpus with Greek documents
    # this is not a curiosity; it is a reconciliation false positive per affected party.
    return value.casefold()


def _separators(value: str) -> str:
    return _SEPARATORS.sub("", value)


def _alphanumeric(value: str) -> str:
    return _NOT_ALNUM.sub("", value)


def _diacritics(value: str) -> str:
    """Decompose, drop the combining marks, recompose.

    NFD first so that a precomposed `ά` becomes `α` + the combining accent and the accent can
    be seen; NFC at the end so the result is in the same canonical form as every other rule's
    output and two paths through this module cannot produce byte-different equal strings.
    """
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return unicodedata.normalize("NFC", stripped)


def _legal_form(value: str) -> str:
    """Strip one trailing legal-form token. One, not all — see the test.

    `NORTHBRIDGE CO LTD` loses `LTD` and keeps `CO`, because stripping greedily turns
    `SHIPPING CO` into `SHIPPING` and `ATHENS AE EPE` into `ATHENS`, and those are different
    companies from the ones that were written down.
    """
    tokens = value.split()
    if len(tokens) > 1 and tokens[-1].casefold().strip(",") in LEGAL_FORMS:
        return " ".join(tokens[:-1])
    return value


_APPLY: Final[dict[Rule, object]] = {
    Rule.UNICODE: _unicode,
    Rule.WHITESPACE: _whitespace,
    Rule.CASE: _case,
    Rule.SEPARATORS: _separators,
    Rule.ALPHANUMERIC: _alphanumeric,
    Rule.LEGAL_FORM: _legal_form,
    Rule.DIACRITICS: _diacritics,
}


@dataclass(frozen=True, slots=True)
class Comparison:
    """Whether two strings agree under a declared rule set, and why they do not.

    `explanation` exists because a provenance or reconciliation failure that says only "these
    differ" produces a review-queue item a human has to reconstruct from scratch. Naming the
    likely cause — a known character confusion, a length difference, a difference only in
    separators the field's rules did not drop — is the difference between a twenty-second
    decision and a two-minute one, and ADR-0001 counts those seconds.

    It never *resolves* anything. `agree` is decided by equality after the declared rules, and
    the explanation is text for a person.
    """

    left: str
    right: str
    rules: tuple[Rule, ...]
    agree: bool
    explanation: str


def compare(left: str, right: str, rules: tuple[Rule, ...] = DEFAULT_RULES) -> Comparison:
    normalised_left = normalise(left, rules)
    normalised_right = normalise(right, rules)
    agree = normalised_left == normalised_right
    return Comparison(
        left=left,
        right=right,
        rules=tuple(rules),
        agree=agree,
        explanation="" if agree else _explain(normalised_left, normalised_right),
    )


def _explain(left: str, right: str) -> str:
    if not left or not right:
        return "one side is empty after normalisation"
    if len(left) != len(right):
        return f"different lengths ({len(left)} against {len(right)})"

    confusions = [
        f"{a!r}→{b!r} at {index}"
        for index, (a, b) in enumerate(zip(left, right, strict=True))
        if a != b and (a, b) in KNOWN_CONFUSIONS
    ]
    others = [
        f"{a!r}→{b!r} at {index}"
        for index, (a, b) in enumerate(zip(left, right, strict=True))
        if a != b and (a, b) not in KNOWN_CONFUSIONS
    ]
    parts = []
    if confusions:
        parts.append(f"known reader confusions: {', '.join(confusions)}")
    if others:
        parts.append(f"other differences: {', '.join(others[:5])}")
    return "; ".join(parts) or "the strings differ"


def looks_like_a_reader_confusion(left: str, right: str) -> bool:
    """Every differing position is a documented character confusion.

    Reported, never acted on. A field where this is true is a field where the two sides are
    *probably* the same value read twice — and "probably" is exactly the state that goes to a
    human rather than being resolved by the system. Its use is in the review queue's ordering:
    a difference that looks like a reader confusion is a fast decision and should be offered
    when a reviewer has four seconds, not when they have four minutes.
    """
    if len(left) != len(right) or left == right:
        return False
    return all(a == b or (a, b) in KNOWN_CONFUSIONS for a, b in zip(left, right, strict=True))
