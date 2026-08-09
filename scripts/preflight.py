#!/usr/bin/env python3
"""Everything that must be true before the estate could be stood up, in one command.

Collecting these is not about convenience. It is that *"is it ready?"* should have one answer,
produced the same way every time, rather than a person remembering nine commands and
forgetting the tenth — which will be the one that mattered.

Three groups, and they fail differently.

**Correctness** — the suite, the gates, the claim harnesses. These are the statements the
README makes. A failure here means a claim is false right now.

**Consistency** — two things that must agree have stopped agreeing. Nothing is broken; the
drift is invisible until something reads both, and by then one of them has been believed.

**Deployability** — Terraform validates against real provider schemas, checkov is clean. None
of it affects an offline run, and each one is otherwise a deploy that fails at minute forty.

`--fast` skips the slow members of each group; CI runs the whole thing.

**Green here does not mean deployed, and it never will.** `docs/DECISIONS.md` 14: nothing in
this repository is ever applied to AWS. This command answers *"would this be ready?"* and that
is the only question it is allowed to answer.

The list grows one line per phase. A check for a claim that is not yet provable would be a
green tick over work that has not happened, so there are fewer lines here than there will be.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str, fallback: str | None = None) -> str:
    """The venv's copy when there is a venv, whatever is on PATH when there is not.

    Hard-coding `.venv/bin/…` makes preflight a check that only runs where it was written, and
    the place it then fails is inside a deploy.
    """
    candidate = ROOT / ".venv" / "bin" / name
    return str(candidate) if candidate.exists() else (fallback or name)


PYTHON = _tool("python", sys.executable)
RUFF = _tool("ruff")
#: checkov lives in its own environment because it pins boto3 exactly. `make iac-scan` creates
#: `.venv-checkov` on demand; this finds it, and falls back to PATH so a runner that installed
#: it another way still runs the scan.
CHECKOV = str(_CV) if (_CV := ROOT / ".venv-checkov" / "bin" / "checkov").exists() else "checkov"

LINT_PATHS = ["src", "tests", "scripts", "corpus"]

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


@dataclass
class Check:
    group: str
    name: str
    command: list[str]
    #: Why a reader should care that this passed. Printed on failure, because a red line with
    #: no reason is a red line somebody reruns hoping it goes away.
    matters: str
    slow: bool = False
    #: Skipped with a note when the tool is absent, rather than failing the run. A preflight
    #: that cannot start without Terraform installed is a preflight nobody runs; CI has the
    #: tool and runs it there.
    needs: str | None = None


CHECKS: list[Check] = [
    # ── Correctness ─────────────────────────────────────────────────────────
    Check(
        "correctness",
        "test suite",
        [PYTHON, "-m", "pytest", "-q"],
        "Every claim this repository makes is asserted by one of these.",
    ),
    Check(
        "correctness",
        "core purity",
        [PYTHON, "scripts/check_core_is_pure.py"],
        "The core imports no cloud SDK, reads no clock and names no engine. It is the reason "
        "claims 1 to 6 can be checked at all without an account and without a billed API.",
    ),
    Check(
        "correctness",
        "planting is blind",
        [PYTHON, "scripts/check_planting_is_blind.py"],
        "The corpus plants mismatches in the vocabulary of shipment facts and knows nothing "
        "about the rules that will find them. Without it, claim 4 is one function agreeing "
        "with itself and reports green forever.",
    ),
    Check(
        "correctness",
        "gate-proof",
        [PYTHON, "scripts/gate_proof.py"],
        "Each gate refuses a real violation, for the right reason. Slow, and the most "
        "informative line here: a gate that has never been shown to fail is a comment.",
        slow=True,
    ),
    # ── Consistency ─────────────────────────────────────────────────────────
    Check(
        "consistency",
        "contracts",
        [PYTHON, "scripts/check_contracts.py"],
        "Every contract loads and the set cross-checks. A rule naming a field nobody declared "
        "is otherwise discovered at run time, on a document, in front of a customer.",
    ),
    Check(
        "consistency",
        "corpus reproduces",
        [PYTHON, "-m", "corpus.generate", "--check"],
        "If the corpus drifts, every claim above was scored against a different corpus than "
        "the one that was reviewed — which is the same as not having scored them.",
        slow=True,
    ),
    Check(
        "consistency",
        "lint",
        [RUFF, "check", *LINT_PATHS],
        "The same command CI runs.",
    ),
    Check(
        "consistency",
        "format",
        [RUFF, "format", "--check", *LINT_PATHS],
        "The same command CI runs.",
    ),
    # ── Deployability ───────────────────────────────────────────────────────
    Check(
        "deployability",
        "terraform fmt",
        ["terraform", "fmt", "-check", "-recursive", "infra"],
        "Formatting drift makes a real diff unreadable.",
        needs="terraform",
    ),
    Check(
        "deployability",
        "terraform validate",
        [sys.executable, "scripts/tf_validate.py"],
        "Against real provider schemas. This catches an attribute that does not exist. It "
        "does not catch a value a provider would reject — that needs a plan, and a plan needs "
        "credentials this repository does not use.",
        slow=True,
        needs="terraform",
    ),
    Check(
        "deployability",
        "checkov",
        [CHECKOV, "-d", "infra", "--compact", "--quiet"],
        "Zero findings, with every deliberate exception carrying a written reason beside the "
        "resource it applies to.",
        slow=True,
        needs=CHECKOV,
    ),
]


@dataclass
class Result:
    check: Check
    status: str
    seconds: float
    output: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def failed(self) -> list[Result]:
        return [result for result in self.results if result.status == "fail"]

    @property
    def skipped(self) -> list[Result]:
        return [result for result in self.results if result.status == "skip"]

    @property
    def ok(self) -> bool:
        return not self.failed


def run(check: Check) -> Result:
    if check.needs and not (shutil.which(check.needs) or Path(check.needs).exists()):
        return Result(check, "skip", 0.0, f"{check.needs} is not installed")
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 — fixed command lists, no shell
        check.command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONWARNINGS": "ignore"},
    )
    elapsed = time.monotonic() - started
    status = "pass" if completed.returncode == 0 else "fail"
    return Result(check, status, elapsed, (completed.stdout + completed.stderr)[-3000:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="Skip the slow checks.")
    parser.add_argument("--group", help="Run one group only.")
    arguments = parser.parse_args()

    selected = [
        check
        for check in CHECKS
        if not (arguments.fast and check.slow)
        and (not arguments.group or check.group == arguments.group)
    ]

    report = Report()
    current_group = ""
    for check in selected:
        if check.group != current_group:
            current_group = check.group
            print(f"\n{DIM}── {current_group}{RESET}")
        print(f"   {check.name:<32}", end="", flush=True)
        result = run(check)
        report.results.append(result)
        mark = {
            "pass": f"{GREEN}ok{RESET}",
            "fail": f"{RED}FAIL{RESET}",
            "skip": f"{YELLOW}skip{RESET}",
        }
        print(f"{mark[result.status]}  {DIM}{result.seconds:5.1f}s{RESET}")

    print()
    for result in report.skipped:
        print(f"{YELLOW}skipped{RESET} {result.check.name}: {result.output}")

    for result in report.failed:
        print(f"\n{RED}FAILED{RESET} {result.check.name}")
        print(f"  why it matters: {result.check.matters}")
        print(f"{DIM}{result.output.rstrip()}{RESET}")

    passed = sum(1 for result in report.results if result.status == "pass")
    print(
        f"\npreflight: {passed} passed, {len(report.failed)} failed, {len(report.skipped)} skipped"
    )
    if report.ok:
        print("the repository would be ready to deploy; nothing here has been deployed")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
