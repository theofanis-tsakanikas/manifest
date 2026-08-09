"""The normalised document representation — the contract with the cloud.

Every reader produces this shape and nothing else: pages, lines, words, each with text, a
confidence and a box. The core never learns which reader produced a value, and
`manifest.gates.core_purity` fails if one is named anywhere in this package.

Four decisions live here, and each is a way the abstraction leaks if it is decided later.

**Geometry is fractions of the page** (`core/geometry.py`). A managed service documents its
boxes as ratios of the page with a top-left origin; a local per-word reader reports pixels and
its adapter divides. There is exactly one place a pixel becomes a stored coordinate.

**Confidence is a fraction in `[0, 1]`, converted from the reader's declared scale by its
adapter, and it is *not* comparable between readers.** The conversion is arithmetic — a reader
emitting 0–100 is divided by 100 — and it is not calibration. Two readers' 0.8 are different
events, and turning one into the other is exactly what claim 1 exists to derive rather than
assume. So the representation carries a `ReaderIdentity`, and everything that compares
confidences groups by it.

**`ReaderIdentity` is opaque.** It is a string the core stores, groups by and reproduces, and
never interprets — the same relationship a multi-tenant system has with a tenant id. That is
what lets calibration be per-reader without the core knowing what a reader is. A core that
branched on its value would have learned the engine, and the purity gate would refuse the name
it would have to branch on.

**Nothing is optional that a claim depends on.** A word with no box cannot support claim 2, so
a word has a box. A page with no size cannot turn a box back into a crop, so a page has a size.
The place to be liberal is the adapter, which either produces the required shape or refuses —
loudly, at the boundary, where the reader can be blamed.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from manifest.core.geometry import Box, GeometryError, PageSize

#: A confidence must be a fraction. An adapter handing over 87.0 has forgotten to divide, and
#: the failure is silent and catastrophic — every threshold derived from that reader is
#: nonsense, and 87.0 is above every threshold there is.
_CONFIDENCE_RANGE: Final = (0.0, 1.0)


class DocumentError(ValueError):
    """A normalised document that cannot support the claims made about it."""


class BlockKind(StrEnum):
    """What a block is, in the vocabulary every reader is mapped into.

    Deliberately short. A reader with a richer taxonomy loses detail here, and that loss is the
    abstraction working: a kind that only one reader can produce is a kind the core would have
    to branch on, and branching on it is knowing the reader.
    """

    WORD = "word"
    LINE = "line"


@dataclass(frozen=True, slots=True)
class ReaderIdentity:
    """Which reader produced a document, as an opaque pair of strings.

    The core groups by it and never interprets it. `name` is whatever the adapter declares —
    the core neither validates it against a list nor branches on it, which is what keeps this
    module honest about not knowing what a reader is.

    `version` is part of the identity, not metadata beside it. A reader upgrade is a different
    reader for every purpose in this system: its calibration is different, its thresholds are
    different, and a record produced by one is not interchangeable with a record produced by
    the other. Claim 3 is the same statement from the other side — same document, same reader
    identity, identical record.
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise DocumentError(
                "a reader identity needs both a name and a version; a record whose reader "
                "cannot be named is a record nothing can be recalibrated against"
            )

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class Word:
    """One token, where it is, and how sure the reader was.

    The atom of claim 2. `text` is stored exactly as the reader emitted it — normalisation for
    comparison happens in `core.text`, against a rule declared in a contract, and never on the
    way in. A representation that normalises on ingestion has thrown away the evidence of what
    was actually on the page, which is the one thing it exists to keep.
    """

    text: str
    confidence: float
    box: Box

    def __post_init__(self) -> None:
        if not self.text:
            raise DocumentError("a word with no text is not a word; readers emit gaps as gaps")
        _check_confidence(self.confidence, f"word {self.text!r}")


@dataclass(frozen=True, slots=True)
class Line:
    """A run of words the reader grouped together.

    The line's own box is the hull of its words rather than whatever the reader reported for
    it. Readers disagree with themselves here — a line box that does not contain its own words
    is common enough that trusting it would put a hole in claim 2 that no fixture would find,
    because the words would still verify individually.
    """

    words: tuple[Word, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not self.words:
            raise DocumentError("a line with no words is not a line")
        _check_confidence(self.confidence, "line")

    @property
    def box(self) -> Box:
        return Box.hull([word.box for word in self.words])

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True, slots=True)
class Page:
    """One page: its raster size, its lines, and what language the reader thinks it is in.

    `language` is a value with a confidence like any other, because ADR-0004 routes on it. A
    page whose language is uncertain routes by the conservative rule rather than the likely
    one, and that decision needs the confidence to exist in the representation rather than
    being reconstructed later from a guess.
    """

    number: int
    size: PageSize
    lines: tuple[Line, ...]
    language: str | None = None
    language_confidence: float | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise DocumentError(f"pages are numbered from 1, not {self.number}")
        if (self.language is None) != (self.language_confidence is None):
            raise DocumentError(
                "a language and its confidence arrive together or not at all; a language with "
                "no confidence cannot be routed on, and a confidence with no language is noise"
            )
        if self.language_confidence is not None:
            _check_confidence(self.language_confidence, f"page {self.number} language")

    @property
    def words(self) -> Iterator[Word]:
        for line in self.lines:
            yield from line.words

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True, slots=True)
class ReadDocument:
    """A document as one reader saw it.

    Named for what it is: not *the* document, but one reading of it. A second reader produces
    a second `ReadDocument` for the same source, and claim 3's diff is between two of these.
    """

    source_id: str
    source_digest: str
    reader: ReaderIdentity
    pages: tuple[Page, ...]

    def __post_init__(self) -> None:
        if not self.source_id:
            raise DocumentError("a reading with no source cannot be traced to a document")
        if not self.source_digest:
            raise DocumentError(
                "a reading with no source digest cannot prove it read the document it names"
            )
        if not self.pages:
            raise DocumentError("a reading with no pages read nothing")
        numbers = [page.number for page in self.pages]
        if numbers != sorted(numbers) or len(set(numbers)) != len(numbers):
            raise DocumentError(
                f"pages are in order and each appears once; got {numbers}. An off-by-one page "
                f"index is the provenance failure ADR-0003 plants a fixture for"
            )

    def page(self, number: int) -> Page:
        """The page with this number, resolved from the record.

        Positional indexing would be wrong here and would look right: a reading that starts at
        page 3 of a split document has `pages[0].number == 3`, and `pages[number - 1]` would
        return a different page while raising nothing. ADR-0003's third fixture is exactly this
        case, and this method is what it asserts against.
        """
        for page in self.pages:
            if page.number == number:
                return page
        raise DocumentError(
            f"page {number} is not in this reading (it has {[p.number for p in self.pages]})"
        )

    @property
    def words(self) -> Iterator[Word]:
        for page in self.pages:
            yield from page.words

    def fingerprint(self) -> str:
        """A digest over everything a claim depends on, derived from content alone.

        No clock and no counter — `core/` cannot read either — so the same source read by the
        same reader identity fingerprints identically, on any machine, in any year. That is
        claim 3 reduced to a comparison, and it is why `ReaderIdentity` carries a version.

        Text, confidence and geometry are all in the digest. A reading whose confidences moved
        is a different reading even if every character is the same, because every threshold
        downstream was derived from those numbers.
        """
        digest = hashlib.sha256()
        digest.update(self.source_digest.encode("utf-8"))
        digest.update(str(self.reader).encode("utf-8"))
        for page in self.pages:
            digest.update(f"\x1fpage:{page.number}:{page.size.width}x{page.size.height}".encode())
            digest.update(f"\x1flang:{page.language}:{page.language_confidence}".encode())
            for word in page.words:
                # Unicode normalisation before hashing, because two byte sequences that render
                # identically must not fingerprint differently. A composed and a decomposed
                # `ά` are the same character on the page and would otherwise make a Greek
                # document's fingerprint depend on which library assembled the string.
                text = unicodedata.normalize("NFC", word.text)
                box = word.box
                digest.update(
                    f"\x1f{text}\x1e{word.confidence:.6f}\x1e"
                    f"{box.left:.6f},{box.top:.6f},{box.width:.6f},{box.height:.6f}".encode()
                )
        return digest.hexdigest()


def _check_confidence(value: float, what: str) -> None:
    low, high = _CONFIDENCE_RANGE
    if not isinstance(value, int | float) or not (low <= value <= high):
        raise DocumentError(
            f"confidence for {what} is {value!r}, which is not a fraction in [0, 1]. An "
            f"adapter that forgot to divide by its reader's scale lands here — and it has to "
            f"land somewhere, because 87.0 is above every threshold there is"
        )


def build_line(words: tuple[Word, ...] | list[Word]) -> Line:
    """A line whose confidence is the *lowest* of its words.

    Not the mean. A line is only as trustworthy as its weakest token, and averaging is how a
    misread digit hides behind nine confident ones — which is the failure mode that matters,
    because the misread digit is usually the amount.

    Provided as a helper rather than as `Line`'s default so that an adapter whose reader
    reports a real line-level confidence can pass it instead, and so the choice is visible at
    the call site rather than buried in a constructor.
    """
    listed = tuple(words)
    if not listed:
        raise DocumentError("a line with no words is not a line")
    return Line(words=listed, confidence=min(word.confidence for word in listed))


def merge_readings(primary: ReadDocument, secondary: ReadDocument) -> ReadDocument:
    """Two readings of one source, page by page, with the secondary taking the pages it has.

    This is what a cascade produces: most pages from the cheap reader, some re-read by a
    better one. The result is a reading whose `reader` names **both**, in order, because a
    record produced by two readers is not attributable to either and pretending otherwise
    would make claim 3's diff lie about what changed.
    """
    if primary.source_digest != secondary.source_digest:
        raise DocumentError(
            "these are readings of different sources; merging them would produce a document "
            "that never existed"
        )
    replaced = {page.number: page for page in secondary.pages}
    pages = tuple(replaced.get(page.number, page) for page in primary.pages)
    unknown = set(replaced) - {page.number for page in primary.pages}
    if unknown:
        raise DocumentError(f"the second reading has pages the first does not: {sorted(unknown)}")
    return ReadDocument(
        source_id=primary.source_id,
        source_digest=primary.source_digest,
        reader=ReaderIdentity(
            name=f"{primary.reader.name}+{secondary.reader.name}",
            version=f"{primary.reader.version}+{secondary.reader.version}",
        ),
        pages=pages,
    )


def word_at(page: Page, box: Box, minimum_overlap: float = 0.5) -> tuple[Word, ...]:
    """Every word whose box overlaps `box` by at least `minimum_overlap` of its own area.

    Used by provenance verification to ask what the reading says is at a location, without
    asking the extractor what it put there. Scored against each word's **own** area rather
    than IoU on purpose: a field's box is the hull of several words, so a single word's IoU
    against it is low by construction, and IoU would reject exactly the case this is for.
    """
    if not 0 < minimum_overlap <= 1:
        raise GeometryError(
            f"an overlap threshold outside (0, 1] selects nothing or everything: {minimum_overlap}"
        )
    found = []
    for word in page.words:
        overlap = word.box.intersection(box)
        if overlap is not None and overlap.area / word.box.area >= minimum_overlap:
            found.append(word)
    return tuple(found)


def read_document_from_pages(
    source_id: str,
    source_digest: str,
    reader: ReaderIdentity,
    pages: list[Page],
) -> ReadDocument:
    """The constructor adapters use, so the tuple conversion happens in one place."""
    return ReadDocument(
        source_id=source_id,
        source_digest=source_digest,
        reader=reader,
        pages=tuple(pages),
    )


def digest_bytes(payload: bytes) -> str:
    """The source digest, so every adapter computes it the same way.

    A source digest computed two ways is two documents, and the duplicate is discovered by a
    reprocessing job that does the work twice — claim 7's failure, arriving from a direction
    nobody watches.
    """
    return hashlib.sha256(payload).hexdigest()


def empty_page(number: int, size: PageSize, language: str | None = None) -> Page:
    """A page the reader found nothing on.

    A real outcome, not an error: a blank reverse side, or a page so degraded that nothing
    survives. It has to be representable, because a reading that silently omits it changes the
    page numbering of everything after it — and every provenance record downstream with it.
    """
    return Page(
        number=number,
        size=size,
        lines=(),
        language=language,
        language_confidence=0.0 if language is not None else None,
    )


def with_pages(document: ReadDocument, pages: list[Page]) -> ReadDocument:
    """A copy of `document` with different pages, for adapters that rebuild page by page."""
    return ReadDocument(
        source_id=document.source_id,
        source_digest=document.source_digest,
        reader=document.reader,
        pages=tuple(pages),
    )
