#!/usr/bin/env python3
"""Refuse an apply that would destroy something nobody asked it to destroy.

**The failure this exists for, which happened on 2026-08-14.** `deploy.yml` takes five boolean
feature inputs — escalation tiers, search, the classifier, batch, analytics — and every one of
them defaults to `false`. That default is correct: a dispatch should not silently bill for an
OpenSearch collection because somebody forgot to say no.

What it also does is silently **tear down** what a previous dispatch built. A deploy sent to fix
one line in a state machine, with four flags set and the fifth forgotten, planned `0 to add, 8 to
change, 6 to destroy` and removed the search collection, its policies and its VPC endpoint. The
run was green. Terraform did exactly what it was told. The estate lost a component and nothing
said so, because *omitting* an input and *asking for* its absence are the same request.

Doctrine rule 5 is *nothing approves itself*, and this is its neighbour: a deploy may build, and
it may change, and it may not quietly demolish. So a plan carrying deletions stops here unless
the dispatch said `accept_destroys`, and the refusal **names every resource** — because "6 to
destroy" is a number and `aws_opensearchserverless_collection.records` is a decision.

Reads the plan as JSON rather than parsing Terraform's prose. `docs/DECISIONS.md` 24: parse the
thing, do not match its shape — the human-readable output is formatting, and formatting changes.

    terraform plan -out=tfplan ...
    terraform show -json tfplan > plan.json
    python3 scripts/check_plan_destroys.py plan.json          # refuses on any delete
    python3 scripts/check_plan_destroys.py plan.json --accept  # allowed, and still printed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Terraform's own words for what it intends to do with a resource. A replacement arrives as the
#: pair `["delete", "create"]` (or `["create", "delete"]` for `create_before_destroy`), and it is
#: a deletion: the resource that exists now stops existing. A collection replaced is a collection
#: whose contents are gone, whatever the count of resources afterwards says.
DELETE = "delete"


def _planned(document: dict) -> list[tuple[str, list[str]]]:
    """Every resource the plan would delete, with the actions that say so."""
    going: list[tuple[str, list[str]]] = []
    for change in document.get("resource_changes", []):
        actions = list(change.get("change", {}).get("actions", []))
        if DELETE in actions:
            going.append((str(change.get("address", "?")), actions))
    return sorted(going)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="`terraform show -json tfplan` output.")
    parser.add_argument(
        "--accept",
        action="store_true",
        help="The dispatch asked for the deletions. They are still printed.",
    )
    parser.add_argument("--layer", default="", help="Named in the refusal, to say which state.")
    arguments = parser.parse_args(argv)

    if not arguments.plan.exists():
        # Refused rather than treated as a clean plan. A missing plan file is this check having
        # nothing to read, and a check that answers "nothing to destroy" when it read nothing is
        # the vacuous pass `docs/DECISIONS.md` 24 is a list of.
        print(f"{RED}{arguments.plan} does not exist{RESET}", file=sys.stderr)
        return 1

    document = json.loads(arguments.plan.read_text(encoding="utf-8"))
    going = _planned(document)
    where = f" in {arguments.layer}" if arguments.layer else ""

    if not going:
        print(f"  {GREEN}ok{RESET}    the plan{where} destroys nothing")
        return 0

    for address, actions in going:
        print(f"  {'→'.join(actions):>15}  {address}")

    if arguments.accept:
        print(
            f"\n  {DIM}{len(going)} resource(s){where} will be destroyed, and the dispatch asked "
            f"for it. Listed rather than counted: a teardown somebody accepted is still a "
            f"teardown somebody should be able to read.{RESET}"
        )
        return 0

    print(
        f"\n{RED}the plan{where} would destroy {len(going)} resource(s) and the dispatch did not "
        f"ask for it{RESET}\n\n"
        f"  Every feature flag on this workflow defaults to `false`, which is the right default "
        f"for a bill and the wrong one for an estate that is already standing: omitting an input "
        f"and asking for its absence are the same request, and one of them is an accident. If "
        f"these deletions are intended — switching a feature off, or shrinking the estate — "
        f"dispatch again with `accept_destroys: true` and they will run, listed as above.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
