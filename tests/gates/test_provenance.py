"""Claim 2's gate, against a stub page.

The gate is a pure function of what a raster returns, which is the whole reason it is written
that way: these run in milliseconds, with no image, no binary and no corpus, and they assert the
behaviour that `evals/provenance/` then measures on real pages. A gate whose only test needed
778 rendered pages would be a gate nobody ran while changing it.

The stub is deliberately dumb. It returns whatever the test told it to, so each test states one
situation and one expected refusal — and the *layer* is asserted, not just the refusal, because
the right answer for the wrong reason is not evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from manifest.core.geometry import Box, PageSize
from manifest.core.text import Rule
from manifest.gates.provenance import (
    FILL_FLOOR,
    INK_CEILING,
    INK_FLOOR,
    InkStatistics,
    Layer,
    Provenance,
    Verdict,
    verify,
)

RULES = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE)


@dataclass
class StubRaster:
    """A page that returns exactly what a test asked it to."""

    ink_result: InkStatistics | None = field(
        default_factory=lambda: InkStatistics(coverage=0.20, fill=0.60)
    )
    reread_text: str = "ROTTERDAM"
    page_size: PageSize | None = field(default_factory=lambda: PageSize(width=2480, height=3508))
    missing_pages: tuple[int, ...] = ()

    def size(self, page: int) -> PageSize | None:
        return None if page in self.missing_pages else self.page_size

    def ink(self, page: int, box: Box) -> InkStatistics | None:
        return None if page in self.missing_pages else self.ink_result

    def reread(self, page: int, box: Box, language: str) -> tuple[str, float]:
        return self.reread_text, 0.9


def _record(value: str = "ROTTERDAM", self_checking: bool = False) -> Provenance:
    return Provenance(
        field="port_of_discharge",
        value=value,
        page=1,
        box=Box(left=0.10, top=0.20, width=0.09, height=0.014),
        language="en",
        comparison=RULES,
        self_checking=self_checking,
    )


@pytest.mark.gate
def test_an_honest_record_verifies() -> None:
    check = verify(_record(), StubRaster())
    assert check.verdict is Verdict.VERIFIED
    # The wording is the control: a reader who takes this as "the value is right" has been
    # misled by the gate rather than by the README.
    assert "does not say the value is right" in check.reason


@pytest.mark.gate
def test_a_blank_crop_is_refused_by_the_ink_layer() -> None:
    """Fixture 1. The easy one, and the one a naive implementation passes by accident —
    there is nothing there to re-read either, so the wrong layer would also refuse."""
    check = verify(_record(), StubRaster(ink_result=InkStatistics(coverage=0.001, fill=0.0)))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.INK
    assert "blank" in check.reason


@pytest.mark.gate
def test_a_saturated_crop_is_refused_by_the_ink_layer() -> None:
    """A stamp, a rule or a border. A reader will happily return characters for it."""
    check = verify(_record(), StubRaster(ink_result=InkStatistics(coverage=0.95, fill=0.99)))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.INK
    assert "saturated" in check.reason


@pytest.mark.gate
def test_a_box_far_larger_than_its_ink_is_refused() -> None:
    """A hull taken over the wrong span of words. Coverage alone would pass it — the value is
    in there — and a human shown that crop would be reading a whole line to check one field."""
    check = verify(_record(), StubRaster(ink_result=InkStatistics(coverage=0.05, fill=0.08)))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.INK
    assert "far larger" in check.reason


@pytest.mark.gate
def test_ink_present_but_the_wrong_ink_is_refused_by_the_reread_layer() -> None:
    """Fixture 2, and the case that decides whether Layer B was worth building.

    Layer A passes — there is ink, and it fills the box. Only a second recognition path can say
    that the ink is not the value the record claims.
    """
    check = verify(_record(value="ROTTERDAM"), StubRaster(reread_text="PIRAEUS"))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.REREAD


@pytest.mark.gate
def test_ink_with_nothing_legible_in_it_is_refused() -> None:
    check = verify(_record(), StubRaster(reread_text="   "))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.REREAD
    assert "read nothing" in check.reason


@pytest.mark.gate
def test_a_reader_confusion_is_named_rather_than_resolved() -> None:
    """`ROTTERDAM` against `R0TTERDAM` is one documented confusion in one position.

    It is still a refusal. Folding the two together would make the gate unable to see the most
    common OCR error there is — but the reason says what it looks like, because ADR-0001 counts
    the seconds a reviewer spends working that out for themselves.
    """
    check = verify(_record(value="ROTTERDAM"), StubRaster(reread_text="R0TTERDAM"))
    assert check.verdict is Verdict.REFUSED
    assert "reader confusion" in check.reason


@pytest.mark.gate
def test_a_reread_carrying_more_than_the_record_still_verifies() -> None:
    """`8959` recorded, `8959 KGS` re-read from a padded crop. The unit came with the padding."""
    check = verify(_record(value="8959"), StubRaster(reread_text="8959 KGS"))
    assert check.verdict is Verdict.VERIFIED


@pytest.mark.gate
def test_a_reread_carrying_less_than_the_record_is_refused() -> None:
    """The single worst thing containment could do: accept `89` as verification of `8959`.

    One direction only. A crop may show more than the record; it may never show less.
    """
    check = verify(_record(value="8959"), StubRaster(reread_text="89"))
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.REREAD


@pytest.mark.gate
def test_a_self_checking_field_is_refused_by_its_own_arithmetic_first() -> None:
    """Layer C, and it runs before the pixels are touched: it is free and it is absolute.

    The container number below fails its ISO 6346 digit, so the record is refused whatever the
    page says — including on a page where the crop would have re-read identically, which is the
    case where the reader was consistently wrong.
    """
    check = verify(
        _record(value="CSQU3054384", self_checking=True),
        StubRaster(reread_text="CSQU3054384"),
    )
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.ARITHMETIC
    assert "provably wrong" in check.reason


@pytest.mark.gate
def test_a_passing_check_digit_does_not_excuse_the_other_layers() -> None:
    """A passing check digit proves nothing — about one corruption in eleven passes it. A gate
    that stopped there would have turned a falsifier into a confirmation."""
    check = verify(
        _record(value="CSQU3054383", self_checking=True),
        StubRaster(ink_result=InkStatistics(coverage=0.001, fill=0.0)),
    )
    assert check.verdict is Verdict.REFUSED
    assert check.layer is Layer.INK


@pytest.mark.gate
def test_a_page_that_cannot_be_read_is_uncheckable_rather_than_verified() -> None:
    """Fixture 3's mechanism, and Attestor's laundering rule applied here.

    A field whose provenance nothing has looked at has not been verified. Reporting it as
    verified because the check could not run is how "we could not check" becomes "it was fine".
    """
    check = verify(_record(), StubRaster(missing_pages=(1,)))
    assert check.verdict is Verdict.UNCHECKABLE
    assert check.layer is Layer.PAGE
    assert check.refuses


@pytest.mark.gate
def test_the_thresholds_sit_between_the_two_populations_the_corpus_produces() -> None:
    """Measured on the committed corpus: a recorded box carries a median of about 19% ink and
    the same box three percent down the page carries about 1.5%. The floor has to sit in that
    gap rather than at either end of it."""
    assert 0.015 < INK_FLOOR < 0.10
    assert 0.5 < INK_CEILING < 0.9
    assert 0.1 < FILL_FLOOR < 0.5
