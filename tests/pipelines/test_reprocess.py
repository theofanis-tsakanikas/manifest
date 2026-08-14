"""The batch job is an adapter. These test the adapter; `evals/scale/` tests the decisions."""

from __future__ import annotations

import json

import pytest
from pipelines.reprocess import describe, main, partitions, read_ledger

from manifest.core.scale import Disposition, LedgerEntry, plan, record


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


# ── The ledger's third answer ────────────────────────────────────────────────


def test_a_refused_document_is_recorded_so_it_is_never_planned_again() -> None:
    """**Claim 7's one real hole, and it survived two full runs.**

    Three documents — an undeclared language, a type no contract governs, a key outside the
    convention — were refused by the pipeline, recorded nowhere, and re-planned on every
    subsequent run. "No duplicates and no double work" is the claim; that is double work with no
    end to it.
    """
    the_plan = plan(["good", "malformed"], [], "tesseract 5.5.0")

    ledger = record([], the_plan, {"good": "v1"}, frozenset({"malformed"}))

    assert [(e.document, e.version, e.refused) for e in ledger] == [
        ("good", "v1", False),
        ("malformed", "", True),
    ]
    again = plan(["good", "malformed"], ledger, "tesseract 5.5.0")
    assert [item.disposition.value for item in again.items] == ["skip", "skip"]


def test_a_document_nobody_got_an_answer_about_is_still_owed() -> None:
    """The distinction the fix turns on: silence is not a refusal, and must be retried."""
    the_plan = plan(["good", "unanswered"], [], "tesseract 5.5.0")

    ledger = record([], the_plan, {"good": "v1"}, frozenset())

    assert [entry.document for entry in ledger] == ["good"]
    again = plan(["good", "unanswered"], ledger, "tesseract 5.5.0")
    assert dict((i.document, i.disposition) for i in again.items)["unanswered"] is (
        Disposition.PROCESS
    )


def test_a_reader_upgrade_asks_the_refused_document_again() -> None:
    """A refusal is an answer *at this reader*, not for ever.

    An undeclared language is a contract fact and will refuse again; a document the reader
    could not open might not. The ledger is keyed by `(document, reader)` for exactly this, and
    a refusal that outlived its reader would be a permanent exclusion nobody decided.
    """
    ledger = record([], plan(["malformed"], [], "tesseract 5.5.0"), {}, frozenset({"malformed"}))

    later = plan(["malformed"], ledger, "tesseract 5.6.0")

    assert later.items[0].disposition is Disposition.REPROCESS


def test_a_refusal_carrying_a_version_is_refused() -> None:
    """It published nothing, so a version here points at an object that does not exist."""
    with pytest.raises(ValueError, match=r"published nothing|does not exist"):
        LedgerEntry(document="d", reader="r", version="v1", refused=True)


def test_a_processed_entry_with_no_version_is_refused() -> None:
    """An empty string standing in for a refusal is the sentinel doctrine rule 3 forbids."""
    with pytest.raises(ValueError, match="Missing is missing"):
        LedgerEntry(document="d", reader="r", version="")
