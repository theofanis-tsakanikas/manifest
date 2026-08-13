"""Ask the search surface a question. The half that made the index worth building.

**A collection nobody can query is a bill, not a capability.** The indexer has been writing
published records into OpenSearch since it was built, the README advertises search over
published records, and nothing has ever read one back — and the network policy makes that
permanent rather than temporary: `AllowFromPublic = false`, so the collection answers inside the
VPC and nowhere else. Correct, and it means the only way to ask it anything is a function that
lives there. This is that function.

**It cannot see anything the indexer did not publish.** `core.search.query_for` asks against a
named list of fields rather than `_all`, and the index only ever received the values that cleared
their thresholds — `document_for` builds from a whitelist. So the two lists are the only way in
and the only way out, and a field that starts arriving in records tomorrow is neither indexed nor
searchable until somebody adds it in both places on purpose.

**The role that runs this cannot write.** The data-access policy gives the indexer
`WriteDocument` and this function `ReadDocument`, as two separate principals — so a search path
that was somehow made to send a mutation is refused by the collection, not only by this code.
"""

from __future__ import annotations

import os
from typing import Any

from manifest.core.search import INDEX, MAXIMUM_HITS, query_for
from manifest.handlers._aoss import SearchSurfaceError, call


class HandlerError(RuntimeError):
    """Refusal."""


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """`{"term": "...", "document_type": "..."} -> the matching published records.`"""
    del context
    term = event.get("term")
    if not isinstance(term, str) or not term.strip():
        raise HandlerError(
            "give {'term': '<what to look for>'}. An empty term asks for the whole index, which "
            "is a way to read it out through a search box one page at a time"
        )

    document_type = event.get("document_type") or ""
    if not isinstance(document_type, str):
        raise HandlerError("document_type must be a string; it is matched exactly")

    body = query_for(term, document_type=document_type, hits=int(event.get("hits", MAXIMUM_HITS)))
    endpoint = _env("SEARCH_ENDPOINT").rstrip("/")

    try:
        answer = call("POST", f"{endpoint}/{INDEX}/_search", body)
    except SearchSurfaceError as error:
        raise HandlerError(str(error)) from error

    hits = answer.get("hits", {}).get("hits", [])
    return {
        "term": term,
        "matched": len(hits),
        "records": [
            {
                # The index key, which is the version — a correction and the record it corrects
                # are both in the index, and a result that did not say which is which would be
                # doctrine rule 4 undone at the point somebody reads it.
                "version": hit.get("_id"),
                "score": hit.get("_score"),
                **hit.get("_source", {}),
            }
            for hit in hits
        ],
        # Stated rather than left to be inferred from the count. A caller that got twenty results
        # cannot tell a full page from a coincidence, and "there may be more" is a different fact
        # from "there are twenty".
        "truncated": len(hits) >= body["size"],
    }


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
