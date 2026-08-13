"""One signed request to OpenSearch Serverless, shared by the writer and the reader.

**Extracted rather than copied, and the method is the reason.** The indexer signed a `PUT` with
the method written into the signing call as a literal. A reader signing a `POST` against a copy
of that function gets a signature computed over the wrong method — which AWS rejects as a
credential mismatch, in a message that sends the reader to the data-access policy for a problem
that is one string. Two callers with different verbs is exactly when a copied signer stops being
harmless.

**`aoss`, not `es`.** They are different signing scopes. The wrong one fails the same way, and
this is the one place that fact needs to be written down.

No client library. `botocore` ships in the Lambda runtime and signs a request; an OpenSearch SDK
would be a wheel in the zip for the sake of two HTTP calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

#: How long to wait on the collection. Serverless takes a moment to route a first request after
#: an idle period; a call that cannot complete in this long is a surface that is down, and the
#: caller should say so rather than hold a Lambda open.
TIMEOUT_SECONDS = 20

#: The first status that is not a success. Named because `>= 300` in a condition is a number
#: somebody eventually reads as a timeout.
FIRST_ERROR_STATUS = 300

#: The signing scope. See the module docstring.
SERVICE = "aoss"


class SearchSurfaceError(RuntimeError):
    """The collection refused a request, or could not be reached."""


def call(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sign, send, and return the parsed answer — or refuse with a reason worth reading."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    request = Request(url, data=body or None, method=method)  # noqa: S310 - https, from an env value
    request.add_header("Content-Type", "application/json")
    for header, value in _signature(method, url, body).items():
        request.add_header(header, value)

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as answer:  # noqa: S310 - as above
            if answer.status >= FIRST_ERROR_STATUS:
                raise SearchSurfaceError(f"the collection answered {answer.status} for {url}")
            raw = answer.read().decode("utf-8")
    except HTTPError as error:
        # The body carries OpenSearch's own reason — a mapping conflict, a refused field, an
        # index that does not exist. The status alone would send the reader to the data-access
        # policy for what is often a document-shape problem.
        detail = error.read().decode("utf-8", "replace")[:400]
        raise SearchSurfaceError(f"the collection refused it: {error.code} {detail}") from error
    except URLError as error:
        raise SearchSurfaceError(
            f"the collection could not be reached: {error.reason}. Every function here runs in "
            f"private subnets with no route out, so this is a VPC endpoint for `aoss` rather "
            f"than a credential"
        ) from error

    return json.loads(raw) if raw else {}


def _signature(method: str, url: str, body: bytes) -> dict[str, str]:
    """SigV4 headers, from the role the function already runs as.

    **`x-amz-content-sha256` is set here and it is the whole reason this ever returned 403.**
    `SigV4Auth` computes the payload hash and does not send it as a header — for most services it
    is not required. OpenSearch Serverless requires it, and refuses a request without it with a
    bare `403 Forbidden` carrying no reason: the same answer it gives a principal with no
    permission at all.

    That is why it cost a deploy. The access policy was correct, the role was correct, and every
    piece of evidence pointed at the one thing that was right — a 403 from a data store reads as
    "the policy is wrong" and nothing in the response says otherwise. The header must be added
    **before** `add_auth`, or it is not part of what was signed.
    """
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415

    session = boto3.Session()
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Amz-Content-Sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    SigV4Auth(session.get_credentials(), SERVICE, session.region_name).add_auth(request)
    return dict(request.headers)
