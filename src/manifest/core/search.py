"""A published record, as a search document.

**It indexes published records. It never indexes raw document text.** `infra/lakehouse/search.tf`
states that as the design and this is where it is enforced, because a comment cannot refuse a
field. A commercial invoice is a document a counterparty wrote; `security/injection.py` treats
its text as data everywhere else in this system, and an index of raw page text is that same text
retained, ranked and surfaced to a human about to make a customs decision — with the retention
class its contract declares quietly dropped on the way in.

So the shape is a whitelist rather than a redaction. Nothing is removed from a record on its way
here; a document is **built** from the facts that are allowed to be searchable, and a field this
module does not name cannot arrive by being added upstream. That distinction is the whole of the
control: a deny-list is one new key away from leaking, and the key is added by somebody solving a
different problem.

**Only what was published.** A field that abstained has no value in the record — `core.lake`
already refuses to carry one — and it carries none here either. Searching over readings that no
threshold approved would put unapproved values in front of the person the abstention was raised
for, which is the review queue's job done backwards.

**No confidence, no threshold, no box.** They are in the lake, where a query can weigh them; in
a search result they are decoration that invites a reader to treat a ranking as a measurement.
What a search answers is *which document*, and the answer to *how good is this value* is one
join away in a table built for it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["INDEX", "document_for", "searchable_fields"]

#: The index every published record lands in. One index rather than one per document type: the
#: question an operator asks is "which document mentions this container", and that question does
#: not know the type in advance.
INDEX = "records"

#: The record-level facts a search result may carry. Named exhaustively — anything not here is
#: not indexed, including anything a future version of the record grows.
_RECORD_FACTS: tuple[str, ...] = (
    "document_id",
    "document_type",
    "language",
    "reader",
)


def searchable_fields(record: Mapping[str, Any]) -> dict[str, str]:
    """The published values, by field name.

    A field with no value published is absent rather than empty: an empty string is a value that
    matches a search for the empty string, and "we have no shipper for this document" and "the
    shipper is blank" are different facts about a customs record.
    """
    fields = record.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes):
        return {}
    return {
        str(entry["field"]): str(entry["value"])
        for entry in fields
        if isinstance(entry, Mapping)
        and entry.get("field")
        and entry.get("publishable")
        and entry.get("value")
    }


def document_for(record: Mapping[str, Any], *, indexed_on: str) -> tuple[str, dict[str, Any]]:
    """`(document id for the index, the document)`.

    The index id is the **version**, not the document id. A correction is a new version beside
    the old one — doctrine rule 4 — and an index keyed by document id would overwrite the older
    one, which is the one behaviour the records bucket refuses. Searching finds both, and each
    says which it is.
    """
    version = str(record.get("fingerprint") or "")
    document_id = str(record.get("document_id") or "")
    if not version or not document_id:
        raise ValueError(
            "a record with no document id or no version cannot be indexed: the version is the "
            "index key, and keying by document id would make a correction overwrite the record "
            "it corrects — the one thing the records bucket exists to refuse"
        )

    document = {fact: str(record.get(fact) or "") for fact in _RECORD_FACTS}
    document["version"] = version
    document["indexed_on"] = indexed_on
    document["fields"] = searchable_fields(record)
    # A count rather than the abstentions themselves. How much of a document went to a human is
    # a useful thing to filter on; *which* values were read and not published is exactly the
    # unapproved reading this module refuses to surface.
    document["published_field_count"] = len(document["fields"])
    document["queued_field_count"] = sum(
        1
        for entry in record.get("fields", [])
        if isinstance(entry, Mapping) and entry.get("queued_because")
    )
    return version, document
