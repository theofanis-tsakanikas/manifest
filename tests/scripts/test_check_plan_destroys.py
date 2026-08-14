"""The deploy's teardown guard, attacked with the plan shapes it has to tell apart.

Written because the guard was wrong the first time it ran in anger: it refused two Lake Formation
grants that Terraform replaces to edit, on a deploy that was tearing nothing down. The distinction
between a delete and a replacement is the whole of this check, so it is the whole of these tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "check_plan_destroys", ROOT / "scripts" / "check_plan_destroys.py"
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_plan_destroys"] = guard
_spec.loader.exec_module(guard)


def _plan(*changes: tuple[str, list[str]]) -> dict[str, Any]:
    return {
        "resource_changes": [
            {"address": address, "change": {"actions": actions}} for address, actions in changes
        ]
    }


def _written(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_plan_that_only_creates_and_changes_is_allowed(tmp_path: Path) -> None:
    plan = _plan(("aws_s3_bucket.lake", ["create"]), ("aws_glue_catalog_table.v", ["update"]))

    assert guard.main([str(_written(tmp_path, plan))]) == 0


def test_a_pure_delete_is_refused(tmp_path: Path) -> None:
    """The failure this exists for: a feature flag went from on to off and nobody said so."""
    plan = _plan(("aws_opensearchserverless_collection.records[0]", ["delete"]))

    assert guard.main([str(_written(tmp_path, plan))]) == 1


def test_a_replacement_is_not_a_teardown(tmp_path: Path) -> None:
    """`["delete", "create"]` is how Terraform edits a resource it cannot change in place.

    Refusing this made the check fire on an apply that was tearing nothing down — and a gate that
    fires on ordinary work is a gate somebody keeps the override flag on for.
    """
    plan = _plan(("aws_lakeformation_permissions.land_writes_the_table", ["delete", "create"]))

    assert guard.main([str(_written(tmp_path, plan))]) == 0


def test_create_before_destroy_is_a_replacement_too(tmp_path: Path) -> None:
    """The same edit with the actions the other way round. Order is a lifecycle choice."""
    plan = _plan(("aws_lambda_function.publish", ["create", "delete"]))

    assert guard.main([str(_written(tmp_path, plan))]) == 0


def test_a_delete_beside_a_replacement_is_still_refused(tmp_path: Path) -> None:
    """The mixed plan, which is what a real one looks like — one accident among ordinary work."""
    plan = _plan(
        ("aws_lakeformation_permissions.land_writes_the_table", ["delete", "create"]),
        ("aws_opensearchserverless_vpc_endpoint.records[0]", ["delete"]),
        ("aws_s3_bucket.lake", ["update"]),
    )

    assert guard.main([str(_written(tmp_path, plan))]) == 1


def test_the_deletions_run_when_the_dispatch_asked_for_them(tmp_path: Path) -> None:
    plan = _plan(("aws_opensearchserverless_collection.records[0]", ["delete"]))

    assert guard.main([str(_written(tmp_path, plan)), "--accept"]) == 0


def test_every_deletion_is_named_rather_than_counted(tmp_path: Path, capsys) -> None:
    """`6 to destroy` is a number; the collection's address is a decision."""
    plan = _plan(
        ("aws_opensearchserverless_collection.records[0]", ["delete"]),
        ('aws_ssm_parameter.published["search_endpoint"]', ["delete"]),
    )

    guard.main([str(_written(tmp_path, plan))])

    printed = capsys.readouterr()
    assert "aws_opensearchserverless_collection.records[0]" in printed.out
    assert "search_endpoint" in printed.out


def test_a_missing_plan_is_refused_rather_than_read_as_clean(tmp_path: Path) -> None:
    """A check answering 'nothing to destroy' when it read nothing is decision 24's whole list."""
    assert guard.main([str(tmp_path / "nothing.json")]) == 1


def test_a_plan_with_no_changes_at_all_is_allowed(tmp_path: Path) -> None:
    assert guard.main([str(_written(tmp_path, {"resource_changes": []}))]) == 0


def test_the_layer_is_named_in_the_refusal(tmp_path: Path, capsys) -> None:
    """Which state this is about. Five layers deploy in sequence and the log is one stream."""
    plan = _plan(("aws_sfn_state_machine.pipeline", ["delete"]))

    guard.main([str(_written(tmp_path, plan)), "--layer", "extraction"])

    assert "in extraction" in capsys.readouterr().err
