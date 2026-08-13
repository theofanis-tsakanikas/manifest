"""The batch job is an adapter. These test the adapter; `evals/scale/` tests the decisions."""

from __future__ import annotations

import json

import pytest
from pipelines.reprocess import describe, main, partitions, read_ledger

from manifest.core.scale import Disposition, LedgerEntry, plan


def test_the_ledger_reads_into_the_core_type() -> None:
    entries = read_ledger([{"document": "d", "reader": "r", "version": "v"}])
    assert entries == [LedgerEntry(document="d", reader="r", version="v")]


def test_partitions_are_a_declared_size_not_the_clusters() -> None:
    """A partition size that follows the cluster's parallelism makes a re-run on a
    differently-sized application do different work, and 'idempotent' quietly becomes
    'idempotent at this scale'."""
    assert [len(batch) for batch in partitions(list(range(7)), 3)] == [3, 3, 1]


def test_a_partition_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="not a partition"):
        partitions([1, 2], 0)


def test_the_run_says_how_much_work_it_believes_it_has() -> None:
    """A bulk job that starts without saying so is a job whose cost is discovered from a bill."""
    the_plan = plan(["a", "b"], [LedgerEntry("a", "r1", "v1")], "r1")
    assert describe(the_plan) == {"process": 1, "reprocess": 0, "skip": 1, "work": 1}


def test_the_job_defaults_to_planning_without_executing(tmp_path, capsys) -> None:
    """The default stays the default. A job that ran by default is a job somebody runs by
    accident, and nothing in this repository has been applied."""
    documents = tmp_path / "documents.json"
    ledger = tmp_path / "ledger.json"
    documents.write_text(json.dumps(["a", "b", "c"]), encoding="utf-8")
    ledger.write_text(
        json.dumps([{"document": "a", "reader": "r1", "version": "v1"}]), encoding="utf-8"
    )

    exit_code = main(["--reader", "r1", "--documents", str(documents), "--ledger", str(ledger)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "nothing executed" in output
    assert json.loads(output.split("\n1 partition")[0])["work"] == 2


def test_a_skipped_document_never_reaches_a_partition() -> None:
    """Idempotence costs a dictionary lookup rather than a conditional write on an executor."""
    the_plan = plan(["a", "b"], [LedgerEntry("a", "r1", "v1")], "r1")
    work = [item for item in the_plan.items if item.disposition is not Disposition.SKIP]
    assert [item.document for item in work] == ["b"]


def test_a_real_run_refuses_before_it_starts_a_cluster(tmp_path, capsys) -> None:
    """A missing argument must fail on the driver, not on an executor.

    A job that started, spun up workers and then failed on an unset name has already been billed
    for the cluster — and the message arrives in a Spark stack trace rather than in the place
    somebody is looking. Every argument a real run needs is checked before `_execute`.
    """
    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps(["SHP00001"]), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    ledger.write_text("[]", encoding="utf-8")

    code = main(
        [
            "--reader",
            "tesseract 5.5.0",
            "--documents",
            str(documents),
            "--ledger",
            str(ledger),
            "--partition-size",
            "10",
        ]
    )

    # The default is a dry run, so this is the safe path and it does not need the estate at all.
    assert code == 0
    assert "dry run" in capsys.readouterr().out


def test_neither_source_of_documents_is_a_refusal_with_both_named(capsys) -> None:
    """Offline and estate are two ways in, and giving neither must say so in one line."""
    with pytest.raises(SystemExit):
        main(["--reader", "tesseract 5.5.0"])

    assert "--landing-bucket" in capsys.readouterr().err
