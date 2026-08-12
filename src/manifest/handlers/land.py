"""Write a published record into the record lake.

**The function the estate has never had.** `infra/lakehouse` has declared an Iceberg table, a
Glue database and an Athena workgroup since the day it was written, and nothing has ever put a
row in it. Four things read from that emptiness — the warehouse marts, the search surface, the
bulk reprocessor, and any Athena query at all — so one missing step made four services
decorative. `notes/ARCHITECTURE.md` calls the warehouse *"a schema over nothing"*, accurately.

**This handler decides nothing.** `core.lake` turns the record into rows and renders the
statement; everything here is the part that needs credentials: read the previous version, run
the query, wait for it, report what landed. If a comparison operator about a *field* appears in
this file, the mapping has been bypassed.

**Why Athena rather than an Iceberg client.** The table is registered in Glue and Athena is
already in the layer with a workgroup, a result location and a bytes ceiling. Writing Parquet
and committing metadata from a Lambda would reimplement the commit protocol — including the
retry semantics of a concurrent append — in the one place nobody could test it offline. An
`INSERT` is a statement the engine that owns the table executes.

The cost of that choice is stated rather than discovered: one query per document, at Athena's
per-query minimum, which is the wrong shape for four million documents. The bulk path exists
for that and is what `pipelines/reprocess.py` drives; this is the per-document path, and a
document is what arrives.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from manifest.core.lake import insert_statement, rows_for

#: How long to wait for the statement. An `INSERT` of a handful of rows into Iceberg is seconds;
#: the ceiling is here so that a queue backed up behind a stuck query fails rather than holds a
#: Lambda open to its own timeout, where the error would be about a timeout rather than a query.
QUERY_TIMEOUT_SECONDS = 120
POLL_SECONDS = 2


class HandlerError(RuntimeError):
    """Refusal. The state machine has no `Catch` on this state for the usual reason."""


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Entry point. Takes the record the pipeline published; returns what it landed."""
    del context
    record = event.get("record") or event
    if not isinstance(record, dict) or "fields" not in record:
        raise HandlerError(
            "the event carries no published record. This state runs after `Publish`, and the "
            "record is passed rather than re-read so that what lands is exactly what was "
            "published rather than whatever the key holds now"
        )

    document_id = str(record.get("document_id") or "")
    version = str(record.get("fingerprint") or "")
    rows = rows_for(
        record,
        # **The clock lives here, not in core.** `extracted_on` is a fact about when this ran,
        # and a core that could read a clock could produce a different answer on two runs over
        # the same input — which is claim 3's property, given away for a convenience.
        extracted_on=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        supersedes=_previous_version(document_id, version),
    )

    statement = insert_statement(rows, database=_env("GLUE_DATABASE"), table=_env("LAKE_TABLE"))
    _execute(statement)
    return {"landed": len(rows), "document_id": document_id, "version": version}


def _previous_version(document_id: str, version: str) -> str | None:
    """The version this one supersedes, or `None` if it is the first.

    Read from the records bucket rather than from the lake, and the difference matters: the
    bucket is where doctrine rule 4 is enforced — *"a correction never erases what was
    previously published"* — so it holds one object per version and is the authority on what
    came before. Asking the lake would ask a table this function is in the middle of writing.

    Most recent first, and the current version skipped rather than assumed absent: this handler
    runs after `Publish`, so the object for *this* version is already there.
    """
    listing = _s3().list_objects_v2(Bucket=_env("RECORDS_BUCKET"), Prefix=f"records/{document_id}/")
    others = [
        entry
        for entry in listing.get("Contents", [])
        if not entry["Key"].endswith(f"/{version}.json")
    ]
    if not others:
        return None
    newest = max(others, key=lambda entry: entry["LastModified"])
    return newest["Key"].rsplit("/", 1)[-1].removesuffix(".json")


def _execute(statement: str) -> None:
    athena = _client("athena")
    started = athena.start_query_execution(
        QueryString=statement,
        WorkGroup=_env("ATHENA_WORKGROUP"),
        QueryExecutionContext={"Database": _env("GLUE_DATABASE")},
    )
    execution = started["QueryExecutionId"]

    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        described = athena.get_query_execution(QueryExecutionId=execution)["QueryExecution"]
        state = described["Status"]["State"]
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "CANCELLED"}:
            reason = described["Status"].get("StateChangeReason", "no reason given")
            raise HandlerError(
                f"the landing query {state.lower()}: {reason}. The record is published and this "
                f"row is not in the lake — which is the right way round: the customs record is "
                f"the object in the records bucket, and the lake is a view of it that can be "
                f"rebuilt"
            )
        time.sleep(POLL_SECONDS)

    raise HandlerError(
        f"the landing query did not finish within {QUERY_TIMEOUT_SECONDS}s (execution "
        f"{execution}). Refused rather than left running: a Lambda that times out reports a "
        f"timeout, and the question worth answering is which query"
    )


def _client(name: str):
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS

    return boto3.client(name)


def _s3():
    return _client("s3")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
