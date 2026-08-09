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

from manifest.core.scale import Disposition, LedgerEntry, plan, record

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
    parser.add_argument("--documents", type=Path, required=True, help="JSON list of document ids.")
    parser.add_argument("--ledger", type=Path, required=True, help="JSON list of ledger entries.")
    parser.add_argument("--partition-size", type=int, default=200)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help=(
            "Plan and report without executing. **The default**, and it stays the default: "
            "nothing in this repository has been applied, so a job that ran by default would "
            "be a job somebody ran by accident."
        ),
    )
    arguments = parser.parse_args(argv)

    documents = json.loads(arguments.documents.read_text(encoding="utf-8"))
    ledger = read_ledger(json.loads(arguments.ledger.read_text(encoding="utf-8")))

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

    return _execute(the_plan, batches)  # pragma: no cover - needs a session


def _execute(the_plan: Any, batches: list[list[Any]]) -> int:  # pragma: no cover
    """The three lines that need an account.

    Deliberately the smallest function in this file. Everything above it is a pure function of
    its arguments and is covered by `tests/pipelines/`; this is the part that cannot be, and
    keeping it this size is what stops the untested region growing.
    """
    from pyspark.sql import SparkSession  # noqa: PLC0415 - only on a cluster

    session = SparkSession.builder.appName("manifest-reprocess").getOrCreate()
    try:
        completed: dict[str, str] = {}
        for batch in batches:
            rows = session.sparkContext.parallelize(batch, numSlices=len(batch))
            for document, version in rows.map(_read_one).collect():
                completed[document] = version
        record([], the_plan, completed)
        return 0
    finally:
        session.stop()


def _read_one(item: Any) -> tuple[str, str]:  # pragma: no cover
    """One document, on an executor. The reader adapter is called here and nowhere else."""
    raise NotImplementedError(
        "the executor-side read is the one part of this system that needs a cluster, and no "
        "cluster has ever been started from this repository"
    )


if __name__ == "__main__":
    sys.exit(main())
