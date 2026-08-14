#!/usr/bin/env python3
"""Delete the lineage SageMaker writes for an endpoint and never removes.

**The second resource in this estate no `terraform destroy` removes**, and it was found the way
these always are: the teardown reported success and `scripts/estate_sweep.py` refused, listing
fifty actions, fifty contexts and a log group that outlived the endpoint they describe.

SageMaker creates *lineage entities* — actions, contexts, artifacts — automatically when a model
is deployed. Terraform does not manage them, deleting the endpoint does not remove them, and they
accumulate one set per deployment. They are free. That is not the point: *a create path with no
delete path is how an estate gets left standing* is this repository's own sentence, and it does
not carry an exception for resources somebody else created on our behalf.

**Prefix-scoped, and that is the whole safety argument.** This deletes by name prefix and nothing
else. An account holding a sibling project's lineage must come out untouched, which is why there
is no "delete every action" path here even though the API offers one.

    python3 scripts/sagemaker_lineage.py --delete           # remove ours
    python3 scripts/sagemaker_lineage.py                    # list, change nothing

Exits zero when there is nothing named to remove, because a teardown that already succeeded must
not fail on its second run — the same rule `bda_project.py --delete` follows.
"""

from __future__ import annotations

import argparse
import sys

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _client(name: str):
    import boto3  # noqa: PLC0415

    return boto3.client(name)


def _ours(prefix: str) -> tuple[list[str], list[str], list[str]]:
    """Every action, context and log group whose name begins with the project's.

    Artifacts are deliberately **not** collected. They are keyed by the S3 URI of a model
    artefact rather than by a name we choose, so a prefix cannot identify ours — and deleting one
    that belongs to something else is worse than leaving a free record behind.
    """
    sagemaker = _client("sagemaker")
    actions = [
        summary["ActionName"]
        for page in sagemaker.get_paginator("list_actions").paginate()
        for summary in page.get("ActionSummaries", [])
        if summary["ActionName"].startswith(prefix)
    ]
    contexts = [
        summary["ContextName"]
        for page in sagemaker.get_paginator("list_contexts").paginate()
        for summary in page.get("ContextSummaries", [])
        if summary["ContextName"].startswith(prefix)
    ]
    groups = [
        group["logGroupName"]
        for page in _client("logs").get_paginator("describe_log_groups").paginate()
        for group in page.get("logGroups", [])
        if prefix in group["logGroupName"]
    ]
    return actions, contexts, groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument("--delete", action="store_true", help="Remove them. Without it, list.")
    arguments = parser.parse_args(argv)

    try:
        actions, contexts, groups = _ours(arguments.project)
    # Broad on purpose: a teardown must not fail because a listing did. The sweep runs after this
    # and reports anything that survived, which is the check that matters.
    except Exception as error:
        print(f"{RED}could not list lineage: {error}{RESET}", file=sys.stderr)
        return 0

    total = len(actions) + len(contexts) + len(groups)
    if not total:
        print(f"  {GREEN}ok{RESET}    no lineage named {arguments.project}* to remove")
        return 0

    print(
        f"  {len(actions)} action(s), {len(contexts)} context(s), {len(groups)} log group(s) "
        f"named {arguments.project}*"
    )
    if not arguments.delete:
        print(f"  {DIM}listed, nothing removed. Re-run with --delete.{RESET}")
        return 0

    sagemaker, logs = _client("sagemaker"), _client("logs")
    removed = 0
    for name in actions:
        # Each deletion is attempted on its own. One that fails — a race with something else
        # deleting it, a permission this role does not hold — must not strand the rest, because
        # the alternative is a teardown that leaves more behind the more it finds.
        try:
            sagemaker.delete_action(ActionName=name)
            removed += 1
        except Exception as error:
            print(f"  {DIM}action {name}: {error}{RESET}")
    for name in contexts:
        try:
            sagemaker.delete_context(ContextName=name)
            removed += 1
        except Exception as error:
            print(f"  {DIM}context {name}: {error}{RESET}")
    for name in groups:
        try:
            logs.delete_log_group(logGroupName=name)
            removed += 1
        except Exception as error:
            print(f"  {DIM}log group {name}: {error}{RESET}")

    print(f"  {GREEN}ok{RESET}    {removed} of {total} lineage record(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
