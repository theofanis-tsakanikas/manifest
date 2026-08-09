"""Finding a declared field's value on a page, from the caption it is printed under.

The reader returns words and lines. A *field* is a business fact, and the step between the two
is layout logic — which is ours to build, because the tier-0 reader does not do it and because
this is exactly the logic claims 1 and 2 are about (ADR-0005).

**Anchor-based, and the anchor comes from the contract.** `contracts/documents/*.yaml` declares
what each field is captioned in each language; this module finds that caption on the page and
takes the value printed beneath it. Real form-like documents work this way, and it degrades in
the right direction: a caption the reader mangled means the field is **not found**, which is an
abstention, not a wrong value.

**Fuzzy on the caption, exact on the value.** A caption is fixed text the system already knows,
so recognising it through OCR damage is a legitimate use of similarity. The value is the thing
being extracted and nothing about it may be repaired — no nearest-match, no correction, no
guess. Doctrine rule 3.

**Confidence is the minimum over the value's words.** A field is only as trustworthy as its
weakest token, and averaging is how a misread digit hides behind four confident ones. The digit
is usually the amount.

**A field that is not found is `None`, and `None` is a fact.** Missing is missing (doctrine rule
3), it is reported as an abstention rather than an error, and `core.calibration` excludes it
from the error denominator so a reader that abstains never scores worse than one that guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from manifest.core.document import Page, Word
from manifest.core.geometry import Box
from manifest.core.text import Rule, normalise

#: How closely a run of words must match a caption to count as that caption. Below this, the
#: field is simply not found — which is the safe outcome, because a wrongly matched caption
#: attaches the wrong value to the field and nothing downstream can tell.
ANCHOR_SIMILARITY: Final = 0.72

#: How far below a caption its value may sit, as a multiple of the caption's own height. The
#: layout prints a value about one line under its caption; three line-heights covers a skewed
#: page and stops well short of the next form box.
VALUE_GAP: Final = 3.2

#: How far the value may start to the left of its caption, as a fraction of the page. Values
#: are printed flush with their caption, and a small allowance covers skew. Widening this is
#: how a field starts picking up the column beside it.
LEFT_ALLOWANCE: Final = 0.012

#: How far right of the caption the value may extend, as a fraction of the page. Wide enough
#: for a party name, narrow enough not to reach the next column of a two-column form.
VALUE_WIDTH: Final = 0.34

#: Normalisation applied to *captions* when matching. Aggressive on purpose: the caption is
#: known text and the only question is whether the reader produced something close to it.
_ANCHOR_RULES: Final = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE, Rule.SEPARATORS)


@dataclass(frozen=True, slots=True)
class Extracted:
    """One field, as found on a page — or the recorded fact that it was not found.

    `box` is the hull of the words that make up the value, which is what claim 2's provenance
    record carries and what a human is shown a crop of.
    """

    field: str
    value: str | None
    confidence: float
    page: int | None
    box: Box | None
    anchor_similarity: float
    reason: str

    @property
    def found(self) -> bool:
        return self.value is not None


def extract(page: Page, field: str, anchor: str) -> Extracted:
    """Find `field` on `page`, given the caption it is printed under."""
    located, similarity = _find_anchor(page, anchor)
    if located is None:
        return Extracted(
            field=field,
            value=None,
            confidence=0.0,
            page=None,
            box=None,
            anchor_similarity=similarity,
            reason=(
                f"the caption {anchor!r} was not found on page {page.number} "
                f"(best similarity {similarity:.2f}). The field is missing, which is a fact "
                f"about this page and not an error"
            ),
        )

    words = _words_below(page, located)
    if not words:
        return Extracted(
            field=field,
            value=None,
            confidence=0.0,
            page=page.number,
            box=None,
            anchor_similarity=similarity,
            reason=(
                f"the caption {anchor!r} is on page {page.number} and there is nothing legible "
                f"under it. On a stamped or obliterated field this is the correct answer"
            ),
        )

    return Extracted(
        field=field,
        value=" ".join(word.text for word in words),
        # The minimum, never the mean. A field is as trustworthy as its weakest token.
        confidence=min(word.confidence for word in words),
        page=page.number,
        box=Box.hull([word.box for word in words]),
        anchor_similarity=similarity,
        reason=f"found under {anchor!r} on page {page.number}",
    )


def extract_from_pages(pages: tuple[Page, ...], field: str, anchor: str) -> Extracted:
    """The first page the field is found on.

    First rather than best: a form's fields appear once, and a "best match across pages" rule
    would silently prefer a continuation page's table header to the field itself.
    """
    best = Extracted(
        field=field,
        value=None,
        confidence=0.0,
        page=None,
        box=None,
        anchor_similarity=0.0,
        reason=f"the caption {anchor!r} was not found on any page",
    )
    for page in pages:
        found = extract(page, field, anchor)
        if found.found:
            return found
        if found.anchor_similarity > best.anchor_similarity:
            best = found
    return best


def _find_anchor(page: Page, anchor: str) -> tuple[Box | None, float]:
    """The box of the best run of words matching `anchor`, and how well it matched.

    Runs, not lines: a caption is one to three words and the reader may have grouped it with
    the value beside it. Scanning windows of the right length inside each line finds the
    caption whether or not the reader drew the line boundary where the form did.
    """
    wanted = normalise(anchor, _ANCHOR_RULES)
    if not wanted:
        return None, 0.0

    expected_words = max(1, len(anchor.split()))
    best_score = 0.0
    best_box: Box | None = None

    for line in page.lines:
        words = line.words
        for start in range(len(words)):
            for length in range(1, min(expected_words + 1, len(words) - start) + 1):
                run = words[start : start + length]
                candidate = normalise(" ".join(word.text for word in run), _ANCHOR_RULES)
                if not candidate:
                    continue
                score = SequenceMatcher(None, wanted, candidate).ratio()
                if score > best_score:
                    best_score = score
                    best_box = Box.hull([word.box for word in run])

    if best_score < ANCHOR_SIMILARITY:
        return None, best_score
    return best_box, best_score


def _words_below(page: Page, anchor: Box) -> tuple[Word, ...]:
    """The words on the line printed under `anchor`, in reading order.

    One line, not everything in the band: a form box holds a caption and a value, and taking
    the whole band would swallow the next caption down whenever the layout is tight. The line
    is chosen by taking the topmost qualifying word and keeping everything within half a
    caption-height of it — which is how a skewed line stays together and how the line after it
    stays out.
    """
    band_top = anchor.bottom
    band_bottom = anchor.bottom + anchor.height * VALUE_GAP
    left = anchor.left - LEFT_ALLOWANCE
    right = anchor.left + VALUE_WIDTH

    candidates = [
        word
        for word in page.words
        if band_top <= word.box.top + word.box.height / 2 <= band_bottom
        and word.box.left >= left
        and word.box.left <= right
    ]
    if not candidates:
        return ()

    first = min(candidates, key=lambda word: word.box.top)
    tolerance = max(anchor.height, first.box.height) * 0.6
    on_the_line = [word for word in candidates if abs(word.box.top - first.box.top) <= tolerance]
    return tuple(sorted(on_the_line, key=lambda word: word.box.left))
