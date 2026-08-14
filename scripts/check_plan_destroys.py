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

**Deletions, not replacements**, and the first run of this check got that wrong — see `_planned`.
A resource Terraform must replace is being edited, not torn down, and refusing those would make
this fire on ordinary applies until somebody kept the override flag on permanently.

    terraform plan -out=tfplan ...
    terraform show -json tfplan > plan.json
    python3 scripts/check_plan_destroys.py plan.json          # refuses a pure delete
    python3 scripts/check_plan_destroys.py plan.json --accept  # allowed, and still printed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Terraform's own words for what it intends to do with a resource.
DELETE = "delete"

#: A resource that is deleted and not replaced. **This is what the check is about**, and the
#: distinction is not pedantry: the teardown that prompted it was five pure deletes, because a
#: feature flag went from on to off and the resources simply stopped being in the configuration.
GOING = [DELETE]


def _planned(document: dict) -> tuple[list[str], list[str]]:
    """What the plan deletes outright, and what it replaces. Two different facts.

    **The first run of this check refused a replacement, and was wrong to.** A replacement arrives
    as `["delete", "create"]` — Terraform's way of saying a resource has an argument it cannot
    change in place — and two Lake Formation grants had one. Nothing was being torn down; a grant
    was being re-issued, which is how that resource type is edited at all.

    Treating the two the same would make this check fire on ordinary applies, and a gate that
    fires on ordinary work is a gate people learn to pass with the override flag. So a pure
    delete refuses and a replacement is printed.

    What that gives up, stated rather than left to be discovered: a replaced resource that holds
    *data* loses it, and this will not stop that. The honest scope is deletions, and an estate
    whose data-bearing resources can be replaced in place of destroyed is a separate control —
    `prevent_destroy` on the resource, which is where Terraform puts it.
    """
    deleted, replaced = [], []
    for change in document.get("resource_changes", []):
        actions = list(change.get("change", {}).get("actions", []))
        address = str(change.get("address", "?"))
        if actions == GOING:
            deleted.append(address)
        elif DELETE in actions:
            replaced.append(address)
    return sorted(deleted), sorted(replaced)


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
    going, replaced = _planned(document)
    where = f" in {arguments.layer}" if arguments.layer else ""

    for address in replaced:
        print(f"  {DIM}replaced{RESET}  {address}")

    if not going:
        print(
            f"  {GREEN}ok{RESET}    the plan{where} deletes nothing"
            + (f", and replaces {len(replaced)}" if replaced else "")
        )
        return 0

    for address in going:
        print(f"  {RED}deleted{RESET}   {address}")

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
