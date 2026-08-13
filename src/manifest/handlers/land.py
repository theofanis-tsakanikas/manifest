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

    # **Landed already? Then land nothing.** See `_already_landed`.
    if _already_landed(document_id, version):
        return {
            "landed": 0,
            "document_id": document_id,
            "version": version,
            "skipped": "this version is already in the table",
        }

    statement = insert_statement(rows, database=_env("GLUE_DATABASE"), table=_env("LAKE_TABLE"))
    _execute(statement)
    return {"landed": len(rows), "document_id": document_id, "version": version}


def _already_landed(document_id: str, version: str) -> bool:
    """Whether this exact version already has rows in the table.

    **Because the same document read twice is the same version, and this appended anyway.**
    A fingerprint is a function of the bytes and the reader — that is claim 3, and it is the
    property that makes re-extraction reproducible. It also means sending one document through
    the pipeline three times produces *one* version and, before this check, twenty-seven rows for
    nine fields. The records bucket was right (one object), the search index was right (keyed by
    version), and the lake had three copies of everything.

    Nothing failed. Every count in the analytics layer was simply a multiple of the truth,
    including the abstention counts claim 5 is judged on — which is the shape of error that
    survives a review, because the ratios still look plausible.

    A `SELECT` before an `INSERT` is not a transaction and does not need to be: the value being
    written is deterministic, so two runs racing here write identical rows and the worst outcome
    is the duplicate this exists to avoid, at the same odds as before. What it buys is that the
    ordinary case — a document reprocessed after a crash, a redrive, a bulk run over a corpus
    that overlaps — costs one query instead of a permanent double count.
    """
    rows = _query(
        "SELECT count(*) FROM document_version WHERE document_id = ? AND version = ?",
        [document_id, version],
    )
    return bool(rows) and int(rows[0]) > 0


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


def _query(statement: str, parameters: list[str]) -> list[str]:
    """Run a parameterised read and return the first row's values.

    Parameterised rather than interpolated, for the reason `core.lake` gives at length about the
    insert: a value that reaches SQL by concatenation is a value that can end the statement it is
    in. Athena's `ExecutionParameters` is the same control the landing statement's own encoder
    exists to make unnecessary.
    """
    athena = _client("athena")
    started = athena.start_query_execution(
        QueryString=statement,
        ExecutionParameters=parameters,
        WorkGroup=_env("ATHENA_WORKGROUP"),
        QueryExecutionContext={"Database": _env("GLUE_DATABASE")},
    )["QueryExecutionId"]

    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        described = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]
        state = described["Status"]["State"]
        if state == "SUCCEEDED":
            answered = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"]
            # Row 0 is the header. A result with no data row is not an error here — it is a
            # table that has never been written to, which is the first document's case.
            return (
                [
                    cell.get("VarCharValue", "")
                    for cell in answered[1]["Data"]
                    if isinstance(cell, dict)
                ]
                if len(answered) > 1
                else []
            )
        if state in {"FAILED", "CANCELLED"}:
            reason = described["Status"].get("StateChangeReason", "no reason given")
            raise HandlerError(
                f"the idempotence query {state.lower()}: {reason}. Refused rather than landing "
                f"anyway: an append that cannot tell whether it is a duplicate is how the lake "
                f"came to hold three copies of one version"
            )
        time.sleep(POLL_SECONDS)

    raise HandlerError(f"the idempotence query did not finish within {QUERY_TIMEOUT_SECONDS}s")


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
    """A client that gives up quickly and says what it could not reach.

    **The default is to hang, and hanging is the worst failure this estate produces.** With no
    VPC endpoint for Athena, the first call went to an address with no route and sat there until
    Lambda killed the invocation at 180 seconds — three retries, nine minutes of billed duration,
    and **not one log line**: not a refusal, not a boto3 error, nothing. The execution history
    blamed the task, the function's logs were empty, and the actual fact — *this subnet cannot
    reach Athena* — appeared nowhere.

    Five seconds to connect is generous for an endpoint inside the VPC and far short of the
    function's timeout, so a missing route now arrives as `EndpointConnectionError` naming the
    host. That is a minute of investigation instead of an hour.
    """
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name,
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _s3():
    return _client("s3")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
