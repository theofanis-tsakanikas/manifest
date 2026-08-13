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
OpenSearch client library would be a wheel in the zip for the sake of a `PUT`.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from manifest.core.search import INDEX, document_for

#: How long to wait on the collection. Serverless takes a moment to route a first request after
#: an idle period; a document that cannot be indexed in this long is a surface that is down, and
#: the pipeline should say so rather than hold a Lambda open.
TIMEOUT_SECONDS = 20

#: The first status that is not a success. Named because `>= 300` in a condition is a number
#: somebody eventually reads as a timeout.
FIRST_ERROR_STATUS = 300


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
    _put(f"{endpoint}/{INDEX}/_doc/{key}", document)
    return {
        "indexed": key,
        "document_id": document["document_id"],
        "searchable_fields": document["published_field_count"],
    }


def _put(url: str, document: dict[str, Any]) -> None:
    body = json.dumps(document).encode("utf-8")
    request = Request(url, data=body, method="PUT")  # noqa: S310 - https, built from an env value
    request.add_header("Content-Type", "application/json")
    for header, value in _signature(url, body).items():
        request.add_header(header, value)

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as answer:  # noqa: S310 - as above
            if answer.status >= FIRST_ERROR_STATUS:
                raise HandlerError(f"the collection answered {answer.status} indexing {url}")
    except HTTPError as error:
        # The body carries OpenSearch's own reason — a mapping conflict, a refused field. The
        # status alone would send the reader to the data-access policy for what is often a
        # document-shape problem.
        detail = error.read().decode("utf-8", "replace")[:400]
        raise HandlerError(f"the collection refused the document: {error.code} {detail}") from error
    except URLError as error:
        raise HandlerError(
            f"the collection could not be reached: {error.reason}. Every function here runs in "
            f"private subnets with no route out, so this is a VPC endpoint for `aoss` rather "
            f"than a credential"
        ) from error


def _signature(url: str, body: bytes) -> dict[str, str]:
    """SigV4 headers for `aoss`, from the role the function already runs as.

    The service name is `aoss`, not `es`. They are different signing scopes and the wrong one
    produces a signature mismatch that reads as a credential problem — which is a long way from
    "this was signed for the wrong service".
    """
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415

    session = boto3.Session()
    request = AWSRequest(
        method="PUT", url=url, data=body, headers={"Content-Type": "application/json"}
    )
    SigV4Auth(session.get_credentials(), "aoss", session.region_name).add_auth(request)
    return dict(request.headers)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
