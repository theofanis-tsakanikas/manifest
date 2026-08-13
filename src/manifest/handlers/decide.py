"""Record a reviewer's decision, and let it publish a field — or refuse to.

**The half of claim 5 that was never built.** The queue exists, the decisions table exists, and
until today nothing in this estate ever wrote a row to it: `evals/review/` scored a *generated*
queue offline and no human decision had ever been recorded against a real one. So "a
classification below threshold cannot be published without a recorded human decision" was true of
a pure function and untested as a property of the running system.

**It decides nothing.** `core.review.publishable` decides, and it has no `force`, no `override`
and no severity argument — *a door with a key in the signature is a door that is open*. This
handler loads the record, builds the two dataclasses that function takes, and does what it says.

**A decision produces a new version, never an edit.** Doctrine rule 4: a correction never erases
what was previously published. So an approved field is published by writing a *new* record whose
`supersedes` names the old one, with the version derived from content by
`core.versioning.derive_version` — which means recording the same decision twice produces the
same version and lands nothing new, exactly as re-reading the same bytes does.

**The evidence is written whatever the outcome.** A rejection is as much a recorded decision as
an approval, and a table holding only approvals would make every agreement rate 100% by
construction — which is the rubber stamp doctrine rule 2 exists to detect, built into the schema.

**`agreed_with_model` is computed here, not accepted from the caller.** A reviewer client that
supplied its own agreement flag would be marking its own homework, and claim 5's whole numerator
is that flag. It follows from the decision: an approval agrees, everything else does not.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

from manifest.core.review import Decision, Item, Reason, Record, publishable
from manifest.core.versioning import PublishedField, publish

#: What a reviewer may spend on one field before the number stops being evidence of looking.
#: Not enforced here — `evals/review/` reports the distribution and doctrine rule 2 is about the
#: *rate*, not one item — but recorded per decision, because a rate needs its denominator.
SECONDS_FLOOR = Decimal("0")


class HandlerError(RuntimeError):
    """Refusal."""


def _client(name: str) -> Any:
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name, config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})
    )


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value


def _record_at(bucket: str, document_id: str, version: str) -> dict[str, Any]:
    key = f"records/{document_id}/{version}.json"
    try:
        body = _client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    # Broad on purpose: a missing key, a denied read and a malformed body are one refusal here.
    except Exception as error:
        raise HandlerError(
            f"no published record at {key}. A decision is about a value this system published, "
            f"and there is nothing at that version to decide about"
        ) from error
    return json.loads(body)


def _reason_of(entry: dict[str, Any]) -> Reason:
    """Why this field is in the queue, from the record rather than from the caller.

    Taking it from the request would let a client say `below_threshold` about a field that
    actually has no provenance — and those two have different answers to an approval, which is
    the one asymmetry doctrine rule 7 turns on.
    """
    queued = entry.get("queued_because")
    if not queued:
        raise HandlerError(
            f"field {entry.get('field')!r} published on its own score; there is nothing waiting "
            f"for a human. A decision recorded against it would be evidence of oversight that "
            f"was never needed, in the table claim 5's agreement rate is computed from"
        )
    try:
        return Reason(queued)
    except ValueError as error:
        raise HandlerError(f"the record says {queued!r}, which is not a queue reason") from error


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """`{document_id, version, field, reviewer, decision, value?, seconds_on_task}`."""
    for required in ("document_id", "version", "field", "reviewer", "decision"):
        if not event.get(required):
            raise HandlerError(f"{required} is required; a decision missing it is not evidence")

    try:
        decision = Decision(str(event["decision"]))
    except ValueError as error:
        raise HandlerError(
            f"{event['decision']!r} is not a decision. `approved`, `corrected`, `supplied` and "
            f"`rejected` are four different facts and the difference is not cosmetic"
        ) from error

    records_bucket = _env("RECORDS_BUCKET")
    document_id, version, field = (str(event[k]) for k in ("document_id", "version", "field"))
    record = _record_at(records_bucket, document_id, version)

    entry = next((f for f in record.get("fields", []) if f.get("field") == field), None)
    if entry is None:
        raise HandlerError(f"{document_id} version {version} has no field {field!r}")

    item = Item(
        document=document_id,
        field=field,
        reason=_reason_of(entry),
        value=entry.get("value"),
        confidence=float(entry.get("confidence") or 0.0),
        has_provenance=bool(entry.get("box")),
    )

    supplied = event.get("value")
    made = Record(
        document=document_id,
        field=field,
        reviewer=str(event["reviewer"]),
        decision=decision,
        value=str(supplied) if supplied is not None else None,
        seconds_on_task=max(Decimal(str(event.get("seconds_on_task", 0))), SECONDS_FLOOR),
        # Computed, never accepted. See the module docstring.
        agreed_with_model=decision is Decision.APPROVED,
    )

    allowed, why = publishable(item, made)

    # **Written before the outcome is acted on, and written whatever it is.** The decision is
    # evidence about a person; whether it published anything is a consequence. Recording only
    # the ones that published would make claim 5's agreement rate a statement about approvals.
    _write_decision(made, version=version, published=allowed, reason=why)

    if not allowed:
        return {
            "document_id": document_id,
            "version": version,
            "field": field,
            "decision": str(decision),
            "published": False,
            "reason": why,
        }

    superseding = _publish_with(record, entry, made)
    _client("s3").put_object(
        Bucket=records_bucket,
        Key=f"records/{document_id}/{superseding['fingerprint']}.json",
        Body=json.dumps(superseding).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=_env("DATA_KEY_ARN"),
    )
    return {
        "document_id": document_id,
        "version": superseding["fingerprint"],
        "supersedes": version,
        "field": field,
        "decision": str(decision),
        "published": True,
        "reason": why,
    }


def _publish_with(record: dict[str, Any], entry: dict[str, Any], made: Record) -> dict[str, Any]:
    """The record again, with this field carrying the reviewer's outcome, as a new version.

    The version is derived from content by `core.versioning`, so the same decision recorded twice
    produces the same identifier — the records bucket already holds it and the lake already
    refuses a duplicate. Idempotence by construction rather than by a conditional write.
    """
    decided_value = made.value if made.value is not None else entry.get("value")
    fields = [
        PublishedField(
            field=str(f["field"]),
            value=(decided_value if f["field"] == made.field else f.get("value")),
            confidence=(float(f["confidence"]) if f.get("confidence") is not None else None),
            page=(int(f["page"]) if f.get("page") is not None else None),
            box=(tuple(float(edge) for edge in f["box"]) if f.get("box") else None),
        )
        for f in record.get("fields", [])
    ]

    version = publish(
        document_id=str(record["document_id"]),
        source_digest=str(record.get("source_digest") or record.get("fingerprint") or ""),
        # **The reader identity carries the reviewer.** A version published on a human's decision
        # was not produced by the reader alone, and a record that claimed it was would let a
        # threshold derivation count a human's value as evidence about an engine.
        reader=f"{record.get('reader', '')}+review:{made.reviewer}",
        contract_version=int(record.get("contract_version", 1)),
        fields=fields,
        supersedes=str(record["fingerprint"]),
    )

    decided = []
    for f in record.get("fields", []):
        if f["field"] != made.field:
            decided.append(f)
            continue
        decided.append(
            {
                **f,
                "value": decided_value,
                "publishable": True,
                "queued_because": None,
                "decided_by": made.reviewer,
                "decision": str(made.decision),
            }
        )

    return {
        **record,
        "fingerprint": version.version,
        "supersedes": version.supersedes,
        "reader": version.reader,
        "fields": decided,
        "publishable_count": sum(1 for f in decided if f.get("publishable")),
        "queued_count": sum(1 for f in decided if f.get("queued_because")),
    }


def _write_decision(made: Record, *, version: str, published: bool, reason: str) -> None:
    """One row per decision, in the table that has never held one.

    Keyed by `(document, field#version)` so a decision on a re-extracted document is a second row
    rather than an overwrite — decision 12's point: a decision is about the value it was made on.
    """
    from datetime import UTC, datetime  # noqa: PLC0415 - the clock is the adapter's, not core's

    _client("dynamodb").put_item(
        TableName=_env("DECISIONS_TABLE"),
        Item={
            "document": {"S": made.document},
            "field": {"S": f"{made.field}#{version}"},
            "reviewer": {"S": made.reviewer},
            "decision": {"S": str(made.decision)},
            "value": ({"S": made.value} if made.value is not None else {"NULL": True}),
            "seconds_on_task": {"N": str(made.seconds_on_task)},
            "agreed_with_model": {"BOOL": made.agreed_with_model},
            "published": {"BOOL": published},
            "reason": {"S": reason[:900]},
            "decided_on": {"S": datetime.now(UTC).isoformat()},
        },
    )
