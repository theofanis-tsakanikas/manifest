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
