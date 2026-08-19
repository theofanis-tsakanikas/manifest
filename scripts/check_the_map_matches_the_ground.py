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


def _unindexed_docs() -> list[str]:
    """Every file under `docs/` is named by the README's Docs index.

    **`docs/NEXT.md` sat unindexed for five days and went stale while it sat.** The README
    standard's rule is that an undiscoverable doc does not exist; the sharper version, learned
    here, is that an undiscoverable doc is not *maintained* either — nobody re-reads what nothing
    links to, so it keeps asserting 32/32 against a suite that reports 34/34.

    ADRs are exempt: the Decisions section links the directory and inlines the table, and a new
    ADR is meant to arrive without editing the Docs line.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    problems = []
    for path in sorted((ROOT / "docs").glob("*.md")):
        if f"docs/{path.name}" not in readme:
            problems.append(
                f"`docs/{path.name}` exists and the README's Docs index does not name it. An "
                f"undiscoverable document is one nobody re-reads, which is how it goes on being "
                f"true about a system that has moved"
            )
    return problems


#: Every top-level directory a document might point a reader into. A path in prose that names
#: something outside these is a URL, a shell fragment or an example, and not this check's business.
NAMED_ROOTS = (
    "src",
    "evals",
    "scripts",
    "contracts",
    "corpus",
    "infra",
    "tests",
    "docs",
    "pipelines",
    "recordings",
    "analytics",
    "images",
    ".github",
)

PROSE = ("README.md", "CLAUDE.md", "SECURITY.md", "PLAN.md")


def _paths_that_go_nowhere() -> list[str]:
    """Every repository path the prose names, against the disk.

    **The map check compared CLAUDE.md's tree to `src/manifest/` and stopped there**, so it saw
    the packages and none of the several hundred other paths the documents point a reader at. An
    audit on 2026-08-19 found seven dead ones, and the worst was not a typo: ADR-0003 names
    `scripts/check_provenance_paths_are_independent.py` under a heading reading *"independence is
    enforced by a gate, not intended"*, and the gate did not exist. The property held anyway, by
    habit, for ten days — and the document asserting it was enforced was the reason nobody looked.

    A dead path is cheap to write and reads as evidence. This makes it cost something.
    """
    problems: list[str] = []
    #: A markdown link writes the path twice — once in the label, once in the target — so one
    #: dead link is two matches. Reported once: a reader fixing it has one thing to fix.
    already: set[tuple[str, int, str]] = set()
    documents = [ROOT / name for name in PROSE] + sorted(ROOT.glob("docs/**/*.md"))
    pattern = re.compile(
        r"[`(]((?:" + "|".join(re.escape(root) for root in NAMED_ROOTS) + r")/[\w./-]*[\w/])[`)]"
    )
    for document in documents:
        if not document.exists():
            continue
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                named = match.group(1)
                if (ROOT / named.rstrip("/")).exists():
                    continue
                signature = (str(document), number, named)
                if signature in already:
                    continue
                already.add(signature)
                problems.append(
                    f"{document.relative_to(ROOT)}:{number} points at `{named}` and there is "
                    f"nothing there. Either it moved and the prose did not, or it was never "
                    f"written and the sentence describes a repository this is not"
                )
    return problems


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

    problems += _unindexed_docs()
    problems += _paths_that_go_nowhere()

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
    # **`classification/` was the third package with this shape and nothing looked at it.**
    # Walking `core/` and `security/` left `artefact`, `inference` and `grounding` unexamined:
    # two reached by routes an import graph cannot see — one packaged into `model.tar.gz` and run
    # by the serving container, one imported by the training script the deploy runs — and one,
    # `grounding`, with no caller in the running system at all. Found by an audit rather than by
    # this check, which is the argument for widening it rather than for trusting it further.
    for package in ("core", "security", "classification"):
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
        f"in core/, security/ and classification/ is reachable from something that runs, "
        f"or declared"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
