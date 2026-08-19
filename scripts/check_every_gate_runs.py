#!/usr/bin/env python3
"""Every claim harness this repository owns is run by CI, and by `make claims`.

**The gap this closes, and it is the largest one this project has had.** There are three
hand-maintained lists of what proves this repository — `make claims`, the claim-gates job in
`ci.yml`, and `scripts/preflight.py` — and CI never invokes `make`. It re-lists the gates by
hand. So the lists could diverge, and they did:

    evals/provenance   claim 2   — not in CI
    evals/drift        the envelope on arriving traffic  — not in CI
    evals/feedback     claim 1's loop  — not in CI
    evals/grounding    a proposal must point at its source  — not in CI
    evals/baseline     what the derived policy buys  — not in CI
    evals/external     out of distribution  — not in CI

Six of fifteen, and one of them is `provenance` — the harness behind *"a published field that
cannot be located on a page is a build failure"*, which `CLAUDE.md` calls the project in one
sentence. It ran only under `make preflight`, by hand, on a machine with Docker. Nothing would
have gone red if it had broken.

**Why a check rather than just adding the six lines.** Adding them fixes today. This fixes the
next one: a harness written next month and wired into nothing is a claim that scores itself. The
list of harnesses is the directory, and the directory is the thing to compare against — anything
else is a fourth hand-maintained list.

An eval may be deliberately absent from CI, and that is a declaration with a reason in
`contracts/ci/gates.yaml`, never a silence. None is: `external` looked like the obvious
exemption — it reads a licensed corpus nobody may redistribute — and needs none, because
`recordings/external/` is committed exactly as `recordings/ocr/` is. It compares two committed
curves and touches no external file at runtime.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
CI = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
DECLARED = ROOT / "contracts" / "ci" / "gates.yaml"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _harnesses() -> set[str]:
    """Every `evals/<name>/__main__.py`. The directory is the list; nothing else is."""
    return {
        child.name
        for child in EVALS.iterdir()
        if child.is_dir() and (child / "__main__.py").exists()
    }


def _run_by_ci() -> set[str]:
    """Every `-m evals.<name>` the workflow runs, in any job, however python is spelled.

    Matched on the module argument rather than on `python -m …`, and the difference is not
    cosmetic: `claim 2` runs *inside the reader image*, because the corpus pages are generated
    there and exist nowhere else, so its line is `-m evals.provenance` under a `docker run`
    with the interpreter given as an entrypoint. The stricter pattern reported the harness as
    unrun while it was running — a check reading the wrong thing, on the very check written to
    stop checks reading the wrong thing.
    """
    return set(re.findall(r"-m evals\.(\w+)", CI.read_text(encoding="utf-8")))


def _run_by_make() -> set[str]:
    """Every `evals.<name>` the Makefile runs, in any target."""
    return set(re.findall(r"evals\.(\w+)", MAKEFILE.read_text(encoding="utf-8")))


def _declared() -> dict[str, str]:
    """Harnesses deliberately absent from CI, and why. The reason must be readable."""
    if not DECLARED.exists():
        return {}
    loaded = yaml.safe_load(DECLARED.read_text(encoding="utf-8")) or {}
    return dict(loaded.get("not_in_ci") or {})


#: Everything that can invoke a check. The Dockerfile is in the list because one check is run
#: there and nowhere else — `check_corpus_font.py` probes the font at image build time, which is
#: the only place it can catch the failure it exists for. A sweep that knew about the first three
#: would have reported it as unrun, which is the wrong finding stated confidently.
INVOKERS = (
    "scripts/preflight.py",
    "Makefile",
    ".github/workflows/ci.yml",
    ".github/workflows/deploy.yml",
    ".github/workflows/destroy.yml",
    "Dockerfile",
    "Dockerfile.emr",
)


def _checks_nothing_runs() -> list[str]:
    """Every `scripts/check_*.py` against everything that could invoke one.

    **This file's whole subject applied to `scripts/`, where it was not applied.** It compared
    `evals/` to CI and the Makefile and stopped there, so a gate written into `scripts/` and
    wired to nothing would sit on disk looking like enforcement. That is the same failure it
    refuses for a harness, one directory across.

    The repository has produced this exact shape twice in three days: ADR-0003 declared a gate
    that was never written, and `security/injection.py` is a control two documents cite and
    nothing calls. A check nobody runs is the version of it that is hardest to see, because the
    file is there and it passes when you run it by hand.
    """
    # **Comments stripped, because a comment naming a script does not run it.** The Dockerfile
    # explains `check_corpus_font.py` in a comment seven lines above the `RUN` that invokes it,
    # and `preflight.py` discusses several checks it does not call. Searching the raw text finds
    # the prose about a gate and reports the gate as wired — the same defect the scoreboard check
    # had, where a comment recording a service's removal proved the service was still there.
    invokers = "\n".join(
        "\n".join(
            line
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith(("#", "//"))
        )
        for name in INVOKERS
        if (ROOT / name).exists()
    )
    return [
        f"scripts/{check.name} exists and nothing invokes it — not preflight, not the Makefile, "
        f"not a workflow, not an image build. A gate wired into nothing is a file that looks "
        f"like enforcement and is not"
        for check in sorted(ROOT.glob("scripts/check_*.py"))
        if check.name not in invokers
    ]


def main() -> int:
    harnesses, in_ci, in_make = _harnesses(), _run_by_ci(), _run_by_make()
    declared = _declared()
    problems: list[str] = _checks_nothing_runs()

    for name in sorted(harnesses - in_ci):
        if name in declared:
            print(f"  {DIM}declared{RESET}  evals/{name} is not in CI — {declared[name]}")
            continue
        problems.append(
            f"evals/{name}/ exists and no job in ci.yml runs it. A harness wired into nothing "
            f"is a claim that scores itself: it can break, and every check stays green. Either "
            f"run it, or declare why not in {DECLARED.relative_to(ROOT)}"
        )

    for name in sorted(harnesses - in_make):
        if name in declared:
            continue
        problems.append(
            f"evals/{name}/ exists and no Makefile target runs it, so `make claims` is not the "
            f"whole set it says it is"
        )

    # And the reverse, which is how a rename goes unnoticed: a workflow that runs a harness this
    # repository no longer has fails at the runner rather than here, three minutes into a deploy.
    for name in sorted((in_ci | in_make) - harnesses):
        problems.append(f"a workflow or the Makefile runs evals.{name}, which does not exist")

    for name in sorted(set(declared) - harnesses):
        problems.append(
            f"{DECLARED.relative_to(ROOT)} declares evals/{name} absent from CI and there is no "
            f"such harness. An exemption outliving its subject is one nobody re-reads"
        )

    if problems:
        print(f"\n{RED}the gates: {len(problems)} finding(s){RESET}\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        return 1

    print(
        f"  {GREEN}ok{RESET}    {len(harnesses)} claim harness(es) and "
        f"{len(list(ROOT.glob('scripts/check_*.py')))} check script(s), every one run by "
        f"something" + (f", {len(declared)} declared absent with a reason" if declared else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
