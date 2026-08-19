#!/usr/bin/env python3
"""This repository describes itself truthfully: the tree it documents, and the code it runs.

Two questions, both about the same failure — a document that was true when it was written.

**Does the documented layout exist?** `CLAUDE.md` opens with *read this first, every session*, and
its repository tree listed four packages that do not exist — `cascade/`, `entities/`, `review/`,
`versioning/` — because those decisions live in `core/`, which is where the engine-free rule puts
them. It also omitted `security/`, which does exist, and described `handlers/` as *the three
functions that run in the estate* when there are twelve. Every one of those was true once. None
of them was true today, and nothing noticed, because the map and the ground were never compared.

**Does every decision the core makes have a caller that runs?** `core/` is where this system's
decisions live. A module there that nothing in `handlers/`, `gates/` or `pipelines/` can reach is
a decision this repository *proved* and does not *make* — true of a pure function, untested as a
property of the running system, and indistinguishable from a finished feature in every document
that mentions it.

That is not hypothetical. `core/reconciliation.py`, `core/review.py` and `core/entities.py` were
each in exactly that state until the day handlers were written for them, and claims 4, 5 and 6
were being scored offline against a system that had no path to any of it. This check exists so
the next one is found by CI rather than by an audit.

**A module may be reachable without being imported**, and `core/calibration.py` is: the deploy
renders its output into a thresholds artefact that the extraction handler reads. That is a real
path and an import graph cannot see it, so it is declared in `contracts/core/reachable.yaml` —
with the route named and a date, because doctrine rule 6 applies to tooling too.

Parses the tree with `ast` rather than grepping for import lines. `docs/DECISIONS.md` 24.
"""

from __future__ import annotations

import ast
import collections
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "CLAUDE.md"
PACKAGE = ROOT / "src" / "manifest"
ACCEPTANCE = ROOT / "contracts" / "core" / "reachable.yaml"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Where the running system starts. A `core` module reachable from one of these is a decision the
#: estate can actually make; everything else is reachable only from something that scores it.
#:
#: `evals/` and `scripts/gate_proof.py` are deliberately **not** roots. They are how a claim is
#: proved, and a module they alone import is exactly the thing this check is looking for: proved,
#: and never asked.
RUNNING = ("src/manifest/handlers", "src/manifest/gates", "pipelines/")

#: Directories under `src/manifest/` that hold no decisions and need no entry in the tree.
NOT_LAYOUT = {"__pycache__"}


def _documented_packages() -> set[str]:
    """Every `src/manifest/<name>/` the guide's repository tree names.

    Anchored to the start of a line and to the tree's own drawing characters. A looser pattern
    matched `s`, `t`, `u`, `v`, `x` and `y` — the branch returns tuples, the caller iterated
    them, and iterating a *string* yields characters. It reported six missing packages named
    after single letters, which is at least a failure that announces itself; the same mistake
    one step quieter would have reported none.
    """
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index("├── src/manifest/")
    end = text.index("├── evals/", start)
    return set(re.findall(r"^│   [├└]── (\w+)/", text[start:end], re.MULTILINE))


def _real_packages() -> set[str]:
    return {
        child.name for child in PACKAGE.iterdir() if child.is_dir() and child.name not in NOT_LAYOUT
    }


def _imports() -> dict[str, set[str]]:
    """`file -> the manifest modules it imports`, from the syntax tree."""
    edges: dict[str, set[str]] = collections.defaultdict(set)
    for path in _our_own_python():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        # A file this cannot parse is not silently skipped as "imports nothing" — it is reported,
        # because a parser that stopped reading is the vacuous pass decision 24 is a list of.
        except SyntaxError as error:
            raise SystemExit(f"{path.relative_to(ROOT)} does not parse: {error}") from error
        here = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "manifest" in node.module:
                edges[here].add(node.module)
            elif isinstance(node, ast.Import):
                edges[here].update(a.name for a in node.names if "manifest" in a.name)
    return edges


#: The directories this repository authors. Everything else on disk — a virtualenv, a vendored
#: package, a build tree — is somebody else's code, and reading it is how a check ends up
#: reporting a syntax warning from `networkx` as if this repository had written it.
OURS = ("src", "tests", "evals", "scripts", "pipelines", "corpus")


def _our_own_python() -> list[Path]:
    return [
        path
        for top in OURS
        for path in (ROOT / top).rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _module_files() -> dict[str, str]:
    """`manifest.core.review -> src/manifest/core/review.py`."""
    found = {}
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(ROOT / "src").with_suffix("").parts
        found[".".join(parts)] = str(path.relative_to(ROOT))
        if path.name == "__init__.py":
            found[".".join(parts[:-1])] = str(path.relative_to(ROOT))
    return found


def _reachable() -> set[str]:
    edges, files = _imports(), _module_files()
    seen: set[str] = set()
    stack = [name for name in edges if name.startswith(RUNNING)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in edges.get(current, ()):
            path = files.get(target)
            if path and path not in seen:
                stack.append(path)
    return seen


def _accepted() -> dict[str, str]:
    """Modules that reach the estate by a route no import graph can see.

    The route must be named. "It is used" is not a route; "the deploy renders its output into an
    artefact the handler reads" is one, and somebody can go and check it.
    """
    if not ACCEPTANCE.exists():
        return {}
    loaded = yaml.safe_load(ACCEPTANCE.read_text(encoding="utf-8")) or {}
    return dict(loaded.get("reaches_the_estate") or {})


def main() -> int:
    problems: list[str] = []

    documented, real = _documented_packages(), _real_packages()
    for missing in sorted(documented - real):
        problems.append(
            f"CLAUDE.md's tree names `src/manifest/{missing}/` and it does not exist. The guide "
            f"is the first thing anybody reads; a package in it that is not on disk sends the "
            f"reader looking for decisions somewhere they are not made"
        )
    for undocumented in sorted(real - documented):
        problems.append(
            f"`src/manifest/{undocumented}/` exists and CLAUDE.md's tree does not name it. A "
            f"package nobody documented is a package nobody reviews"
        )

    reachable, accepted = _reachable(), _accepted()

    # **`security/` is walked for the same reason `core/` is, and it was not.**
    #
    # `security/injection.py` is the control `CLAUDE.md` and `SECURITY.md` both point a reader at
    # for untrusted document text, and nothing in the running system calls it: the escalation
    # handler sends the page as an image under its own prompt, which is a genuine structural
    # fence and a different one. So the module is proved by `evals/injection` and asked by
    # nobody — the exact state `core/reconciliation.py` was in while claim 4 was being scored.
    #
    # It is not a hole today. It is a control whose absence of a caller nothing was checking,
    # which is how it stays absent when the text path finally arrives.
    orphans = []
    for package in ("core", "security"):
        for module in sorted(PACKAGE.glob(f"{package}/*.py")):
            if module.name == "__init__.py":
                continue
            name = module.stem
            if str(module.relative_to(ROOT)) in reachable:
                continue
            if name in accepted:
                print(f"  {DIM}declared{RESET}  {package}/{name}.py — {accepted[name]}")
                continue
            orphans.append(f"{package}/{name}")

    for name in orphans:
        problems.append(
            f"`{name}.py` is a decision nothing in handlers/, gates/ or pipelines/ can "
            f"reach. It is proved offline and never asked — the state core/reconciliation.py, "
            f"core/review.py and core/entities.py were in while claims 4, 5 and 6 were being "
            f"scored against a system that had no path to any of them. Give it a caller that "
            f"runs, or declare its route in {ACCEPTANCE.relative_to(ROOT)}"
        )

    if problems:
        print(
            f"\n{RED}the map does not match the ground: {len(problems)} finding(s){RESET}\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        return 1

    print(
        f"  {GREEN}ok{RESET}    {len(real)} package(s) documented and present, and every module "
        f"in core/ and security/ is reachable from something that runs, or declared"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
