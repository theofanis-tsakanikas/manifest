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

**Green here does not mean deployed.** `docs/DECISIONS.md` 14: only `bootstrap` has been
applied, and each layer above it is a deliberate, separate act. This command answers *"would
this be ready?"* and that is the only question it is allowed to answer — a green run after a
deploy still means the same thing, because every check in it is offline by construction.

The list grows one line per phase. A check for a claim that is not yet provable would be a
green tick over work that has not happened, so there are fewer lines here than there will be.
"""

from __future__ import annotations

import argparse
import os
import re
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

#: Which paragraph of the README carries the service line: title, tagline, services.
#: The stack line is italic and dot-separated; three separators is more than any prose line
#: in this README carries, and fewer than the stack has ever had.
SUBTITLE_MIN_SEPARATORS = 3

LINT_PATHS = ["src", "tests", "scripts", "corpus", "evals", "pipelines"]

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
        # **No `-q` here.** `pyproject.toml` already sets it in `addopts`, so passing it again
        # makes `-qq`, and pytest's second quiet level removes the `N passed in Ms` line
        # entirely. That line is the only thing `_scoreboard_figures` reads the test count from,
        # so the count was never extracted, the README's figure was never compared to anything,
        # and the check that exists to catch a stale scoreboard reported green over one for as
        # long as it has existed — the README said 295 against a suite of 309.
        #
        # The same shape as the gate that counted an export and called it a value: not a wrong
        # answer, an absent input, which is the failure that looks most like success.
        [PYTHON, "-m", "pytest"],
        "Every claim this repository makes is asserted by one of these.",
    ),
    Check(
        "correctness",
        "the engine adapter parses this build",
        [PYTHON, "scripts/check_engine_adapter.py"],
        "A reader upgrade that renamed a column breaks every reading silently: the committed "
        "recording keeps deriving perfectly good thresholds from data nothing can produce any "
        "more, and the first sign is an empty record. Derives nothing itself — see "
        "docs/DECISIONS.md 19.",
        needs="tesseract",
    ),
    Check(
        "correctness",
        "claim 1 · the loop",
        [PYTHON, "-m", "evals.feedback"],
        "A reviewer's correction enters the derivation and a field can leave always-review as N "
        "grows — with the error budget untouched. Rubber-stamped and hurried decisions are "
        "excluded and counted, because an approval from somebody who was not looking lowers the "
        "observed error rate on evidence nobody generated.",
        slow=True,
    ),
    Check(
        "correctness",
        "what the discipline buys",
        [PYTHON, "-m", "evals.baseline"],
        "The derived policy against publishing everything and against a hand-picked 0.85 — "
        "wrong-rate and queue volume, both, for all three. Without this the repository proves "
        "its controls refuse correctly and never says what that is worth.",
        slow=True,
    ),
    Check(
        "correctness",
        "production drift",
        [PYTHON, "-m", "evals.drift"],
        "The declared envelope applied to arriving traffic, firing in both directions. A reader "
        "suddenly confident is the more alarming direction and the one nobody watches.",
        slow=True,
    ),
    Check(
        "consistency",
        "the external corpus is licensed",
        [PYTHON, "scripts/check_external_corpus.py"],
        "An out-of-distribution set may only be scored if its terms were read and written down "
        "first. The temptation here is to score and read afterwards, and that is the order this "
        "refuses.",
    ),
    Check(
        "correctness",
        "the out-of-distribution column",
        [PYTHON, "-m", "evals.external"],
        "Whether a confidence means the same thing on paper this repository did not design. It "
        "is the only answer to 'did you tune the generator until the claims passed?' that does "
        "not come from the generator's own author.",
        slow=True,
    ),
    Check(
        "correctness",
        "grounded classification",
        [PYTHON, "-m", "evals.grounding"],
        "A tariff proposal that cannot point at the text it came from does not go forward — "
        "claim 2's argument applied to text instead of pixels. Contested pairs still abstain "
        "with both members well supported, which is the trap retrieval creates.",
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
        "reader identity",
        [PYTHON, "scripts/reader_version_check.py"],
        "The image's reader and the recording's reader are the same binary, exactly. Every "
        "threshold here is a statement about one reader, and the deployed handler looks its "
        "thresholds up by that reader's identity — so a version difference of one patch is a "
        "deployment that builds, applies, triggers, and then finds no artefact for the reader "
        "that is actually reading. It cost a green deploy and an IAM error naming nothing.",
    ),
    Check(
        "deployability",
        "no stale never",
        [PYTHON, "scripts/check_no_stale_never.py"],
        "Nine files claimed nothing had ever been applied after five applies, and a second wave "
        "claimed the managed readers were never called after all three had been. Both were found "
        "by reading, a week apart, and the second used different wording from the first — which "
        "is why grepping for last week's phrasing found nothing. An underclaim reads as modesty "
        "and is exactly as false as the overclaim this repository spends its life removing.",
    ),
    Check(
        "deployability",
        "screenshots redacted",
        [PYTHON, "scripts/mask_account_id.py", "--check"],
        "Every AWS console page prints the account id in its corner, and every terminal capture "
        "of a bucket name or an ARN prints it again in the middle of the line. gitleaks gates "
        "that identifier in text and never sees a screenshot, so it walks straight past both. "
        "This is the same rule for pixels, and it found the id in the image this README opens "
        "with.",
    ),
    Check(
        "deployability",
        "pipeline routing",
        [PYTHON, "scripts/check_pipeline_routing.py"],
        "A document that publishes eight fields and abstains on four still sends the four to a "
        "human. The estate shipped without this: Publish and QueueForReview were the only "
        "terminal states and they exclude each other, so every partial abstention reached "
        "nobody and claim 5's capacity model was measuring the empty set.",
    ),
    Check(
        "correctness",
        "cascade tiers",
        [PYTHON, "-m", "pytest", "-q", "tests/handlers/test_handlers.py", "-k", "Escalation"],
        "Only tiers 0 and 1 report a confidence, and the handler's set agrees with the routing "
        "contract's own prose. Admitting an unscored tier would publish a value on a number "
        "nothing measured — and it would look like an improvement, because the page really was "
        "read better.",
    ),
    Check(
        "consistency",
        "the warehouse has a source",
        [PYTHON, "scripts/check_warehouse_is_fed.py"],
        "Every column the marts read either exists in the lake, is derivable from it, or is "
        "declared as having no source at all. The lakehouse and the warehouse both applied "
        "cleanly and neither has ever held a row; a mart reading an invented column returns a "
        "number with the shape of an answer.",
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
        "claim 1 · thresholds",
        [PYTHON, "-m", "evals.calibration"],
        "No field publishes below a threshold derived from its declared error budget, and "
        "where no threshold fits, the harness says whether the limit is evidence or quality — "
        "two problems with the same symptom and opposite fixes.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 2 · provenance",
        [PYTHON, "-m", "evals.provenance", "--sample", "40"],
        "The gate passes honest records and refuses corrupted ones, by the right layer. A "
        "corrupted box that verifies is claim 2 reporting green over a record nothing checked.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 3 · versioning",
        [PYTHON, "-m", "evals.reprocessing"],
        "The same input publishes an identical record; a reader change publishes a new version "
        "with a diff, and a human decision on a field that moved is re-queued rather than "
        "carried across a value that no longer exists.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 4 · reconciliation",
        [PYTHON, "-m", "evals.reconciliation"],
        "Exactly the planted disagreements, and nothing else, on the values that were printed. "
        "The planting is blind to the contract and the expectation comes from ground truth by "
        "a separate path — without both, this claim is one function agreeing with itself.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 5 · the human loop",
        [PYTHON, "-m", "evals.review"],
        "Nothing publishes below its threshold without a recorded decision, a field with no "
        "provenance cannot be approved into existence, and the queue's declared capacity is "
        "measured against — with any overage accepted by name, with an expiry.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 7 · scale and cost",
        [PYTHON, "-m", "evals.scale"],
        "A re-run does no work, a crash resumes at exactly the remainder, and the cost is a "
        "model built from a measured routing distribution and published prices — labelled a "
        "model, with its largest assumption swept rather than chosen.",
        slow=True,
    ),
    Check(
        "correctness",
        "claim 6 · entities",
        [PYTHON, "-m", "evals.entities"],
        "A merge can be undone with lineage intact and every downstream record re-pointed. "
        "The half nobody builds is the re-pointing: a dangling pointer is invisible until "
        "somebody follows it.",
    ),
    Check(
        "correctness",
        "injection",
        [PYTHON, "-m", "evals.injection"],
        "Document text is fenced in a delimiter it cannot itself contain — refused, not "
        "escaped — and the detector has zero false positives on 2,963 documents of ordinary "
        "trade prose. The second number is what decides whether the control survives.",
        slow=True,
    ),
    Check(
        "correctness",
        "line-item totals",
        [PYTHON, "-m", "evals.lineitems"],
        "A table truncated at a page break is caught by arithmetic the printed total cannot "
        "hide. Without it a dropped row leaves a record that is short and every field in it "
        "looks correct.",
        slow=True,
    ),
    Check(
        "correctness",
        "classification",
        [PYTHON, "-m", "evals.classification"],
        "A contested heading abstains, the band is on the gap between the top two rather than "
        "on the top score, and no proposal publishes at any score — hs_code is always-review "
        "and the claim is about the gate rather than the model.",
    ),
    Check(
        "consistency",
        "analytics marts",
        [PYTHON, "scripts/check_marts.py"],
        "Every mart reads only columns analytics/schema.sql declares. A query that invents a "
        "column fails at minute forty of a deploy, and this is the only place to catch it "
        "without a warehouse.",
    ),
    Check(
        "correctness",
        "corpus envelope",
        [PYTHON, "scripts/check_corpus_envelope.py"],
        "The generator sits inside its declared operating range. Degraded too gently, every "
        "confidence is at the top and no threshold means anything; too hard, and every claim "
        "is scored on pages nobody could read. Both report green without this.",
        slow=True,
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
        "the deploy path",
        [PYTHON, "scripts/check_deploy_path.py"],
        "Every layer the deploy workflow applies is torn down by the destroy workflow, in "
        "reverse order, and both are human-dispatch only behind a protected environment. A "
        "repository with a deploy path and no teardown path is how an estate gets left "
        "standing.",
    ),
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


#: Each claim's own summary line, and the numbers in it the README must repeat. Written as
#: patterns over the harness's output rather than as expected values, so this file states *where
#: to look* and never *what the answer is* — a scoreboard check carrying its own copy of the
#: figures would agree with itself for ever.
CLAIM_FIGURES = {
    "1": (r"(\d+) fields — (\d+) with a derived threshold, (\d+) always-review"),
    "4": r"exactly (\d+) planted disagreements found",
}

#: Figures the README states in prose rather than in a `**claim N**` row, keyed by the check
#: whose output produces them.
#:
#: **Every one of these had drifted.** The audit of 2026-08-19 found the README claiming ECE
#: 0.0371 for a corpus that scores 0.0815, a baseline of 19.11/4.56/0.41 against a run producing
#: 26.72/4.68/0.22, and 2,963 clean documents against 2,969 — while the two figures the scoreboard
#: check *did* cover were both correct. The mechanism worked; it was pointed at two rows out of a
#: page of numbers.
#:
#: The claim-row check reads a line containing `**claim N**`. These do not live in such a line, so
#: they are matched anywhere in the file: the rule is only that the figure the harness printed
#: appears somewhere the reader can find it.
PROSE_FIGURES = {
    "the out-of-distribution column": (
        r"ECE ([\d.]+) on ours against ([\d.]+) on theirs",
        "the out-of-distribution ECE pair",
    ),
    "what the discipline buys": (
        r"naive\s+published\s+[\d,]+\s+published-and-wrong\s+[\d,]+ \(\s*([\d.]+)%\).*?"
        r"chosen\s+published\s+[\d,]+\s+published-and-wrong\s+[\d,]+ \(\s*([\d.]+)%\).*?"
        r"derived\s+published\s+[\d,]+\s+published-and-wrong\s+[\d,]+ \(\s*([\d.]+)%\)",
        "the baseline comparison",
    ),
    "injection": (
        r"documents carrying none\s+(\d+)",
        "the count of clean documents",
    ),
}


def _scoreboard_figures(report: Report) -> dict[str, str]:
    """The figures this run actually produced, keyed by the phrase the README must contain."""
    figures: dict[str, str] = {}

    for result in report.results:
        found = re.search(r"(\d+) passed[ ,]", result.output)
        if found and result.check.name == "test suite":
            figures["tests"] = f"{found.group(1)} passing"
        found = re.search(r"gate-proof: (\d+) refused, (\d+) accepted, (\d+) stale", result.output)
        if found:
            figures["gate-proof"] = (
                f"{found.group(1)} refused, {found.group(2)} accepted, {found.group(3)} stale"
            )
        found = re.search(r"Passed checks: (\d+), Failed checks: (\d+)", result.output)
        if found:
            figures["checkov"] = f"{found.group(1)} passed, {found.group(2)} findings"

    # Plus one, because this check is one of the checks and is not in the list yet. Stated
    # rather than left as a bare `+ 1`: the number on the README is the number `make preflight`
    # prints, and the two agreeing is the whole point of the check.
    figures["preflight"] = f"{len(report.results) + 1} checks"
    return figures


def _prose_figure_disagreements(report: Report, readme: str) -> list[str]:
    """Figures the README states in prose, against the harness that produced them.

    **The gap the claim-table check left, and it was the larger half.** That check reads a line
    containing `**claim N**`; the README states just as many numbers in ordinary sentences, and
    on 2026-08-19 every one of those had drifted while both covered rows were correct. A check
    aimed at two rows on a page of numbers reports green about the page.

    Matched anywhere in the file rather than on a particular line: the requirement is that the
    figure the harness printed is somewhere a reader can find it, not that a sentence is phrased
    a given way. A number that has changed meaning still fails, which is the point — the ECE pair
    that started this was not only stale, it was the wrong way round.
    """
    problems: list[str] = []
    stripped = re.sub(r"[,*`]", "", readme)
    for name, (pattern, label) in PROSE_FIGURES.items():
        output = next((r.output for r in report.results if r.check.name == name), "")
        found = re.search(pattern, re.sub(r"[,*`]", "", output), re.DOTALL)
        if not found:
            continue
        for number in found.groups():
            if number and number not in stripped:
                problems.append(
                    f"`{name}` produced {number} for {label} and the README states no such "
                    f"figure. Either the number moved and the prose did not, or the prose is "
                    f"describing a run that no longer happens"
                )
    return problems


def _claim_table_disagreements(report: Report, readme: str) -> list[str]:
    """Each claim's own summary numbers, against the README row that repeats them.

    The paragraph in `check_the_scoreboard` says *"every count the README states is now extracted
    and required to match, wherever it is written"*. It was not true: three figures were
    extracted and the twenty on the claim table were not. Two had drifted a whole corpus
    generation — claim 1 reported `4 derived, 7 evidence-limited, 26 quality-limited` against a
    run producing `5, 4, 30`, and claim 4 said 116 planted disagreements against a corpus that
    plants 123.

    Read out of each harness's own output and matched against the README row for that claim.
    Phrasing is free to differ; the numbers are not.
    """
    problems: list[str] = []
    for claim, pattern in CLAIM_FIGURES.items():
        output = next(
            (r.output for r in report.results if r.check.name.startswith(f"claim {claim}")), ""
        )
        found = re.search(pattern, output)
        if not found:
            continue
        row = next((line for line in readme.splitlines() if f"**claim {claim}**" in line), "")
        if not row:
            problems.append(f"the README has no scoreboard row for claim {claim}")
            continue
        stated = set(re.findall(r"\d+", row.replace(",", "")))
        problems += [
            f"claim {claim} produced {number} and the README row does not state it: "
            f"{row.strip()[:120]}"
            for number in found.groups()
            if number and number not in stated
        ]
    return problems


def check_the_scoreboard(report: Report) -> Result:
    """The README's numbers must be the numbers this run produced.

    `README.md` opens by saying every figure on it is the output of a command in this
    repository. Nothing enforced that, and by the time it was looked at the scoreboard carried
    three stale figures and disagreed with itself: **25 checks** at the top, **21** in the usage
    block, **19** mutations against a harness reporting 24, and a checkov total 13 behind.

    None of them was a lie when it was written, and that is the point — a scoreboard drifts by
    the ordinary act of adding a gate, silently, in the direction of looking more finished than
    it is. A repository whose first claim is that every number is reproducible has to be the one
    that checks.
    """
    check = Check(
        group="deployability",
        name="scoreboard",
        command=[],
        matters=(
            "the README states every figure is the output of a command here; a stale one makes "
            "that sentence false about itself"
        ),
    )
    figures = _scoreboard_figures(report)
    if "gate-proof" not in figures or "checkov" not in figures:
        return Result(check, "skip", 0.0, "gate-proof and checkov did not run (--fast)")

    problems: list[str] = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    # **The number has to be right everywhere it appears, not somewhere.**
    #
    # The first version of this check verified three figures — gate-proof, checkov, the check
    # count — and reported green over four others: the test count in two places, disagreeing
    # with each other and with reality by fifty-nine; a "breaks nineteen controls" left two
    # lines under a table saying thirty-three.
    #
    # A check scoped to three figures, described as "the scoreboard is verified", is worse than
    # no check: it is the drift this file exists to catch, wearing the badge of the thing that
    # was supposed to catch it. So every count the README states is now extracted and required
    # to match, wherever it is written.
    problems += _claim_table_disagreements(report, readme)
    problems += _prose_figure_disagreements(report, readme)

    counted = {
        "tests": re.findall(r"\*\*(\d+) passing\*\*|# (\d+) tests", readme),
        "gate-proof mutations": re.findall(
            r"\*\*(\d+) refused|break (\d+) controls|breaks (\d+) controls", readme
        ),
    }
    for name, matches in counted.items():
        spoken = {value for group in matches for value in group if value}
        if len(spoken) > 1:
            problems.append(
                f"README states the {name} count as {sorted(spoken, key=int)} in different "
                f"places. Two numbers for one fact is worse than one stale number: nobody can "
                f"tell which is current, so nobody trusts either"
            )

    problems += [
        f"README does not say `{value}` ({name}). It is what this run produced"
        for name, value in figures.items()
        if value not in readme
    ]

    # A service in the subtitle that the estate does not use is decision 6's CV keyword, and the
    # subtitle is the worst place in the repository for one: it is the first line a reader sees.
    # `Comprehend` sat there for a day after being deliberately removed everywhere else —
    # including from the core purity gate's list of forbidden names.
    # **Found by its shape, not by its position, and the difference was a real failure.**
    #
    # This read `paragraphs[2]` on the assumption that the file opens title, tagline, services.
    # The README was restructured on 2026-08-19 to add a banner and three rows of badges, the
    # stack line moved to the fifth paragraph, and the check began reading the badge block —
    # which names no services, so it reported OpenSearch, EMR Serverless and Lambda as missing
    # from a line that lists all three.
    #
    # It was right to fire: something moved underneath it. But an index into a document is a
    # dependency on layout that nothing declares, and the next reshuffle breaks it again. The
    # stack line has a shape no other line in the file has — italic, dot-separated, several
    # entries — so that is what identifies it.
    subtitle = next(
        (
            line
            for line in readme.splitlines()
            if line.startswith("*")
            and line.rstrip().endswith("*")
            and line.count("·") >= SUBTITLE_MIN_SEPARATORS
        ),
        "",
    )
    if not subtitle:
        # **An absent input, not a wrong answer.** With no line found, every service would read
        # as "not named" and the check would report eight findings that are really one — or,
        # worse, if the service list ever emptied too, none at all. The failure that looks most
        # like success is the one where the comparison had nothing on one side.
        problems.append(
            "the README has no italic dot-separated stack line, so there is nothing to compare "
            "the estate against. This check cannot pass by finding nothing"
        )
    # **Comments stripped first.** Without this the check read `comprehend:` out of the comment
    # that records its removal and reported the service as present — a gate finding a service in
    # the prose explaining that the service is gone. What the subtitle must match is what the
    # estate *does*, and a comment does nothing.
    estate = "\n".join(
        "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        for path in sorted(ROOT.glob("infra/*/*.tf"))
    ).lower()
    for service, token in (
        {}
        if not subtitle
        else {
            "Comprehend": "comprehend:",
            "OpenSearch": "opensearchserverless",
            "SageMaker": "sagemaker",
            "Redshift": "redshiftserverless",
            "EMR Serverless": "emrserverless",
            "Lambda": "aws_lambda_function",
            "Textract": "textract:",
            "Bedrock": "bedrock:",
        }
    ).items():
        named = service.lower() in subtitle.lower()
        present = token in estate
        if named and not present:
            problems.append(
                f"the README subtitle advertises {service} and no layer uses it. Decision 6: a "
                f"service named where it cannot be pointed at its work is a CV keyword, and "
                f"this is the first line anybody reads"
            )
        if present and not named:
            problems.append(
                f"{service} is in the estate and absent from the README subtitle. The "
                f"under-statement is the same defect as the over-statement: the line does not "
                f"describe the system"
            )
    # A count stated twice with two different values is worse than a stale one: it means nobody
    # can tell which is current.
    counts = set(re.findall(r"\*\*(\d+) checks?\*\*|all (\d+)[:,]| all (\d+) pass", readme))
    spoken = {n for group in counts for n in group if n}
    if len(spoken) > 1:
        problems.append(
            f"README states the preflight count as {sorted(spoken)} in different places"
        )

    if problems:
        return Result(check, "fail", 0.0, "\n".join(problems))
    return Result(check, "pass", 0.0, ", ".join(f"{k}: {v}" for k, v in figures.items()))


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

    scoreboard = check_the_scoreboard(report)
    report.results.append(scoreboard)
    if scoreboard.status == "fail":
        print(f"\n{RED}FAILED{RESET} {scoreboard.check.name}")
        print(f"  why it matters: {scoreboard.check.matters}")
        print(f"{DIM}{scoreboard.output}{RESET}")
    elif scoreboard.status == "pass":
        print(f"{GREEN}ok{RESET}     scoreboard — {scoreboard.output}")

    passed = sum(1 for result in report.results if result.status == "pass")
    print(
        f"\npreflight: {passed} passed, {len(report.failed)} failed, {len(report.skipped)} skipped"
    )
    if report.ok:
        print("the repository would be ready to deploy; nothing here has been deployed")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
