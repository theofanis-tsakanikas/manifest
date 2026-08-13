"""A published record, as rows of the record lake.

**The lake has been a schema over nothing since the day it was created.** `infra/lakehouse`
declares an Iceberg table, a Glue database and an Athena workgroup; the pipeline publishes JSON
to S3 and no step ever converted one into the other. Four things read from that emptiness — the
warehouse marts, the search surface, the bulk reprocessor and every Athena query anybody would
run — so one missing function made four services decorative.

This is the pure half: a published record in, one row per field out. It has no clock and no
client, which is what lets the mapping be tested against 3,000 documents in milliseconds.
`extracted_on` and `supersedes` are supplied by the caller for that reason and not out of
tidiness — the first is a fact about *when the adapter ran* and the second needs a lookup, and a
core that could reach either could reach anything.

**One row per field, not one per document, and that is the schema's decision rather than this
module's.** A customs record is asked questions like *what did we say the gross weight was, when,
against which threshold, and did a human touch it* — all of which are per field. A row per
document would push every one of those into a nested column nobody can join on.

**A field that abstained carries a NULL value, and its row still exists.** That pairing is the
whole point of the table:

*The value is null* because it was never published. It was read, it did not clear its
threshold, and writing it into the lake anyway would put an unpublished reading in the place
downstream consumers treat as the customs record — doctrine rule 3 with a warehouse attached.

*The row is there anyway* because the fact that the field was read, at that confidence, against
that threshold, and published nothing is exactly what claim 5's economics are computed from.
Dropping the row would make the queue invisible to every query in the analytics layer, and the
one number that matters about a thresholding system is how much it sends to humans.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Row", "rows_for"]


@dataclass(frozen=True)
class Row:
    """One field of one version of one document, in the order the table declares.

    Frozen and typed rather than a dict, so that a column added to the table without a value
    here fails at construction instead of arriving as a silent NULL in a mart.
    """

    document_id: str
    version: str
    supersedes: str | None
    reader: str
    #: **Three facts the record carries and this table used to drop.** The warehouse's marts
    #: group by document type, by language and by tier — "error rate by carrier and language",
    #: "modelled cost per tier" — and none of the three was in a row, so the questions the
    #: analytics layer was built to answer could not be asked of the data it reads. The record
    #: has all three; the mapping simply did not carry them.
    #:
    #: `reader_tier` is the number, beside `reader` which is the identity. Both, because the
    #: identity is what claim 1's thresholds are keyed to and the tier is what the cost model
    #: groups by, and deriving either from the other means parsing a string somebody will change.
    document_type: str
    language: str
    reader_tier: int
    #: Whether this value reached a consumer. Distinct from `value IS NOT NULL`, which is the
    #: same thing today and stops being it the moment a field publishes an empty string.
    published: bool
    field: str
    value: str | None
    confidence: float | None
    threshold: float | None
    page: int | None
    box: tuple[float, ...] | None
    provenance_verified: bool
    review_decision: str | None
    extracted_on: str


#: The verdict the provenance gate gives a field it checked and accepted. Every other verdict —
#: `refused`, `uncheckable`, `not_applicable` — is *not* verified, and they are deliberately not
#: collapsed into one another anywhere else in this system.
VERIFIED = "verified"


def rows_for(
    record: Mapping[str, Any],
    *,
    extracted_on: str,
    supersedes: str | None = None,
) -> tuple[Row, ...]:
    """Every row this published record contributes to the lake.

    `record` is what `publish` returned and the pipeline wrote — after any escalation, because
    the escalation re-thresholds and the record that lands must be the one that was published.
    """
    document_id = str(record.get("document_id") or "")
    version = str(record.get("fingerprint") or "")
    if not document_id or not version:
        raise ValueError(
            "a record with no document id or no version cannot be landed: the two of them are "
            "the key, and a row keyed by the empty string is a row no re-extraction can ever "
            "supersede"
        )

    fields = record.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes):
        raise ValueError("a published record carries a list of field outcomes; this carries none")

    return tuple(
        Row(
            document_id=document_id,
            version=version,
            supersedes=supersedes,
            reader=str(record.get("reader") or ""),
            document_type=str(record.get("document_type") or ""),
            language=str(record.get("language") or ""),
            reader_tier=int(entry.get("reader_tier", record.get("reader_tier", 0)) or 0),
            published=bool(entry.get("publishable")),
            field=str(entry["field"]),
            # **Published values only.** `publishable` is the record's own word for "this
            # cleared its threshold and the gate did not refuse it", and anything else is a
            # reading that no threshold approved. It exists, it is in the queue, and it is not
            # what this system said the field was.
            value=str(entry["value"]) if entry.get("publishable") and entry.get("value") else None,
            confidence=_number(entry.get("confidence")),
            threshold=_number(entry.get("threshold")),
            page=int(entry["page"]) if entry.get("page") is not None else None,
            box=tuple(float(edge) for edge in entry["box"]) if entry.get("box") else None,
            provenance_verified=entry.get("verdict") == VERIFIED,
            # Null until a human decides. The column exists from the first row rather than being
            # added when review is built, because a schema that grows a column later cannot
            # answer "was this reviewed?" about anything published before it.
            review_decision=None,
            extracted_on=extracted_on,
        )
        for entry in fields
        if isinstance(entry, Mapping) and entry.get("field")
    )


def _number(value: Any) -> float | None:
    """A float, or nothing. `0.0` is a score and must survive; `None` is the absence of one."""
    return None if value is None else float(value)


def insert_statement(rows: Sequence[Row], *, database: str, table: str) -> str:
    """The `INSERT` that lands these rows, with every value encoded rather than interpolated.

    **A field value is text a counterparty wrote.** `CLAUDE.md` says that about extraction
    prompts and it is no less true of a warehouse: a party name is chosen by the party. The
    values here have already passed through a reader, so they are unlikely to be well-formed
    SQL — and "unlikely" is the word that makes this the classic mistake. A packing list whose
    consignee is `ACME', 1, 1); DROP TABLE` is a document somebody can post.

    So values never reach the statement as themselves. Strings are encoded by the one rule
    Trino, and therefore Athena, defines for a string literal — a single quote is written twice —
    and everything else is refused rather than quoted hopefully:

    *Numbers* are rendered from `float`/`int` the caller already parsed, so a string cannot
    arrive in a numeric position at all. *Nulls* are the bare keyword, because `'NULL'` is a
    three-character string and the difference between "we abstained" and "we published the word
    NULL" is the whole of doctrine rule 3.

    **Why a string at all, rather than parameters.** Athena's execution parameters carry every
    value as text and cast on the way in, which puts the type decision inside the engine and out
    of reach of a test. Here the encoding is a pure function over plain data, `evals/injection`
    can attack it, and `gate-proof` can break it — which is the trade this repository makes
    everywhere else too.
    """
    if not rows:
        raise ValueError("no rows to land; an INSERT with no VALUES is a syntax error")

    columns = (
        "document_id, version, supersedes, reader, document_type, language, reader_tier, "
        "published, field, value, confidence, threshold, "
        "page, box, provenance_verified, review_decision, extracted_on, extraction_date"
    )
    values = ",\n  ".join(_row_literal(row) for row in rows)
    target = f'"{_identifier(database)}"."{_identifier(table)}"'
    return f"INSERT INTO {target} ({columns})\nVALUES\n  {values}"


def _row_literal(row: Row) -> str:
    return (
        "("
        + ", ".join(
            (
                _text(row.document_id),
                _text(row.version),
                _text(row.supersedes),
                _text(row.reader),
                _text(row.document_type),
                _text(row.language),
                _integer(row.reader_tier),
                "true" if row.published else "false",
                _text(row.field),
                _text(row.value),
                _double(row.confidence),
                _double(row.threshold),
                _integer(row.page),
                _box(row.box),
                "true" if row.provenance_verified else "false",
                _text(row.review_decision),
                f"timestamp {_text(row.extracted_on)}",
                f"date {_text(row.extracted_on[:10])}",
            )
        )
        + ")"
    )


def _identifier(name: str) -> str:
    """A catalogue name, refused unless it is one.

    These come from `infra/lakehouse`, not from a document, so the check is cheap insurance
    rather than a control — but an identifier cannot be escaped the way a value can, so the only
    safe treatment is to refuse anything unexpected instead of quoting it and hoping.
    """
    if not name or not all(character.isalnum() or character == "_" for character in name):
        raise ValueError(
            f"{name!r} is not a plain catalogue identifier. Unlike a value, an identifier has no "
            f"escaping rule that makes an arbitrary string safe, so it is refused"
        )
    return name


def _text(value: str | None) -> str:
    if value is None:
        return "NULL"
    # The whole of the rule, and it is the whole of it on purpose. Backslash is *not* an escape
    # character in a Trino string literal, so doubling quotes is sufficient and adding
    # backslash handling would corrupt any value that legitimately contains one — a Windows path
    # in a free-text field, most obviously.
    return "'" + str(value).replace("'", "''") + "'"


def _double(value: float | None) -> str:
    return "NULL" if value is None else f"CAST({float(value)!r} AS DOUBLE)"


def _integer(value: int | None) -> str:
    return "NULL" if value is None else str(int(value))


def _box(box: tuple[float, ...] | None) -> str:
    if box is None:
        return "NULL"
    edges = ", ".join(f"CAST({float(edge)!r} AS DOUBLE)" for edge in box)
    return f"ARRAY[{edges}]"
