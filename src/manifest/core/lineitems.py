"""Reading a line-item table, and catching the row that was silently lost.

`docs/SCENARIO.md`, pathology 3: a line-item table continuing on page 3 with **no repeated
header** is where naive extraction loses rows — *and the total still looks plausible*, because
the total was printed rather than summed. Nothing about the extracted record looks wrong. The
invoice has a seller, a buyer, a currency, a total and some lines, and it is short by however
much the missing rows were worth.

**The only thing that can see it is arithmetic over the rows against the printed total.** That
is what this module builds, and it is why `commercial_invoice.yaml` declares a `table` with a
`total_field`: a table with no total to check against is a table whose dropped row nothing
notices.

Three decisions that decide whether the check works.

**A continuation page is found by geometry, not by a header.** The header appears once. Rows on
the following page are recognised by falling in the same column positions — which is the only
signal there is, because the document deliberately gives no other. A reader that required a
header per page would read the first page and report a complete table.

**A row is only a row if it has a line value.** A page footer, a note, a stray line of address
all sit in the description column. Requiring the money column is what separates a row from
everything else printed at the same x.

**The check reports a difference, never a correction.** A total that disagrees with its rows is
a finding for a human. Replacing the printed total with the sum — or the sum with the total —
is the smoothing claim 4 is named after, arriving on one document instead of two.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Final

from manifest.core.document import Page, Word
from manifest.core.geometry import Box
from manifest.core.quantity import QuantityError, parse
from manifest.core.quantity import Unit as _Unit
from manifest.core.text import Rule, normalise

#: How closely a run of words must match a column caption. Same reasoning as a field anchor:
#: the caption is known text, and the only question is whether the reader produced something
#: close to it.
HEADER_SIMILARITY: Final = 0.72

#: How far either side of a column's caption a cell may sit, as a fraction of the page. Wide
#: enough for a right-aligned amount under a left-aligned caption, narrow enough not to reach
#: the next column.
COLUMN_TOLERANCE: Final = 0.055

#: The fewest digits a cell must carry to be a money value rather than a stray token. Two,
#: because every printed line value here has at least a whole part and two decimals, and one
#: digit is a footnote marker.
MINIMUM_VALUE_DIGITS: Final = 2

#: Rows are grouped into lines by vertical proximity, as a fraction of the page. A row is about
#: 0.004 of an A4 page tall at 8.5pt, so half of that keeps two adjacent rows apart on a skewed
#: page without splitting one.
ROW_TOLERANCE: Final = 0.0035

_ANCHOR_RULES: Final = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE, Rule.SEPARATORS)


class TotalOutcome(StrEnum):
    AGREES = "agrees"
    #: The printed total is larger than the rows sum to. **The dropped-row signature** — and the
    #: direction that matters, because it means the document says more was invoiced than the
    #: lines account for.
    ROWS_MISSING = "rows_missing"
    #: The rows sum to more than the printed total. A duplicated row, or a row read twice.
    ROWS_SURPLUS = "rows_surplus"
    #: One side could not be read at all. Not an agreement, and never counted as one.
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True, slots=True)
class Column:
    """One declared column, and where it turned out to be on this page."""

    name: str
    is_line_value: bool
    left: float
    box: Box
    similarity: float


@dataclass(frozen=True, slots=True)
class Row:
    """One line item, as read."""

    page: int
    cells: dict[str, str]
    confidence: float
    box: Box

    @property
    def line_value(self) -> str | None:
        return self.cells.get("__value__")


@dataclass(frozen=True, slots=True)
class Table:
    """A line-item table as read off one document."""

    rows: tuple[Row, ...]
    columns: tuple[Column, ...]
    pages_read: tuple[int, ...]
    header_page: int | None

    @property
    def found(self) -> bool:
        return self.header_page is not None


@dataclass(frozen=True, slots=True)
class TotalCheck:
    """The rows against the printed total.

    `difference` is signed from the total's point of view: positive means the total is larger
    than the rows, which is the dropped-row direction.
    """

    outcome: TotalOutcome
    printed_total: Decimal | None
    summed_rows: Decimal | None
    difference: Decimal | None
    rows: int
    #: How many rows carried a value the reader mangled. Where this is non-zero the sum is a
    #: floor, and the check says so rather than presenting it as a total.
    unreadable_rows: int
    explanation: str


def find_columns(page: Page, anchors: dict[str, str], value_column: str) -> tuple[Column, ...]:
    """Locate the table's header on this page.

    Returns empty when the header is not here — which is the normal case for a continuation
    page, and the reason the caller must not treat "no header" as "no table".
    """
    located: list[Column] = []
    for name, caption in anchors.items():
        box, similarity = _best_run(page, caption)
        if box is None:
            continue
        located.append(
            Column(
                name=name,
                is_line_value=name == value_column,
                left=box.left,
                box=box,
                similarity=similarity,
            )
        )
    # Every declared column, or none. A header found by half its captions is a header the
    # reader mangled, and reading rows against a partial column map silently drops a field
    # from every row.
    if len(located) != len(anchors):
        return ()
    return tuple(sorted(located, key=lambda column: column.left))


def read_rows(page: Page, columns: tuple[Column, ...], below: float) -> tuple[Row, ...]:
    """Every row on this page beneath `below`, in reading order.

    `below` is the header's bottom on the page that has one, and the top of the page on a
    continuation. Passing the header's own line would read the header as a row.
    """
    if not columns:
        return ()
    value_column = next((column for column in columns if column.is_line_value), None)
    if value_column is None:
        return ()

    candidates = [word for word in page.words if word.box.top > below]
    lines = _group_into_lines(candidates)

    rows: list[Row] = []
    for line in lines:
        cells: dict[str, str] = {}
        for column in columns:
            members = [
                word
                for word in line
                if abs(word.box.left - column.left) <= COLUMN_TOLERANCE
                or (column.left <= word.box.left <= column.left + COLUMN_TOLERANCE)
            ]
            if members:
                cells[column.name] = " ".join(
                    word.text for word in sorted(members, key=lambda word: word.box.left)
                )

        value = cells.get(value_column.name)
        # A row is only a row if it carries the money. A footer, a note and a stray line of
        # address all sit in the description column; the value column is what separates them.
        if value is None or not _looks_numeric(value):
            continue
        cells["__value__"] = value
        rows.append(
            Row(
                page=page.number,
                cells=cells,
                confidence=min(word.confidence for word in line),
                box=Box.hull([word.box for word in line]),
            )
        )
    return tuple(rows)


def read_table(
    pages: tuple[Page, ...],
    anchors: dict[str, str],
    value_column: str,
) -> Table:
    """The whole table, across however many pages it runs to.

    The continuation is the point. Once a header is found, every **subsequent** page is read
    against the same column positions from the top — because the document prints no header
    there, and a reader that needed one would stop at the page break and report a complete
    table that is short by however many rows followed it.
    """
    columns: tuple[Column, ...] = ()
    header_page: int | None = None
    rows: list[Row] = []
    read: list[int] = []

    for page in sorted(pages, key=lambda page: page.number):
        if not columns:
            found = find_columns(page, anchors, value_column)
            if not found:
                continue
            columns = found
            header_page = page.number
            below = max(column.box.bottom for column in columns)
        else:
            # A continuation page. From the very top, because the table resumes without
            # announcing itself.
            below = 0.0

        page_rows = read_rows(page, columns, below)
        if page_rows:
            read.append(page.number)
            rows.extend(page_rows)

    return Table(
        rows=tuple(rows),
        columns=columns,
        pages_read=tuple(read),
        header_page=header_page,
    )


def check_total(table: Table, printed_total: str | None, tolerance: Decimal) -> TotalCheck:
    """The rows against the printed total.

    The one check on a commercial invoice that can see a row that is not there. It reports and
    does not resolve: a total that disagrees with its rows goes to a human with both numbers,
    because replacing either with the other is smoothing a disagreement, on one document
    instead of two.
    """
    if printed_total is None or not table.rows:
        return TotalCheck(
            outcome=TotalOutcome.NOT_COMPARABLE,
            printed_total=None,
            summed_rows=None,
            difference=None,
            rows=len(table.rows),
            unreadable_rows=0,
            explanation=("no printed total" if printed_total is None else "no rows were read")
            + ". An abstention is not an agreement and is not counted as one",
        )

    try:
        total = _amount(printed_total)
    except QuantityError as exc:
        return TotalCheck(
            outcome=TotalOutcome.NOT_COMPARABLE,
            printed_total=None,
            summed_rows=None,
            difference=None,
            rows=len(table.rows),
            unreadable_rows=0,
            explanation=(
                f"the printed total could not be read: {exc} This is an extraction problem and "
                f"is reported as one — counting rows against a number nobody could read would "
                f"produce a confident answer about nothing"
            ),
        )

    # **A row that cannot be read makes the sum a floor, not an error.** Refusing the whole
    # check on one bad cell was the first version, and it turned 186 of 252 truncated tables
    # into "not comparable" — the dropped-row signal disappeared entirely because one mangled
    # line value poisoned the sum of eight good ones.
    #
    # A floor is enough for the direction that matters. If the rows sum to less than the total
    # even counting only the ones that read, the rows do not account for the total, and that
    # conclusion survives however many more were unreadable. The other direction does not: a
    # surplus over a floor could be a row read twice or could be the unreadable rows being
    # negative, so it is reported as not comparable rather than guessed at.
    summed = Decimal("0")
    unreadable = 0
    for row in table.rows:
        try:
            summed += _amount(row.line_value or "")
        except QuantityError:
            unreadable += 1
    difference = total - summed
    if difference > tolerance:
        # Holds whether or not some rows were unreadable: a floor below the total is still
        # below the total.
        outcome = TotalOutcome.ROWS_MISSING
    elif unreadable:
        outcome = TotalOutcome.NOT_COMPARABLE
    elif abs(difference) <= tolerance:
        outcome = TotalOutcome.AGREES
    else:
        outcome = TotalOutcome.ROWS_SURPLUS

    return TotalCheck(
        outcome=outcome,
        printed_total=total,
        summed_rows=summed,
        difference=difference,
        rows=len(table.rows),
        unreadable_rows=unreadable,
        explanation=(
            f"{len(table.rows)} rows sum to {summed}"
            + (f" (a floor — {unreadable} row values could not be read)" if unreadable else "")
            + f", printed total {total}"
            + (
                ""
                if outcome is TotalOutcome.AGREES
                else f", short by {difference} — the printed total accounts for value the rows "
                f"do not, which is what a dropped row looks like"
                if outcome is TotalOutcome.ROWS_MISSING
                else f", over by {-difference} — the rows account for more than the total, "
                f"which is a row read twice"
                if outcome is TotalOutcome.ROWS_SURPLUS
                else ", and the direction is not attributable while row values are unreadable"
            )
        ),
    )


def _amount(text: str) -> Decimal:
    """A printed money amount as a Decimal.

    **A symbol beside the number is typography; a stray character inside it is a misread**, and
    the two are handled differently on purpose. The first version of this filtered the string
    down to digits and separators and threw the rest away, which is the unsafe direction and
    it produced exactly the failure this repository exists to catch: the reader returned
    `75.812;15` for `75.812,15`, the filter dropped the semicolon, `75.81215` parsed as
    **75.8**, and an invoice worth seventy-five thousand became one worth seventy-five. The
    harness reported the resulting comparison as `ROWS_SURPLUS`, which is a confident answer in
    the wrong direction — the worst shape a number can fail in.

    So: leading and trailing non-numeric characters are dropped (a currency symbol, a stray
    `φ` where the reader saw a `$`), and a run of digits interrupted by anything that is not a
    separator is **refused**. A total the reader mangled is an extraction problem, and calling
    it a quantity is how a mangled total becomes an arithmetic conclusion.
    """
    stripped = text.strip()
    start, end = 0, len(stripped)
    while start < end and not stripped[start].isdigit():
        start += 1
    while end > start and not stripped[end - 1].isdigit():
        end -= 1
    run = stripped[start:end]

    if not run:
        raise QuantityError(f"{text!r} carries no number")
    intruder = next(
        (character for character in run if not (character.isdigit() or character in ".,  ")),
        None,
    )
    if intruder is not None:
        raise QuantityError(
            f"{text!r} has {intruder!r} inside its digits. Dropping it would change the "
            f"magnitude — a misread thousands separator is a factor of a thousand — so this is "
            f"reported as a value that could not be read rather than parsed into a number "
            f"somebody would then act on"
        )
    return parse(run, _Unit.PIECE).amount


def _looks_numeric(text: str) -> bool:
    digits = sum(1 for character in text if character.isdigit())
    return digits >= MINIMUM_VALUE_DIGITS and digits >= len(text.replace(" ", "")) // 2


def _group_into_lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda word: (word.box.top, word.box.left)):
        for line in lines:
            if abs(line[0].box.top - word.box.top) <= ROW_TOLERANCE:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda word: word.box.left) for line in lines]


def _best_run(page: Page, caption: str) -> tuple[Box | None, float]:
    wanted = normalise(caption, _ANCHOR_RULES)
    if not wanted:
        return None, 0.0
    expected = max(1, len(caption.split()))
    best_score, best_box = 0.0, None

    for line in page.lines:
        words = line.words
        for start in range(len(words)):
            for length in range(1, min(expected + 1, len(words) - start) + 1):
                run = words[start : start + length]
                candidate = normalise(" ".join(word.text for word in run), _ANCHOR_RULES)
                if not candidate:
                    continue
                score = SequenceMatcher(None, wanted, candidate).ratio()
                if score > best_score:
                    best_score, best_box = score, Box.hull([word.box for word in run])

    return (best_box, best_score) if best_score >= HEADER_SIMILARITY else (None, best_score)
