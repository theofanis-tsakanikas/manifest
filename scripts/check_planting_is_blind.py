#!/usr/bin/env python3
"""The corpus plants mismatches without knowing which rules will find them.

Claim 4 says: on a corpus with N planted mismatches, exactly N are found, with zero false
positives on the set that agrees. That sentence is worth nothing if the planter read
`contracts/reconciliation/` to decide what to break and the detector reads the same file to
find it. It would be one function agreeing with itself, it would report green forever, and it
is the trap `PLAN.md` names.

So the independence is structural and it is checked here, by reading the import graph rather
than by trusting a convention:

**`corpus/plant.py` and `corpus/world.py` may not reach `manifest.contracts`.** They name what
they changed in the vocabulary of *shipment facts* — a gross weight, a container, a package
count — and they do not know that any rule compares those across documents.

**Neither may they reach the reconciliation code.** Whatever finds a disagreement is
downstream, and a planter that could call it could be tuned until it agreed.

This is Watermark's ADR-0004 lesson applied one project later: independence that is intended
gets deleted by the first refactor that notices the duplication. Independence that is a gate
survives it.

Its mutation is in `scripts/gate_proof.py`, in the same commit.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The modules that plant. If the corpus grows another, add it here — and note that forgetting
#: to is the failure this script cannot catch, which is why the list is short and the files are
#: named for what they do.
PLANTERS = ("corpus/plant.py", "corpus/world.py")

#: What a planter may not import, and why each one would break the claim.
FORBIDDEN: dict[str, str] = {
    "manifest.contracts": (
        "the reconciliation contract declares which fields must agree. A planter that reads it "
        "is choosing its mismatches to match the rules that will find them, and claim 4 becomes "
        "one function agreeing with itself"
    ),
    "manifest.reconciliation": (
        "whatever finds a disagreement is downstream of the planting. A planter that can call "
        "it can be tuned until it agrees"
    ),
    "evals": (
        "the eval computes the expected findings. A planter importing it closes the loop from "
        "the other end"
    ),
}


def violations() -> list[str]:
    found: list[str] = []
    for relative in PLANTERS:
        path = ROOT / relative
        if not path.exists():
            found.append(f"{relative} does not exist; the gate is checking nothing")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                modules = [(alias.name, node.lineno) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [(node.module, node.lineno)]
            for module, line in modules:
                for forbidden, reason in FORBIDDEN.items():
                    if module == forbidden or module.startswith(f"{forbidden}."):
                        found.append(f"{relative}:{line} imports `{module}` — {reason}")
    return found


def main() -> int:
    found = violations()
    if not found:
        print(
            "planting-is-blind: the corpus plants mismatches in the vocabulary of shipment "
            "facts, and knows nothing about the rules that will find them"
        )
        return 0
    print(f"planting-is-blind: {len(found)} violation(s)\n", file=sys.stderr)
    for violation in found:
        print(f"  {violation}", file=sys.stderr)
    print(
        "\nClaim 4 is that planted disagreements are found by an independent path. Every line "
        "above is that independence being given up, and the harness would keep reporting green "
        "afterwards — which is worse than it reporting red.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
