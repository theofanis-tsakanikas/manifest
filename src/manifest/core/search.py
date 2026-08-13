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

__all__ = ["INDEX", "MAXIMUM_HITS", "document_for", "query_for", "searchable_fields"]

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


#: How many records one question returns. An operator asking "which document mentions this
#: container" is looking for a handful; a page of fifty is a list nobody reads to the end, and an
#: unbounded one is a way to pull the index out through a search box one query at a time.
MAXIMUM_HITS = 20


def query_for(term: str, *, document_type: str = "", hits: int = MAXIMUM_HITS) -> dict[str, Any]:
    """The search body for one question, over the fields a record is allowed to be found by.

    **`_all` is not searched, and that is the whole design.** OpenSearch will happily match
    against every field in a document; this asks against a named list instead, so a field that
    arrives in the index tomorrow — because a record grew one — is not searchable until somebody
    adds it here. `document_for` already refuses to *index* anything outside its whitelist, and
    this is the second half of the same rule: the two lists are the only way in and the only way
    out.

    **`query_string` is not used either.** It accepts an expression language, and the term
    reaching this function came off a document a counterparty wrote or a box an operator typed
    into. `multi_match` treats it as text — the same structural argument the extraction prompts
    make about document text, in the one place where a search box makes it easy to forget.
    """
    if not term.strip():
        raise ValueError("a search needs a term; an empty one asks for the whole index")

    must: list[dict[str, Any]] = [
        {
            "multi_match": {
                "query": term,
                # `fields.*` rather than a list of field names: the published values are keyed by
                # the contract's field names, which differ per document type, and enumerating
                # them here would be a third copy of `contracts/documents/` that nothing checks.
                # The nesting is the boundary — `fields` holds published values and nothing else,
                # because `searchable_fields` puts nothing else there.
                "fields": ["fields.*", "document_id"],
                "type": "best_fields",
            }
        }
    ]
    if document_type:
        must.append({"term": {"document_type": document_type}})

    return {
        "size": max(1, min(hits, MAXIMUM_HITS)),
        "query": {"bool": {"must": must}},
        # Newest version first. A corrected record and the record it corrects are both in the
        # index — doctrine rule 4 — so the ordering decides which one an operator reads first,
        # and it should be the current one.
        "sort": [{"indexed_on": {"order": "desc"}}, "_score"],
        # Named rather than left to default to the whole document. A source filter is not a
        # security control — the index is what it is — but it keeps the answer to the shape
        # `core.search` declares, so a consumer cannot come to depend on a field arriving by
        # accident.
        "_source": [
            *_RECORD_FACTS,
            "version",
            "indexed_on",
            "fields",
            "published_field_count",
            "queued_field_count",
        ],
    }
