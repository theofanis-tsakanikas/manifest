"""A document that declares a line-item table, through `publish`.

**The test that was not here, and the defect it would have caught in two seconds.** The
line-total check was wired into `publish` and named `TotalOutcome.DISAGREES`, a member that does
not exist — the enum has `ROWS_MISSING` and `ROWS_SURPLUS`, because the two directions mean
different things. Every offline test passed. The first two-page commercial invoice through the
deployed estate failed with `AttributeError`, after the reading and after the extraction, inside
the step that exists to protect it.

Nothing in the suite put a table-bearing document through this handler, so the whole branch was
unexecuted code that looked covered. That is the gap these tests close, and the first one is
deliberately the dullest: *the branch runs at all*.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from manifest.core.geometry import Box
from manifest.core.lineitems import Column, Row, Table, TotalOutcome, check_total
from manifest.core.review import Reason
from manifest.handlers.publish import Outcome, _check_the_line_total


def test_the_two_directions_of_disagreement_are_both_named() -> None:
    """**The exact defect.** `DISAGREES` was written; neither the enum nor the estate had it.

    Asserted as membership rather than by calling anything, because the failure was an attribute
    that did not exist — and a test that catches it has to name the members the handler names.
    """
    assert {"ROWS_MISSING", "ROWS_SURPLUS", "AGREES", "NOT_COMPARABLE"} == {
        member.name for member in TotalOutcome
    }
    assert not hasattr(TotalOutcome, "DISAGREES")


def _table(*values: str) -> Table:
    """A table of `n` rows, each carrying one line value.

    `__value__` is the key `Row.line_value` reads — the line-value column is addressed by role
    rather than by whatever the contract happened to name it, so a table declared with a column
    called `amount` and one called `bedrag` sum the same way.
    """
    columns = (
        Column(
            name="line_value",
            is_line_value=True,
            left=0.5,
            box=Box(0.5, 0.1, 0.2, 0.02),
            similarity=1.0,
        ),
        Column(
            name="description",
            is_line_value=False,
            left=0.1,
            box=Box(0.1, 0.1, 0.3, 0.02),
            similarity=1.0,
        ),
    )
    rows = tuple(
        Row(
            page=1,
            cells={"__value__": value, "description": "goods"},
            confidence=0.9,
            box=Box(0.1, 0.2 + index * 0.02, 0.6, 0.018),
        )
        for index, value in enumerate(values)
    )
    return Table(rows=rows, columns=columns, pages_read=(1,), header_page=1)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("300.00", TotalOutcome.AGREES),
        # The document says more was invoiced than its lines account for. The pathology.
        ("450.00", TotalOutcome.ROWS_MISSING),
        # A row read twice.
        ("200.00", TotalOutcome.ROWS_SURPLUS),
        (None, TotalOutcome.NOT_COMPARABLE),
    ],
)
def test_the_printed_total_against_its_own_rows(
    printed: str | None, expected: TotalOutcome
) -> None:
    """Three rows of a hundred against four printed totals, one of them absent."""
    check = check_total(_table("100.00", "100.00", "100.00"), printed, Decimal("0.01"))

    assert check.outcome is expected


def test_a_table_with_no_rows_is_not_an_agreement() -> None:
    """A page nothing was read from and a page whose rows sum correctly are different facts.

    `NOT_COMPARABLE` rather than `AGREES`, because an extractor that read no rows at all would
    otherwise report the strongest possible result on the document it failed hardest at.
    """
    check = check_total(_table(), "450.00", Decimal("0.01"))

    assert check.outcome is TotalOutcome.NOT_COMPARABLE


def test_the_tolerance_is_applied_rather_than_ignored() -> None:
    """A cent, from the contract. Line values are summed exactly; anything beyond rounding is a
    row that is not there."""
    # A cent apart, which is rounding. The first attempt here used a third decimal place in a
    # row value and the sum came out short by a whole row — money is read to two places, and a
    # test that quietly fed it three was testing the parser rather than the tolerance.
    within = check_total(_table("100.00", "100.00"), "200.01", Decimal("0.01"))
    beyond = check_total(_table("100.00", "100.00"), "200.50", Decimal("0.01"))

    assert within.outcome is TotalOutcome.AGREES
    assert beyond.outcome is TotalOutcome.ROWS_MISSING


# ── The handler's own function, which is what actually broke ─────────────────


def test_the_refusal_sets_the_queue_reason_rather_than_the_derived_property() -> None:
    """**The second guessed name, and the second `TypeError` from the estate.**

    `Outcome.publishable` is a property computed from `queued_because` and `value`; the handler
    passed it to `dataclasses.replace` and the two-page invoice failed again, in the same step,
    one line further on.

    The tests above did not catch it because they exercise `core.lineitems.check_total` — the
    neighbour — and the code that broke was the handler's own. That is the lesson worth keeping:
    a test of the thing beside the change is a test that passes while the change is broken.
    """
    published = Outcome(
        field="invoice_total",
        value="$ 81.832,10",
        confidence=0.97,
        page=2,
        box=(0.6, 0.18, 0.1, 0.01),
        reason="read at 0.97",
        queued_because=None,
        threshold=0.9,
    )
    assert published.publishable is True

    refused = replace(
        published,
        queued_because=Reason.LINE_TOTAL_DISAGREES,
        reason="9 rows sum to 78,000.00; the page prints 81,832.10",
    )

    assert refused.publishable is False
    assert refused.queued_because is Reason.LINE_TOTAL_DISAGREES
    # The value is kept. A human sees both numbers — replacing either with the other would
    # smooth a disagreement on one document instead of two.
    assert refused.value == "$ 81.832,10"


def test_a_document_whose_contract_declares_no_table_is_untouched() -> None:
    """Most of them. Reading columns off a bill of lading would be inventing a check."""

    class _NoTable:
        table = None

    outcomes = [
        Outcome(
            field="gross_weight",
            value="8959 KGS",
            confidence=0.97,
            page=1,
            box=(0.1, 0.2, 0.1, 0.01),
            reason="read at 0.97",
            queued_because=None,
            threshold=0.9,
        )
    ]

    same, reported = _check_the_line_total(_NoTable(), object(), "en", outcomes)

    assert same is outcomes
    assert reported is None
