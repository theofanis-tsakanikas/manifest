"""The line-item table, and the arithmetic that sees a row which is not there."""

from __future__ import annotations

from decimal import Decimal

from manifest.core.document import Page, Word, build_line
from manifest.core.geometry import Box, PageSize
from manifest.core.lineitems import (
    Table,
    TotalOutcome,
    check_total,
    read_table,
)

SIZE = PageSize(width=2480, height=3508)
ANCHORS = {"description": "Description of goods", "amount": "Amount"}


def _word(text: str, left: float, top: float, confidence: float = 0.95) -> Word:
    return Word(
        text=text, confidence=confidence, box=Box(left=left, top=top, width=0.05, height=0.004)
    )


def _page(number: int, rows: list[tuple[str, str]], header: bool) -> Page:
    lines = []
    top = 0.20
    if header:
        lines.append(
            build_line(
                [
                    _word("Description", 0.08, top),
                    _word("of", 0.15, top),
                    _word("goods", 0.18, top),
                    _word("Amount", 0.74, top),
                ]
            )
        )
        top += 0.02
    for description, amount in rows:
        lines.append(build_line([_word(description, 0.08, top), _word(amount, 0.74, top)]))
        top += 0.01
    return Page(
        number=number, size=SIZE, lines=tuple(lines), language="en", language_confidence=1.0
    )


def test_a_table_is_followed_past_a_page_break_with_no_repeated_header() -> None:
    """The pathology. A reader that needs a header per page stops at the break and reports a
    complete table that is short by every row after it."""
    pages = (
        _page(1, [("Tiles", "1,000.00"), ("Oil", "2,000.00")], header=True),
        _page(2, [("Linen", "3,000.00")], header=False),
    )
    table = read_table(pages, ANCHORS, "amount")
    assert table.found
    assert len(table.rows) == 3
    assert table.pages_read == (1, 2)


def test_a_truncated_table_is_caught_by_its_total() -> None:
    pages = (_page(1, [("Tiles", "1,000.00")], header=True),)
    table = read_table(pages, ANCHORS, "amount")
    result = check_total(table, "3,000.00", Decimal("0.01"))
    assert result.outcome is TotalOutcome.ROWS_MISSING
    assert "dropped row" in result.explanation


def test_a_complete_table_agrees_with_its_total() -> None:
    pages = (_page(1, [("Tiles", "1,000.00"), ("Oil", "2,000.00")], header=True),)
    table = read_table(pages, ANCHORS, "amount")
    assert check_total(table, "3,000.00", Decimal("0.01")).outcome is TotalOutcome.AGREES


def test_a_number_with_a_mangled_separator_is_refused() -> None:
    """What the first version got wrong. `75.812;15` had its semicolon filtered out, parsed as
    75.81215, and became **75.8** — an invoice worth seventy-five thousand read as one worth
    seventy-five, and the check then answered confidently in the wrong direction."""
    pages = (_page(1, [("Tiles", "1,000.00")], header=True),)
    table = read_table(pages, ANCHORS, "amount")
    result = check_total(table, "75.812;15", Decimal("0.01"))
    assert result.outcome is TotalOutcome.NOT_COMPARABLE
    assert "could not be read" in result.explanation


def test_an_unreadable_row_makes_the_sum_a_floor_rather_than_an_error() -> None:
    """Refusing the whole check on one bad cell turned 186 of 252 truncated tables into "not
    comparable" — the dropped-row signal disappeared because one mangled value poisoned the sum
    of eight good ones. A floor is still enough for the direction that matters."""
    pages = (_page(1, [("Tiles", "1,000.00"), ("Oil", "2;000.00")], header=True),)
    table = read_table(pages, ANCHORS, "amount")
    result = check_total(table, "9,000.00", Decimal("0.01"))
    assert result.outcome is TotalOutcome.ROWS_MISSING
    assert result.unreadable_rows == 1
    assert "a floor" in result.explanation


def test_a_surplus_over_a_floor_is_not_attributable() -> None:
    """The other direction does not survive an incomplete sum: a surplus could be a row read
    twice or could be the unreadable rows. Guessing between them is not available."""
    pages = (_page(1, [("Tiles", "1,000.00"), ("Oil", "2;000.00")], header=True),)
    table = read_table(pages, ANCHORS, "amount")
    result = check_total(table, "500.00", Decimal("0.01"))
    assert result.outcome is TotalOutcome.NOT_COMPARABLE


def test_an_empty_table_is_not_an_agreement() -> None:
    empty = Table(rows=(), columns=(), pages_read=(), header_page=None)
    assert check_total(empty, "1,000.00", Decimal("0.01")).outcome is TotalOutcome.NOT_COMPARABLE


def test_a_header_found_by_only_some_of_its_captions_is_no_header() -> None:
    """Reading rows against a partial column map silently drops a field from every row."""
    pages = (_page(1, [("Tiles", "1,000.00")], header=False),)
    assert not read_table(pages, ANCHORS, "amount").found


def test_a_line_with_no_money_is_not_a_row() -> None:
    """A footer, a note and a stray line of address all sit in the description column."""
    pages = (_page(1, [("Tiles", "1,000.00"), ("SHP00001 / 1", "page")], header=True),)
    assert len(read_table(pages, ANCHORS, "amount").rows) == 1
