"""A reader that reports no confidence must not look like a confident reader.

One of the managed readers this system routes to publishes text, reading order and geometry per
word and no confidence field anywhere. The representation therefore has to carry the *absence*
of a score as its own state, and every one of these tests is a way that absence could quietly
become a number instead.

The dangerous direction is always the same: `None` becoming `1.0`. A zero would abstain, get
noticed and get fixed within a day. A one clears every derived threshold in the repository and
publishes silently, which is precisely the failure this project exists to argue against.
"""

from __future__ import annotations

import pytest

from manifest.core.document import (
    DocumentError,
    Page,
    ReadDocument,
    ReaderIdentity,
    Word,
    build_line,
    weakest,
)
from manifest.core.fields import extract
from manifest.core.geometry import Box, PageSize
from manifest.core.review import Reason, reason_for


def _word(text: str, confidence: float | None, left: float = 0.1, top: float = 0.1) -> Word:
    return Word(
        text=text, confidence=confidence, box=Box(left=left, top=top, width=0.1, height=0.05)
    )


class TestAbsenceDominates:
    def test_one_unscored_word_makes_the_group_unscored(self) -> None:
        assert weakest([0.9, 0.95, None, 0.99]) is None

    def test_a_fully_scored_group_reduces_to_its_weakest(self) -> None:
        assert weakest([0.9, 0.95, 0.4, 0.99]) == pytest.approx(0.4)

    def test_the_unscored_member_is_not_skipped_in_favour_of_the_rest(self) -> None:
        """The bug this test exists for is `min(c for c in group if c is not None)`.

        It is the obvious way to write it, it type-checks, and it publishes a value one of
        whose tokens nothing vouched for on the strength of the tokens that were vouched for.
        """
        assert weakest([0.99, None]) is not pytest.approx(0.99)
        assert weakest([0.99, None]) is None

    def test_an_empty_group_is_refused_rather_than_reduced(self) -> None:
        with pytest.raises(DocumentError):
            weakest([])


class TestTheRepresentationKeepsTheDistinction:
    def test_a_word_may_carry_no_confidence(self) -> None:
        assert _word("MSCU", None).confidence is None

    def test_a_word_still_may_not_carry_a_percentage(self) -> None:
        """Optional does not mean unchecked. 87.0 must still land here."""
        with pytest.raises(DocumentError, match="not a fraction"):
            _word("MSCU", 87.0)

    def test_a_line_of_unscored_words_is_unscored(self) -> None:
        line = build_line([_word("MSCU", None), _word("1234567", None, left=0.3)])
        assert line.confidence is None

    def test_an_unscored_reading_does_not_fingerprint_as_a_scored_one(self) -> None:
        """Claim 3 in the presence of absence.

        If the digest substituted any number for `None`, a reading that reports no confidence
        would be indistinguishable from one that reports that number — and a re-extraction that
        silently changed reader would produce an identical fingerprint and no diff.
        """

        def document(confidence: float | None) -> ReadDocument:
            return ReadDocument(
                source_id="doc-1",
                source_digest="a" * 64,
                reader=ReaderIdentity(name="r", version="1"),
                pages=(
                    Page(
                        number=1,
                        size=PageSize(width=1000, height=1400),
                        lines=(build_line([_word("MSCU", confidence)]),),
                    ),
                ),
            )

        unscored = document(None).fingerprint()
        assert unscored != document(1.0).fingerprint()
        assert unscored != document(0.0).fingerprint()
        assert unscored == document(None).fingerprint()


class TestUnscoredNeverPublishes:
    @pytest.mark.parametrize("threshold", [None, 0.0, 0.5, 0.99])
    def test_no_threshold_admits_an_unscored_value(self, threshold: float | None) -> None:
        """Including 0.0, which every real number clears."""
        assert reason_for(None, threshold) is Reason.UNSCORED

    def test_unscored_is_reported_apart_from_below_threshold(self) -> None:
        """They have different fixes, so merging them hides one behind the other.

        A queue of `below_threshold` is a calibration or capture problem. A queue of `unscored`
        is a *routing* problem — a reader that reports nothing is handling volume it should not
        be handling — and it would be invisible if it were counted as the first.
        """
        assert reason_for(None, 0.9) is not reason_for(0.1, 0.9)

    def test_a_scored_value_above_its_threshold_needs_no_human(self) -> None:
        assert reason_for(0.95, 0.9) is None


class TestExtractionCarriesTheAbsenceThrough:
    def test_a_field_read_by_an_unscored_reader_is_unscored(self) -> None:
        page = Page(
            number=1,
            size=PageSize(width=1000, height=1400),
            lines=(
                build_line([_word("Container", None, left=0.1, top=0.10)]),
                build_line([_word("MSCU1234567", None, left=0.1, top=0.20)]),
            ),
        )
        found = extract(page, field="container_number", anchor="Container")
        assert found.found
        assert found.confidence is None

    def test_one_unscored_token_taints_a_field_the_rest_of_which_was_scored(self) -> None:
        page = Page(
            number=1,
            size=PageSize(width=1000, height=1400),
            lines=(
                build_line([_word("Container", 0.99, left=0.1, top=0.10)]),
                build_line(
                    [
                        _word("MSCU", 0.99, left=0.10, top=0.20),
                        _word("1234567", None, left=0.25, top=0.20),
                    ]
                ),
            ),
        )
        found = extract(page, field="container_number", anchor="Container")
        assert found.found
        assert found.confidence is None
