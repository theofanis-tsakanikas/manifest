"""The normalised representation — the contract with the cloud, asserted."""

from __future__ import annotations

import pytest

from manifest.core.document import (
    DocumentError,
    Line,
    Page,
    ReadDocument,
    ReaderIdentity,
    Word,
    build_line,
    digest_bytes,
    empty_page,
    merge_readings,
    word_at,
)
from manifest.core.geometry import Box, PageSize

SIZE = PageSize(width=2480, height=3508)
READER = ReaderIdentity(name="reference", version="5.5.2")


def word(text: str, left: float, top: float, confidence: float = 0.9) -> Word:
    return Word(
        text=text,
        confidence=confidence,
        box=Box(left=left, top=top, width=0.05, height=0.012),
    )


def document(pages: list[Page], reader: ReaderIdentity = READER) -> ReadDocument:
    return ReadDocument(
        source_id="bol-0001", source_digest="abc123", reader=reader, pages=tuple(pages)
    )


def test_a_confidence_outside_zero_to_one_is_refused() -> None:
    """The adapter that forgot to divide by its reader's scale.

    It has to land somewhere, and it has to land loudly: 87.0 is above every threshold there
    is, so a representation that accepted it would publish everything.
    """
    with pytest.raises(DocumentError, match="forgot to divide"):
        Word(text="ROTTERDAM", confidence=87.0, box=Box(0.1, 0.1, 0.1, 0.01))


def test_a_reader_identity_carries_its_version() -> None:
    with pytest.raises(DocumentError, match="recalibrated"):
        ReaderIdentity(name="reference", version="")


def test_a_line_box_is_the_hull_of_its_words_not_what_a_reader_claimed() -> None:
    line = build_line(
        [word("PORT", 0.10, 0.20), word("OF", 0.16, 0.20), word("LOADING", 0.19, 0.20)]
    )
    assert all(line.box.contains(w.box) for w in line.words)


def test_a_line_is_only_as_confident_as_its_weakest_word() -> None:
    """Not the mean. Averaging is how a misread digit hides behind nine confident ones — and
    the misread digit is usually the amount."""
    line = build_line(
        [word("1", 0.1, 0.2, 0.99), word("2", 0.16, 0.2, 0.31), word("5", 0.2, 0.2, 0.98)]
    )
    assert line.confidence == pytest.approx(0.31)


def test_a_language_and_its_confidence_arrive_together() -> None:
    with pytest.raises(DocumentError, match="together or not at all"):
        Page(number=1, size=SIZE, lines=(), language="ell")


def test_pages_are_resolved_by_number_not_by_position() -> None:
    """ADR-0003's third fixture: the coordinates are perfect and the page index is off by one.

    A reading that starts at page 3 of a split document has `pages[0].number == 3`. Positional
    indexing would return a different page and raise nothing, which is the shape of a
    provenance failure that no test notices.
    """
    reading = document([empty_page(3, SIZE), empty_page(4, SIZE)])
    assert reading.page(3) is reading.pages[0]
    with pytest.raises(DocumentError, match="not in this reading"):
        reading.page(1)


def test_pages_out_of_order_or_repeated_are_refused() -> None:
    with pytest.raises(DocumentError, match="each appears once"):
        document([empty_page(2, SIZE), empty_page(1, SIZE)])
    with pytest.raises(DocumentError, match="each appears once"):
        document([empty_page(1, SIZE), empty_page(1, SIZE)])


def test_a_page_the_reader_found_nothing_on_is_representable() -> None:
    """A blank reverse side is a real outcome. A reading that silently omits it renumbers
    every page after it, and every provenance record with them."""
    reading = document([empty_page(1, SIZE), empty_page(2, SIZE)])
    assert [page.number for page in reading.pages] == [1, 2]


class TestFingerprint:
    """Claim 3, reduced to a comparison."""

    def _reading(self, text: str = "MSKU", confidence: float = 0.9) -> ReadDocument:
        page = Page(number=1, size=SIZE, lines=(build_line([word(text, 0.1, 0.2, confidence)]),))
        return document([page])

    def test_the_same_reading_fingerprints_identically(self) -> None:
        assert self._reading().fingerprint() == self._reading().fingerprint()

    def test_a_changed_confidence_is_a_different_reading(self) -> None:
        """Even with every character the same. Every threshold downstream was derived from
        those numbers, so a reading whose confidences moved is not the same evidence."""
        assert self._reading().fingerprint() != self._reading(confidence=0.91).fingerprint()

    def test_a_different_reader_version_is_a_different_reading(self) -> None:
        one = self._reading()
        other = ReadDocument(
            source_id=one.source_id,
            source_digest=one.source_digest,
            reader=ReaderIdentity(name="reference", version="5.5.3"),
            pages=one.pages,
        )
        assert one.fingerprint() != other.fingerprint()

    def test_unicode_composition_does_not_change_a_fingerprint(self) -> None:
        """A composed and a decomposed Greek accent are the same mark on the page.

        Without normalisation, a Greek document's fingerprint would depend on which library
        assembled the string — and claim 3 would fail on a machine that assembled it the other
        way, for no reason a reader could ever find.
        """
        # Escapes rather than literal Greek, so the two sides are visibly the *same*
        # word in two encodings: U+038C is one composed character, U+039F U+0301 is
        # the same mark built from a letter and a combining accent.
        composed = self._reading("\u03a0\u0395\u0399\u03a1\u0391\u0399\u038c\u03a3")
        decomposed = self._reading("\u03a0\u0395\u0399\u03a1\u0391\u0399\u039f\u0301\u03a3")
        assert composed.fingerprint() == decomposed.fingerprint()


def test_merging_two_readings_names_both_readers() -> None:
    """What a cascade produces. A record made by two readers is attributable to neither, and
    pretending otherwise makes claim 3's diff lie about what changed."""
    cheap = document([empty_page(1, SIZE), empty_page(2, SIZE)])
    better = ReadDocument(
        source_id=cheap.source_id,
        source_digest=cheap.source_digest,
        reader=ReaderIdentity(name="escalated", version="2026-08"),
        pages=(Page(number=2, size=SIZE, lines=(build_line([word("RECOVERED", 0.1, 0.3)]),)),),
    )
    merged = merge_readings(cheap, better)
    assert merged.reader.name == "reference+escalated"
    assert merged.page(2).text == "RECOVERED"
    assert merged.page(1).text == ""


def test_merging_readings_of_different_sources_is_refused() -> None:
    one = document([empty_page(1, SIZE)])
    other = ReadDocument(
        source_id="other", source_digest="different", reader=READER, pages=(empty_page(1, SIZE),)
    )
    with pytest.raises(DocumentError, match="never existed"):
        merge_readings(one, other)


def test_word_at_scores_against_each_words_own_area() -> None:
    """A field box is the hull of several words, so a single word's IoU against it is low by
    construction. Scoring by IoU would reject exactly the case this is for."""
    page = Page(
        number=1,
        size=SIZE,
        lines=(build_line([word("GROSS", 0.10, 0.20), word("WEIGHT", 0.16, 0.20)]),),
    )
    field_box = Box(left=0.10, top=0.198, width=0.11, height=0.016)
    assert [w.text for w in word_at(page, field_box)] == ["GROSS", "WEIGHT"]


def test_word_at_ignores_a_word_that_merely_grazes_the_box() -> None:
    page = Page(number=1, size=SIZE, lines=(build_line([word("ELSEWHERE", 0.80, 0.20)]),))
    assert word_at(page, Box(left=0.10, top=0.20, width=0.05, height=0.012)) == ()


def test_a_source_digest_is_computed_one_way() -> None:
    """Two ways of computing it is two documents, and the duplicate is found by a reprocessing
    job that does the work twice — claim 7's failure from a direction nobody watches."""
    assert digest_bytes(b"page bytes") == digest_bytes(b"page bytes")
    assert digest_bytes(b"page bytes") != digest_bytes(b"other bytes")


def test_a_reading_with_no_pages_read_nothing() -> None:
    with pytest.raises(DocumentError, match="read nothing"):
        document([])


def test_a_line_with_no_words_is_refused() -> None:
    with pytest.raises(DocumentError, match="not a line"):
        Line(words=(), confidence=0.9)
