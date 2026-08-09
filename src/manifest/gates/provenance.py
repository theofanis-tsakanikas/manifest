"""CLAIM 2 — the box is checked against the page, not against the record that produced it.

ADR-0003. Read that first; this module is its implementation and the ADR is where the honesty
lives.

**The claim, stated exactly.** A published field is **where the record says it is**. It is *not*
that the value is **right**. Correctness is claim 1's business — the derived threshold — and the
human loop's. Conflating the two is how a locational check gets sold as a correctness check, and
it is the most likely way this project could mislead a reader.

Three layers of declared and unequal strength:

**A — ink is present where the record says a value is.** Pixels only, no reader. Refuses a blank
crop, a crop whose ink does not fill the recorded box, and a crop that is saturated — a stamp, a
rule line or a black border rather than text. Independent of every recognition engine that
exists, and the weakest in what it can distinguish: it says *something is there*, not *what*.

**B — the crop, re-read through a different recognition path.** The value was produced by a
full-page pass with page segmentation, layout analysis and line finding, resolving each word in
the context of its line. This re-read is single-unit mode on a crop: no page, no layout, no
neighbours. Two genuinely different code paths in one binary, so a disagreement is possible —
which is what makes agreement mean something. **It is independence from the segmentation path,
not from the character classifier.** A `0`/`O` confusion the classifier makes reproduces on both
passes, and the README says so.

**C — arithmetic.** A field that can check itself is checked against itself. The ISO 6346 check
digit refuses; it never confirms.

**What this does not catch, decided in advance and fixtured:** a box on an identical string
elsewhere on the page — every layer passes, because the value genuinely is at those coordinates.
`evals/provenance/` carries that case with an expected result of *not caught*, so the limitation
is measured rather than hoped about.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from manifest.core.checkdigit import check
from manifest.core.geometry import Box, PageSize
from manifest.core.text import Rule, compare, looks_like_a_reader_confusion

#: Margin added to a recorded box before cropping. A crop taken at exactly the reported box cuts
#: the ascenders and descenders a reader used to recognise the word, and re-reading that would
#: make the verifier disagree with a correct record — a gate that manufactures failures is a
#: gate somebody mutes within a week.
CROP_MARGIN: Final = 0.004

#: Ink coverage below this is a blank crop. Measured on the committed corpus: a recorded box
#: carries a median of 19% ink and the same box moved three percent down the page carries about
#: 1.5%, so two percent sits in the gap rather than at either end of it.
INK_FLOOR: Final = 0.02

#: Ink coverage above this is not text. A stamp, a rule line, a black border — something a
#: reader will return characters for and a human would not.
INK_CEILING: Final = 0.72

#: How much of the recorded box the ink's own hull must occupy. A hull taken over the wrong span
#: of words leaves a box far larger than the ink inside it.
FILL_FLOOR: Final = 0.25


class Layer(StrEnum):
    """Which check refused, so the report can say what kind of failure it is."""

    INK = "ink"
    REREAD = "reread"
    ARITHMETIC = "arithmetic"
    PAGE = "page"


class Verdict(StrEnum):
    VERIFIED = "verified"
    REFUSED = "refused"
    #: The record could not be checked at all — no raster, no page. Not a pass: a field whose
    #: provenance nothing could look at has not been verified, and publishing it on the strength
    #: of "we could not check" is the laundering Attestor's reason-code vocabulary exists to
    #: prevent.
    UNCHECKABLE = "uncheckable"


@dataclass(frozen=True, slots=True)
class InkStatistics:
    """What the pixels under a box look like, with no reader involved."""

    coverage: float
    fill: float


class Raster(Protocol):
    """What the gate needs from the outside world, and nothing more.

    A protocol rather than an image type, because `manifest.core` may not import an imaging
    library and this gate lives one level above it. The adapter that opens the page implements
    these two; the gate stays a pure function of what they return.
    """

    def ink(self, page: int, box: Box) -> InkStatistics | None:
        """Ink statistics under `box` on `page`, or None if the page is not available."""

    def reread(self, page: int, box: Box, language: str) -> tuple[str, float]:
        """The crop re-read through the single-unit path: text and its lowest confidence."""

    def size(self, page: int) -> PageSize | None: ...


@dataclass(frozen=True, slots=True)
class Provenance:
    """What a published field claims about where it came from."""

    field: str
    value: str
    page: int
    box: Box
    language: str
    #: The field's declared comparison rules, from its contract. Shared with the extraction
    #: path, and that sharing is stated rather than hidden: it is the one thing the two paths
    #: have in common, and a normalisation is a place a disagreement can be buried.
    comparison: tuple[Rule, ...]
    #: Whether the field's own arithmetic can refuse it — a container number, today.
    self_checking: bool = False


@dataclass(frozen=True, slots=True)
class Check:
    """The result of verifying one published field."""

    provenance: Provenance
    verdict: Verdict
    layer: Layer | None
    reason: str
    reread_value: str | None = None
    ink: InkStatistics | None = None

    @property
    def refuses(self) -> bool:
        return self.verdict is not Verdict.VERIFIED


def verify(provenance: Provenance, raster: Raster) -> Check:
    """Check one published field's provenance against the page.

    The layers run cheapest first, and the first refusal stops the rest — not to save work, but
    because a refusal from a later layer on a crop the earlier one already called blank would be
    a second opinion about nothing.

    One `return` per refusal, which is more than the linter likes. A single exit collecting a
    reason into a variable would make *which layer refused* something a reader reconstructs by
    reading backwards, and the layer is the most useful thing this function produces.
    """
    size = raster.size(provenance.page)
    if size is None:
        return Check(
            provenance=provenance,
            verdict=Verdict.UNCHECKABLE,
            layer=Layer.PAGE,
            reason=(
                f"page {provenance.page} is not available, so this record could not be checked "
                f"at all. That is not a pass: a field whose provenance nothing has looked at "
                f"has not been verified"
            ),
        )

    padded = provenance.box.padded(CROP_MARGIN)

    # ── Layer C first where it applies, because it is free and absolute ──────
    if provenance.self_checking:
        arithmetic = check(provenance.value)
        if arithmetic.refuses:
            return Check(
                provenance=provenance,
                verdict=Verdict.REFUSED,
                layer=Layer.ARITHMETIC,
                reason=f"the value refuses its own arithmetic: {arithmetic.reason}",
            )

    # ── Layer A: is there ink there at all? ──────────────────────────────────
    ink = raster.ink(provenance.page, padded)
    if ink is None:
        return Check(
            provenance=provenance,
            verdict=Verdict.UNCHECKABLE,
            layer=Layer.PAGE,
            reason=f"the raster for page {provenance.page} could not be read",
        )
    refusal = _judge_ink(ink)
    if refusal:
        return Check(
            provenance=provenance,
            verdict=Verdict.REFUSED,
            layer=Layer.INK,
            reason=refusal,
            ink=ink,
        )

    # ── Layer B: does a different recognition path agree? ────────────────────
    text, _ = raster.reread(provenance.page, padded, provenance.language)
    if not text.strip():
        return Check(
            provenance=provenance,
            verdict=Verdict.REFUSED,
            layer=Layer.REREAD,
            reason=(
                "the crop carries ink and the single-unit path read nothing from it. The record "
                "points at a mark that is not the text it claims"
            ),
            ink=ink,
        )

    agreement = compare(provenance.value, text, provenance.comparison)
    if not agreement.agree and not _is_contained(provenance.value, text, provenance.comparison):
        confusion = looks_like_a_reader_confusion(provenance.value, text.strip())
        return Check(
            provenance=provenance,
            verdict=Verdict.REFUSED,
            layer=Layer.REREAD,
            reason=(
                f"the crop re-read as {text.strip()!r} through a different recognition path; "
                f"the record says {provenance.value!r}. {agreement.explanation}"
                + (
                    ". Every differing position is a documented reader confusion, so the two "
                    "are probably the same value read twice — which is exactly the state that "
                    "goes to a human rather than being resolved here"
                    if confusion
                    else ""
                )
            ),
            reread_value=text.strip(),
            ink=ink,
        )

    return Check(
        provenance=provenance,
        verdict=Verdict.VERIFIED,
        layer=None,
        reason=(
            "ink is present where the record says the value is, and a different recognition "
            "path reads the same value from that crop. This says the value is *where the record "
            "says it is*; it does not say the value is right"
        ),
        reread_value=text.strip(),
        ink=ink,
    )


def _judge_ink(ink: InkStatistics) -> str | None:
    if ink.coverage < INK_FLOOR:
        return (
            f"the crop is blank — {ink.coverage:.1%} ink against a floor of {INK_FLOOR:.0%}. "
            f"The record points at a part of the page with nothing on it"
        )
    if ink.coverage > INK_CEILING:
        return (
            f"the crop is saturated — {ink.coverage:.1%} ink against a ceiling of "
            f"{INK_CEILING:.0%}. That is a stamp, a rule or a border rather than text, and a "
            f"reader will happily return characters for it"
        )
    if ink.fill < FILL_FLOOR:
        return (
            f"the ink under this box fills only {ink.fill:.1%} of it, against a floor of "
            f"{FILL_FLOOR:.0%}. The box is far larger than what is written in it, which is a "
            f"hull taken over the wrong span of words"
        )
    return None


def _is_contained(value: str, text: str, rules: tuple[Rule, ...]) -> bool:
    """Whether the published value is the whole of what the crop says, allowing for its unit.

    A value printed as `8959 KGS` is extracted whole and re-read whole, but a padded crop can
    also pick up a neighbouring token. Containment in **one direction only**: the re-read may
    carry more than the record, and never less. The other direction would accept `89` as
    verification of `8959`, which is the single worst thing this function could do.
    """
    from manifest.core.text import normalise  # noqa: PLC0415 — one call, avoids a cycle

    published = normalise(value, rules)
    read = normalise(text, rules)
    return bool(published) and published in read
