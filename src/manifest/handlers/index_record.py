"""Put a published record into the search surface.

**The service that was in the subtitle and had nothing to show.** `infra/lakehouse/search.tf`
has declared an OpenSearch Serverless collection since it was written, the README advertises it,
and nothing has ever written a document to it — so "search over published records" was a
sentence rather than a surface. Worse, the flag that stands it up could not be set by any
dispatch until today, which meant the collection was not off by default; it was unbuildable.

**This handler decides nothing.** `core.search` builds the document — a whitelist of facts that
are allowed to be searchable, so that a field it does not name cannot arrive by being added
upstream — and everything here is the part that needs credentials: sign the request, send it,
report what happened.

**Signed with SigV4 and nothing else.** OpenSearch Serverless takes no password and no API key;
the caller is the IAM role, and the collection's data-access policy names it. That is the same
boundary every other store in this estate uses, which is the reason to prefer it: one place to
read to find out who can write here.

No new dependency. `botocore` ships with the Lambda runtime and can sign a request; an
OpenSearch client library would be a wheel in the zip for the sake of a `PUT`. The signing and
the transport moved to `_aoss.py` when a reader appeared that needed the same two and a different
verb — the method is part of the signature, so a copied signer would have failed as a credential
problem.
"""

from __future__ import annotations

import os
from typing import Any

from manifest.core.search import INDEX, document_for
from manifest.handlers._aoss import SearchSurfaceError, call


class HandlerError(RuntimeError):
    """Refusal."""


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Entry point. Takes the record the pipeline published; returns what it indexed."""
    del context
    record = event.get("record") or event
    if not isinstance(record, dict) or "fields" not in record:
        raise HandlerError(
            "the event carries no published record. This runs after `Publish`, and the record "
            "is passed rather than re-read so that what is indexed is exactly what was "
            "published rather than whatever the key holds now"
        )

    from datetime import UTC, datetime  # noqa: PLC0415 - the clock is the adapter's, not core's

    key, document = document_for(record, indexed_on=datetime.now(UTC).isoformat())
    endpoint = _env("SEARCH_ENDPOINT").rstrip("/")
    try:
        call("PUT", f"{endpoint}/{INDEX}/_doc/{key}", document)
    except SearchSurfaceError as error:
        raise HandlerError(str(error)) from error
    return {
        "indexed": key,
        "document_id": document["document_id"],
        "searchable_fields": document["published_field_count"],
    }


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
