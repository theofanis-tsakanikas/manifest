"""Bulk re-extraction on a Spark application — the adapter over `core.scale`.

**Written, validated, never run.** `docs/DECISIONS.md` 14. Claim 7 is proved against the pure
planner on a laptop (`evals/scale/`), and this is the thing that would execute a plan it
produced. Saying it the other way round — proving idempotence "on EMR Serverless" — would be
claiming a property of a cluster nobody has started.

**The job decides nothing.** It reads the ledger, calls `plan`, distributes the work the plan
describes, and appends what completed. Every branch that matters — what to skip, what to
re-process, what a reader change means — is in `manifest.core.scale`, where it runs in
milliseconds against 3,000 documents in a test. A job that made those decisions itself would
move them somewhere no offline test can reach, and claim 7 would become a claim about a bill.

Two properties this shape buys, and both are the reason for it rather than a side effect:

**A crash is resumable by construction.** The ledger is appended per completed document, not
per run, so a resumed job plans exactly the remainder. Recording the whole plan optimistically
would make a crashed job look complete — the silent failure — and recording nothing would make
it repeat four million documents, which is the expensive one.

**Nothing is written twice.** A document already recorded at this reader version is planned as
`SKIP` before any executor sees it, so idempotence costs a dictionary lookup rather than a
conditional write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# **Checked before the import that would fail, and with the answer in the message.**
#
# `manifest.core.scale` uses `StrEnum` (3.11) and slotted dataclasses (3.10); `pyproject.toml`
# asks for 3.12. An EMR Serverless application runs whatever Python its release image carries,
# which is not a thing this repository controls or can see offline — so the failure would
# otherwise be an `ImportError` inside a Spark stack trace, on a driver, minutes after a cluster
# started, naming `StrEnum` and nothing about what to do.
#
# Refusing here costs one comparison and turns it into a sentence with the fix in it. The fix is
# a custom image for the application; the alternative — weakening the core so a cluster can
# import it — would make the estate's Python the repository's Python, which is backwards.
#: What `pyproject.toml` asks for. A tuple rather than the literal in the comparison, because
#: `ruff` reads a literal one as dead code — correctly, for a file that only ever runs here.
#: This one also runs on a driver whose interpreter nobody in this repository chose.
_NEEDED = (3, 12)

if sys.version_info < _NEEDED:  # pragma: no cover - the laptop and CI are both 3.12
    raise SystemExit(
        f"this job needs Python 3.12 and the driver is running "
        f"{sys.version_info.major}.{sys.version_info.minor}. `manifest.core.scale` — the planner "
        f"claim 7 is proved against — uses language features this interpreter does not have, and "
        f"the planner is the one part of this job that must not be reimplemented for the "
        f"cluster. Give the EMR Serverless application a custom image carrying 3.12."
    )

from manifest.core.scale import Disposition, LedgerEntry, plan, record  # noqa: E402

#: How long one document's pipeline may take before an executor stops waiting on it. The
#: per-document path is bounded by the tier-0 function's own timeout plus the gate's; longer than
#: this is an execution that is stuck rather than slow, and an executor holding a slot for it is
#: paying for the wait twice.
EXECUTION_TIMEOUT_SECONDS = 900

#: Between polls. Step Functions has no callback for a standard workflow, and a tighter loop
#: spends its API quota discovering that OCR is still OCR.
POLL_SECONDS = 5

if TYPE_CHECKING:  # pragma: no cover - the cluster's types, never imported here
    pass


def read_ledger(rows: list[dict[str, Any]]) -> list[LedgerEntry]:
    """The ledger as the core wants it.

    A plain function over dictionaries so that the whole planning path is testable without a
    session. The DynamoDB read that produces `rows` is the only part of this file that needs an
    account, and it is three lines at the bottom.
    """
    return [
        LedgerEntry(document=row["document"], reader=row["reader"], version=row["version"])
        for row in rows
    ]


def partitions(plan_items: list[Any], size: int) -> list[list[Any]]:
    """Split the work into batches of a declared size.

    Declared rather than derived from the cluster's parallelism, because a partition size that
    follows the cluster makes a re-run on a differently-sized application do different work —
    and "idempotent" would quietly mean "idempotent at this scale".
    """
    if size < 1:
        raise ValueError("a partition of fewer than one document is not a partition")
    return [plan_items[start : start + size] for start in range(0, len(plan_items), size)]


def describe(the_plan: Any) -> dict[str, int]:
    """What this run would do, before it does any of it.

    Printed and written to the run record. A bulk job that starts without saying how much work
    it believes it has is a job whose cost is discovered from a bill.
    """
    return {
        "process": len(the_plan.of(Disposition.PROCESS)),
        "reprocess": len(the_plan.of(Disposition.REPROCESS)),
        "skip": len(the_plan.of(Disposition.SKIP)),
        "work": the_plan.work,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader", required=True, help="The reader identity to process at.")
    parser.add_argument("--documents", type=Path, help="JSON list of document ids. Offline mode.")
    parser.add_argument("--ledger", type=Path, help="JSON list of ledger entries. Offline mode.")
    parser.add_argument(
        "--landing-bucket",
        help=(
            "Discover documents and their source keys from this bucket instead of --documents. "
            "Required for a real run."
        ),
    )
    parser.add_argument(
        "--ledger-table", help="Read and append the ledger here instead of --ledger."
    )
    parser.add_argument("--state-machine", help="The per-document pipeline each executor starts.")
    parser.add_argument("--partition-size", type=int, default=200)
    parser.add_argument(
        "--dry-run",
        # **`BooleanOptionalAction`, because `store_true` with `default=True` has no off switch.**
        #
        # That is what was here, and it meant `--dry-run` could be passed or omitted and the job
        # planned either way: there was no spelling of this argument that executed anything. The
        # sentence below was true for a reason nobody had noticed, and it made the flag a
        # decoration rather than a control — which is worse than a job that runs by default,
        # because it reads as a deliberate safety and is not one.
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Plan and report without executing. **The default**, and it stays the default: a "
            "job that ran by default would be a job somebody ran by accident. `--no-dry-run` "
            "turns it off, and `scripts/reprocess_submit.py` is the only thing that passes it."
        ),
    )
    arguments = parser.parse_args(argv)

    # **Two ways in, and the offline one is not a simulation of the other.** Files are how
    # `tests/pipelines/` and `evals/scale/` drive the planner with no account at all; the estate
    # arguments are how a real run finds the same three things. Both produce the same plain data
    # and everything after this line is identical, which is the only shape where the tested path
    # and the executed path are the same path.
    sources: dict[str, str] = {}
    if arguments.landing_bucket:
        sources = _sources(arguments.landing_bucket)
        documents = sorted(key for key in sources if key != "__bucket__")
    elif arguments.documents:
        documents = json.loads(arguments.documents.read_text(encoding="utf-8"))
    else:
        parser.error("give --documents (offline) or --landing-bucket (a real run)")

    if arguments.ledger_table:
        ledger = read_ledger(_read_ledger_table(arguments.ledger_table))
    elif arguments.ledger:
        ledger = read_ledger(json.loads(arguments.ledger.read_text(encoding="utf-8")))
    else:
        parser.error("give --ledger (offline) or --ledger-table (a real run)")

    the_plan = plan(documents, ledger, arguments.reader)
    summary = describe(the_plan)
    print(json.dumps(summary, indent=1, sort_keys=True))

    batches = partitions(
        [item for item in the_plan.items if item.disposition is not Disposition.SKIP],
        arguments.partition_size,
    )
    print(f"{len(batches)} partition(s) of at most {arguments.partition_size}")

    if arguments.dry_run:
        print(
            "dry run: nothing executed. This job has never been run against a cluster, and "
            "claim 7 is proved against the planner above rather than against one."
        )
        return 0

    missing = [
        name
        for name, value in (
            ("--landing-bucket", arguments.landing_bucket),
            ("--ledger-table", arguments.ledger_table),
            ("--state-machine", arguments.state_machine),
        )
        if not value
    ]
    if missing:
        # Refused here rather than discovered on an executor. A job that started, spun up
        # workers and then failed on a missing argument has already been billed for the cluster.
        print(f"a real run needs {', '.join(missing)}", file=sys.stderr)
        return 2

    return _execute(  # pragma: no cover - needs a session
        the_plan,
        batches,
        {
            "sources": sources,
            "ledger_table": arguments.ledger_table,
            "state_machine": arguments.state_machine,
        },
    )


def _execute(
    the_plan: Any, batches: list[list[Any]], estate: dict[str, str]
) -> int:  # pragma: no cover
    """The part that needs an account. Everything above it is a pure function of its arguments.

    **The executors start the pipeline; they do not re-implement it.** Each planned document is
    sent through the same state machine an arriving document goes through — read at tier 0,
    thresholded, gated against its own pages, published, landed, indexed. A job that called the
    reader directly would be a second copy of that sequence, in the one place no offline test
    reaches, and every decision it made would be a decision `core` no longer owns.

    **And that is also why the reader is not in this image.** Every threshold here is keyed to
    one reader identity — `tesseract 5.5.0`, from Debian, asserted at build time in
    `Dockerfile`. EMR Serverless custom images are built on Amazon Linux 2023, which carries no
    tesseract at all (`docs/AWS-CONSTRAINTS.md`), so putting the reader on the cluster would mean
    compiling it there: a second build of the binary whose exact build is the unit of evidence.
    The estate has one reader, it runs in one image, and this job calls it.

    The cost of that choice is stated rather than hidden: an executor slot spends most of its
    life waiting on a Step Functions execution rather than computing. For a workload whose unit
    of work is a page of OCR behind a Lambda, the coordinator is what is being distributed.
    """
    from pyspark.sql import SparkSession  # noqa: PLC0415 - only on a cluster

    session = SparkSession.builder.appName("manifest-reprocess").getOrCreate()
    try:
        # Broadcast rather than captured: the map is one entry per document and every executor
        # needs all of it, which is exactly what a broadcast variable is for.
        sources = session.sparkContext.broadcast(estate["sources"])
        machine = estate["state_machine"]

        completed: dict[str, str] = {}
        for batch in batches:
            rows = session.sparkContext.parallelize(batch, numSlices=len(batch))
            for document, version in rows.map(
                lambda item, sources=sources, machine=machine: _read_one(
                    item, sources.value, machine
                )
            ).collect():
                if version:
                    completed[document] = version

        # **Written after the run and only for what finished.** `record` is pure and returns the
        # appended ledger; this is the write. A document that failed carries no version and is
        # absent, so the next run plans it again — which is the whole of the resumability
        # argument in the module docstring.
        appended = record([], the_plan, completed)
        _write_ledger(estate["ledger_table"], appended)
        print(f"{len(completed)}/{sum(len(batch) for batch in batches)} documents completed")
        return 0 if len(completed) == sum(len(batch) for batch in batches) else 1
    finally:
        session.stop()


def _read_one(
    item: Any, sources: dict[str, str], state_machine: str
) -> tuple[str, str]:  # pragma: no cover
    """One document, on an executor: start the pipeline for it and wait for its version.

    Returns `(document, "")` when it did not publish. A failure is not raised, because raising
    would lose the whole partition — and the documents that *did* finish are exactly what the
    ledger needs in order for a resumed run to plan the remainder rather than everything.
    """
    import json as _json  # noqa: PLC0415 - executors import their own
    import time as _time  # noqa: PLC0415

    import boto3  # noqa: PLC0415

    key = sources.get(item.document)
    if not key:
        print(f"{item.document}: no source object; the landing bucket has nothing under that id")
        return item.document, ""

    states = boto3.client("stepfunctions")
    started = states.start_execution(
        stateMachineArn=state_machine,
        input=_json.dumps({"bucket": sources["__bucket__"], "key": key}),
    )

    deadline = _time.time() + EXECUTION_TIMEOUT_SECONDS
    while _time.time() < deadline:
        described = states.describe_execution(executionArn=started["executionArn"])
        if described["status"] == "RUNNING":
            _time.sleep(POLL_SECONDS)
            continue
        if described["status"] != "SUCCEEDED":
            print(f"{item.document}: execution {described['status']}")
            return item.document, ""
        output = _json.loads(described.get("output") or "{}")
        version = ((output.get("extraction") or {}).get("outcome") or {}).get("fingerprint", "")
        return item.document, version

    print(f"{item.document}: still running after {EXECUTION_TIMEOUT_SECONDS}s; not recorded")
    return item.document, ""


def _sources(bucket: str) -> dict[str, str]:  # pragma: no cover
    """`document id -> the landing key it arrived under`, plus the bucket under `__bucket__`.

    Listed rather than reconstructed. The key carries the language and the document type, and
    both are decided by whoever uploaded the object; a job that rebuilt the key from a
    convention would read the wrong page for any document that did not follow it, and would do
    so silently.
    """
    import boto3  # noqa: PLC0415

    found: dict[str, str] = {"__bucket__": bucket}
    pages = boto3.client("s3").get_paginator("list_objects_v2")
    for page in pages.paginate(Bucket=bucket, Prefix="incoming/"):
        for entry in page.get("Contents", ()):
            key = entry["Key"]
            if key.endswith("/"):
                continue
            found[key.rsplit("/", 1)[-1].rsplit(".", 1)[0]] = key
    return found


def _read_ledger_table(table: str) -> list[dict[str, Any]]:  # pragma: no cover
    """The ledger, as rows the pure reader understands."""
    import boto3  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    pages = boto3.client("dynamodb").get_paginator("scan")
    for page in pages.paginate(TableName=table):
        rows.extend(
            {
                "document": entry["document"]["S"],
                "reader": entry["reader"]["S"],
                "version": entry["version"]["S"],
            }
            for entry in page.get("Items", ())
        )
    return rows


def _write_ledger(table: str, entries: list[LedgerEntry]) -> None:  # pragma: no cover
    """Append what completed. One item per document and reader, which is the table's own key.

    `PutItem` rather than a conditional write: the key is `(document, reader)` and the version a
    given reader produces for a given document is deterministic, so writing it twice writes the
    same value. That is what makes a re-run cost a lookup instead of a conflict.
    """
    import boto3  # noqa: PLC0415

    client = boto3.client("dynamodb")
    for entry in entries:
        client.put_item(
            TableName=table,
            Item={
                "document": {"S": entry.document},
                "reader": {"S": entry.reader},
                "version": {"S": entry.version},
            },
        )


if __name__ == "__main__":
    sys.exit(main())
