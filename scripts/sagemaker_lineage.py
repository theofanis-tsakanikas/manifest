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


def _ours(prefix: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Every action, context and log group whose name begins with the project's, and what failed.

    Artifacts are deliberately **not** collected. They are keyed by the S3 URI of a model
    artefact rather than by a name we choose, so a prefix cannot identify ours — and deleting one
    that belongs to something else is worse than leaving a free record behind.

    **Each of the three is collected on its own**, because they used to share a failure. The
    teardown of 2026-08-15 was refused `sagemaker:ListActions`, the first call raised, and the
    other two never ran — so a single missing verb hid a log group the role could have deleted
    all along. Three listings that fail together are one listing wearing three names.
    """
    sagemaker, logs = _client("sagemaker"), _client("logs")
    found: dict[str, list[str]] = {"actions": [], "contexts": [], "groups": []}
    refused: list[str] = []

    def collect(kind: str, call) -> None:
        try:
            found[kind] = sorted(call())
        # Broad on purpose: one listing that fails must not stop the other two from running,
        # and every failure is carried out in `refused` rather than swallowed.
        except Exception as error:
            refused.append(f"{kind}: {error}")

    # The ARN travels with the name because deleting one of these takes both: the name names it,
    # and the association API — which has to run first — speaks only ARNs.
    collect(
        "actions",
        lambda: [
            f"{summary['ActionName']}\t{summary['ActionArn']}"
            for page in sagemaker.get_paginator("list_actions").paginate()
            for summary in page.get("ActionSummaries", [])
            if summary["ActionName"].startswith(prefix)
        ],
    )
    collect(
        "contexts",
        lambda: [
            f"{summary['ContextName']}\t{summary['ContextArn']}"
            for page in sagemaker.get_paginator("list_contexts").paginate()
            for summary in page.get("ContextSummaries", [])
            if summary["ContextName"].startswith(prefix)
        ],
    )
    collect(
        "groups",
        lambda: [
            group["logGroupName"]
            for page in logs.get_paginator("describe_log_groups").paginate()
            for group in page.get("logGroups", [])
            if prefix in group["logGroupName"]
        ],
    )
    return found["actions"], found["contexts"], found["groups"], refused


def _cut_the_edges(sagemaker, entities: list[str]) -> None:
    """Remove every association incident to one of these entities.

    **Lineage is a graph, and a node with edges will not delete.** `DeleteAction` on an entity
    that is still associated with anything answers `ValidationException: Cannot delete entity
    with associations` — which is what the teardown of 2026-08-15 hit the moment the permission
    was fixed and it could finally try. Thirty-three entities, every deletion refused.

    Both directions are asked for, because an association is directed and ours may sit at either
    end: a context is the *source* of the actions it groups, and an action is the *source* of the
    artifact it produced. Deduplicated, because an edge whose two ends are both ours comes back
    once from each.

    **Deleting an edge does not delete what is on the end of it.** Artifacts stay exactly as they
    were — which is the whole reason `_ours` refuses to collect them, and why the `artifact/*`
    reach in `CutTheEdgesTouchingThisProjectsLineage` is acceptable. Enumeration starts from our
    prefixed entities, so no edge is touched that does not have one of ours at one end.

    Failures are printed and not raised. An edge that will not come off surfaces immediately
    afterwards as an entity that will not delete, and *that* is what returns non-zero — one
    failure reported once, at the point where it means something.
    """
    edges: set[tuple[str, str]] = set()
    for entity in entities:
        arn = entity.split("\t")[1]
        for direction in ("SourceArn", "DestinationArn"):
            try:
                for page in sagemaker.get_paginator("list_associations").paginate(
                    **{direction: arn}
                ):
                    for summary in page.get("AssociationSummaries", []):
                        edges.add((summary["SourceArn"], summary["DestinationArn"]))
            except Exception as error:
                print(f"  {DIM}associations of {arn} ({direction}): {error}{RESET}")

    if not edges:
        return
    cut = 0
    for source, destination in sorted(edges):
        try:
            sagemaker.delete_association(SourceArn=source, DestinationArn=destination)
            cut += 1
        except Exception as error:
            print(f"  {DIM}association {source} -> {destination}: {error}{RESET}")
    print(f"  {cut} of {len(edges)} association(s) cut")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument("--delete", action="store_true", help="Remove them. Without it, list.")
    arguments = parser.parse_args(argv)

    actions, contexts, groups, refused = _ours(arguments.project)

    # **A listing that was refused is not a listing that found nothing**, and the first version
    # of this file reported the two identically. It caught every exception, printed to stderr and
    # returned zero — so on 2026-08-15 the step printed `AccessDeniedException`, went green, and
    # thirty-three lineage entities survived a teardown that reported success. Only the estate
    # sweep three jobs later noticed.
    #
    # The old justification was that a teardown must not fail because a listing did. That is
    # right about the *teardown* and wrong about the *report*: every job downstream of this one
    # runs `if: always()`, so the layers still come down. What a non-zero exit costs is a green
    # tick nobody should have had. Doctrine rule 3 — a default is a lie with a plausible shape —
    # and "0 lineage records found" from a call that never returned is exactly that shape.
    if refused:
        for failure in refused:
            print(f"  {RED}could not list {failure}{RESET}", file=sys.stderr)
        print(
            f"{RED}Lineage was not checked. This is not the same as finding none.{RESET}",
            file=sys.stderr,
        )
        return 1

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
    _cut_the_edges(sagemaker, [*actions, *contexts])

    removed = 0
    for entity in actions:
        # Each deletion is attempted on its own. One that fails — a race with something else
        # deleting it, a permission this role does not hold — must not strand the rest, because
        # the alternative is a teardown that leaves more behind the more it finds.
        name = entity.split("\t")[0]
        try:
            sagemaker.delete_action(ActionName=name)
            removed += 1
        except Exception as error:
            print(f"  {DIM}action {name}: {error}{RESET}")
    for entity in contexts:
        name = entity.split("\t")[0]
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

    if removed != total:
        # Same rule as the refused listing above: the sweep would catch these anyway, three jobs
        # later and with no indication of which step was supposed to have removed them.
        print(f"  {RED}only {removed} of {total} lineage record(s) removed{RESET}", file=sys.stderr)
        return 1

    print(f"  {GREEN}ok{RESET}    {removed} of {total} lineage record(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
