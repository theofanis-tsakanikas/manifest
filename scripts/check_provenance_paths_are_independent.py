#!/usr/bin/env python3
"""The provenance verifier may not import the module that produced the record it checks.

**ADR-0003 says "independence is enforced by a gate, not intended", and declares this file. It
was never written.** The property held anyway — `gates/provenance.py` imports `core.geometry`,
`core.text` and `core.checkdigit` and nothing else — but it held by nobody having broken it,
which is the difference between a guarantee and a coincidence. One `from manifest.core.fields
import ...` added for a convenience, and claim 2's second check becomes the field assembler
agreeing with itself: the same anchor logic, over the same words, reaching the same answer, and
reporting it as independent corroboration. Every test in this repository would still pass.

Claim 2 says three checks of **declared and unequal strength**. The re-read is the weakest of
them and its whole value is *whose* reading it is. So the boundary is read out of the import
graph, transitively — a breach one module deep is a breach — and named with the chain that
reaches it, because "provenance imports fields" is easy to see and "provenance imports text
imports fields" is not.

    python3 scripts/check_provenance_paths_are_independent.py
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: The verifier, and everything it is allowed to reach.
VERIFIER = "manifest.gates.provenance"

#: What it may not reach, and why each one would make the re-read circular. Data rather than a
#: bare list, because a gate whose refusal cannot be explained is a gate somebody deletes.
FORBIDDEN: dict[str, str] = {
    "manifest.core.fields": (
        "the field assembler — it decides which words under which caption become the value. A "
        "verifier that reuses it re-runs the decision it is supposed to be auditing"
    ),
    "manifest.core.cascade": (
        "the routing rule that chose the engine. The re-read's independence is from the *path*, "
        "and this module is the path"
    ),
    "manifest.core.calibration": (
        "the thresholds. A verifier that knows what was published is a verifier that can agree "
        "with it for the wrong reason"
    ),
    "manifest.extraction": (
        "the engine adapters. The re-read runs a recognition path, and it must be reached "
        "through the caller rather than imported here — otherwise the gate names its engine, "
        "which is also what core purity forbids"
    ),
}


def _module_path(module: str) -> pathlib.Path | None:
    candidate = SOURCE / (module.replace(".", "/") + ".py")
    if candidate.exists():
        return candidate
    package = SOURCE / module.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def _imports(path: pathlib.Path) -> set[str]:
    """Every `manifest.*` module this file imports, by absolute name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name.startswith("manifest")}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith("manifest"):
                continue
            found.add(node.module)
            # `from manifest.core import fields` names the module in the alias, not the module.
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _breach(module: str) -> str | None:
    """The forbidden ancestor this module name falls under, if any."""
    return next(
        (
            forbidden
            for forbidden in FORBIDDEN
            if module == forbidden or module.startswith(forbidden + ".")
        ),
        None,
    )


def main() -> int:
    start = _module_path(VERIFIER)
    if start is None:
        # **An absent input, not a clean result.** A renamed verifier would otherwise walk an
        # empty graph and report independence about a file that is not there.
        print(f"{RED}  {VERIFIER} does not exist — this check has nothing to read{RESET}")
        return 1

    #: module → the chain that reached it, so a two-hop breach is reported as two hops.
    reached: dict[str, list[str]] = {VERIFIER: [VERIFIER]}
    queue = [VERIFIER]
    problems: list[str] = []
    #: `from manifest.core import fields` yields both `manifest.core` and `manifest.core.fields`,
    #: so one import can breach twice. Reported once per (chain, boundary): a reader fixing it
    #: has one line to delete, and two findings for one line reads as two problems.
    already: set[tuple[str, str]] = set()

    while queue:
        current = queue.pop(0)
        path = _module_path(current)
        if path is None:
            continue
        for imported in sorted(_imports(path)):
            forbidden = _breach(imported)
            chain = [*reached[current], imported]
            if forbidden is not None:
                signature = (" → ".join(chain[:-1]), forbidden)
                if signature not in already:
                    already.add(signature)
                    problems.append(
                        f"{' → '.join([*chain[:-1], forbidden])}\n"
                        f"        {forbidden} is {FORBIDDEN[forbidden]}"
                    )
                continue
            if imported not in reached and _module_path(imported) is not None:
                reached[imported] = chain
                queue.append(imported)

    walked = len(reached)
    if problems:
        print(f"{RED}  the provenance verifier reaches the path it is meant to be independent of")
        for problem in problems:
            print(f"      · {problem}")
        print(f"{RESET}")
        print(
            f"{DIM}  Claim 2's re-read is worth exactly as much as the independence of the "
            f"reader doing it. See docs/adr/0003.{RESET}"
        )
        return 1

    print(f"provenance independence: {walked} module(s) walked from {VERIFIER}")
    print(f"  {GREEN}ok{RESET}    reaches none of: {', '.join(sorted(FORBIDDEN))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
