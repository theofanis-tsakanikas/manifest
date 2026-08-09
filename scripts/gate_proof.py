#!/usr/bin/env python3
"""Break every gate on purpose, and require the real gate to refuse it.

A test suite tells you the code does what it does. It does not tell you the *gates* still
bite, because a gate that has quietly stopped checking anything passes every test it has.
This script plants a genuine violation and demands a refusal.

Three rules keep it a proof rather than a ritual — the same three as Attestor and Watermark,
because they were each learned by getting one of them wrong:

**Green first.** Every mutation runs against a repository that currently passes. A refusal
from an already-broken baseline proves nothing.

**A non-zero exit is not evidence.** The *named* check must be the thing that failed, with a
message that names the violation. A mutation that happens to cause an unrelated crash is
reported as a failure of the proof, not as a pass — otherwise the day a gate is deleted, its
mutation still "passes" because the import now fails.

**A mutation whose target has moved is STALE.** If the code a mutation edits no longer looks
the way it expects, the mutation is not silently skipped and is not counted as a pass. It is
reported, and the run is red, because a proof that quietly stopped running is worse than one
that never existed.

Every mutation here is a mistake somebody could plausibly make. An absurd one proves the gate
refuses absurdity, which nobody doubted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    gate: str
    #: The command that must fail, run inside the mutated copy.
    command: list[str]
    #: A phrase the failure must contain. Its presence is what makes the refusal *the* refusal
    #: rather than any old error.
    expect: str
    apply: Callable[[Path], bool]
    #: Why this mutation is worth planting.
    rationale: str


def _replace(path: Path, old: str, new: str) -> bool:
    """Edit a file, returning False if the target text is not there any more."""
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


# ── The mutations ────────────────────────────────────────────────────────────


def _reach_for_the_cloud_from_the_core(root: Path) -> bool:
    """Fetch a page from object storage inside the core.

    The plausible version is not a rewrite, it is a convenience: a function that took a page
    now takes a key, because the caller had a key. Nothing fails on the machine that wrote it
    — and every claim in this repository silently becomes a claim about a machine with
    credentials.
    """
    path = root / "src/manifest/core/geometry.py"
    text = path.read_text(encoding="utf-8")
    return _replace(path, "import math\n", "import math\n\nimport boto3\n") and bool(text)


def _let_the_core_learn_which_engine_read_the_page(root: Path) -> bool:
    """One comment naming an engine.

    Not an import, not a branch — a comment, which is how it actually starts. It is left in
    while somebody works out whether the quirk is real, and by the time the branch appears
    underneath it, nobody remembers the comment was the first step.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "**Rounding out, never in.**",
        "**Rounding out, never in.** Textract reports fractions already, so this is a no-op there.",
    )


def _read_the_clock_inside_the_core(root: Path) -> bool:
    """Stamp a box with the time it was constructed.

    Reads as observability. It is claim 3: the same document re-extracted at the same engine
    version now produces a different record, and the diff report shows a change nobody made.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "    def __post_init__(self) -> None:\n",
        "    def __post_init__(self) -> None:\n"
        "        import datetime\n\n"
        "        _seen_at = datetime.datetime.now()  # noqa: DTZ005\n",
    )


def _let_an_engine_name_in_as_long_as_it_is_not_an_import(root: Path) -> bool:
    """Scan only the syntax tree, not the source text.

    The plausible fix for a false positive: "we only care about actual imports". It reads as
    precision and it removes the only part of the rule that catches a string literal, a
    dictionary key or a branch on an engine name — which is every way the name really gets in.
    """
    return _replace(
        root / "src/manifest/gates/core_purity.py",
        "    yield from _engine_findings(source, relative)\n",
        "",
    )


def _round_a_crop_to_the_nearest_pixel(root: Path) -> bool:
    """Round to nearest instead of outward.

    The most defensible-looking change in this file, and it is the one that makes claim 2 lie
    in the dangerous direction: a crop half a pixel short can cut the stroke off a digit, the
    verifier re-reads a damaged crop, and the gate reports that a *correct* record was false.
    A provenance check that manufactures failures gets muted within a week.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "        left = math.floor(self.left * page.width)\n"
        "        top = math.floor(self.top * page.height)\n"
        "        right = max(left + 1, math.ceil(self.right * page.width))\n"
        "        bottom = max(top + 1, math.ceil(self.bottom * page.height))\n",
        "        left = round(self.left * page.width)\n"
        "        top = round(self.top * page.height)\n"
        "        right = max(left + 1, round(self.right * page.width))\n"
        "        bottom = max(top + 1, round(self.bottom * page.height))\n",
    )


def _let_two_boxes_that_only_touch_overlap(root: Path) -> bool:
    """Compare the overlap against zero rather than against the tolerance.

    This is not hypothetical: it is what the first version of the module did, and the test
    caught it. `0.1 + 0.2` is not `0.3`, so two boxes meeting at an edge produce an overlap of
    the order of 1e-17 and `iou` reports agreement between a recorded box and the word beside
    it. The gate has to keep refusing the version that was already written once.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "        if right - left <= _EPSILON or bottom - top <= _EPSILON:\n",
        "        if right <= left or bottom <= top:\n",
    )


def _clamp_a_coordinate_that_left_the_page(root: Path) -> bool:
    """Clamp a coordinate that left the page instead of refusing it.

    Reads as robustness — "be liberal in what you accept". What it accepts is an adapter with
    a sign error or an unsubtracted offset, and the result is an archive of provenance records
    every one of which points at the top-left corner of its page.

    This mutation earned its place the hard way: on first run it was **accepted**, because
    every test written until then happened to be satisfied by a different raise further down.
    The gap was in the suite, not in the gate, and nothing but planting the violation would
    have shown it.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "    if -_EPSILON <= value <= 1 + _EPSILON:\n        return min(max(value, 0.0), 1.0)\n",
        "    return min(max(value, 0.0), 1.0)\n    if False:\n        pass\n",
    )


def _let_a_blank_crop_verify(root: Path) -> bool:
    """Drop the ink floor to zero.

    The plausible version is not a deletion, it is a tuning: somebody sees the gate refuse a
    faint but correct record, lowers the floor to make it pass, and lowers it to zero because
    that is where the last complaint stops. What is left is a check that measures ink and never
    refuses on it, so a box pointing at the margin verifies — and claim 2 keeps reporting green
    over records that point at nothing.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "INK_FLOOR: Final = 0.02",
        "INK_FLOOR: Final = 0.0",
    )


def _let_a_shorter_reread_verify_a_longer_record(root: Path) -> bool:
    """Make containment work in both directions.

    Reads as symmetry, and it accepts `89` as verification of `8959`. A padded crop legitimately
    shows *more* than the record; a crop that shows less is a record claiming a value the page
    does not carry, and this is the direction where the difference is money.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "    return bool(published) and published in read",
        "    return bool(published) and (published in read or read in published)",
    )


def _stop_re_reading_the_crop(root: Path) -> bool:
    """Trust the ink and skip the second recognition path.

    The expensive layer, and the first one somebody removes on a busy afternoon: it costs a
    reader pass per published field. Layer A still runs, so a box on the margin is still
    refused and most of claim 2 keeps its shape — while the only layer that can tell ink from
    *the right* ink stops running.
    """
    path = root / "src/manifest/gates/provenance.py"
    text = path.read_text(encoding="utf-8")
    marker = "    if not agreement.agree and not _is_contained("
    if marker not in text:
        return False
    start = text.index(marker)
    end = text.index("):", start) + len("):")
    path.write_text(text[:start] + "    if False:" + text[end:], encoding="utf-8")
    return True


def _report_an_unreadable_page_as_verified(root: Path) -> bool:
    """Treat a page nothing could open as a page that checked out.

    Attestor's laundering, in this project's costume: "we could not check" becomes "it was
    fine". A field whose provenance nothing has looked at has not been verified, and a batch
    that lost its rasters would publish everything.
    """
    path = root / "src/manifest/gates/provenance.py"
    text = path.read_text(encoding="utf-8")
    marker = "verdict=Verdict.UNCHECKABLE"
    if text.count(marker) < 1:
        return False
    path.write_text(text.replace(marker, "verdict=Verdict.VERIFIED"), encoding="utf-8")
    return True


def _let_a_container_number_skip_its_own_arithmetic(root: Path) -> bool:
    """Stop checking a self-checking field.

    Free and absolute, so removing it looks like removing redundancy. What it removes is the
    only check on any of these documents that can prove a read wrong without a second opinion.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "    if provenance.self_checking:",
        "    if False:",
    )


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "reach for the cloud from inside the core",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "boto3",
        _reach_for_the_cloud_from_the_core,
        "Nothing fails on the machine that wrote it; the suite just stops being runnable by "
        "a stranger, and that was the whole of the evidence.",
    ),
    Mutation(
        "let the core learn which engine read the page",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "engine name",
        _let_the_core_learn_which_engine_read_the_page,
        "A comment, which is how it starts. The branch arrives later and nobody remembers "
        "the comment was the first step.",
    ),
    Mutation(
        "read the clock inside the core",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "ambient state",
        _read_the_clock_inside_the_core,
        "Reads as observability. It is claim 3: the same document, the same engine version, "
        "a different record.",
    ),
    Mutation(
        "check only imports for an engine name, not the source text",
        "core purity",
        ["pytest", "-q", "tests/gates/test_core_purity.py", "-x"],
        "test_an_engine_named_anywhere_is_refused",
        _let_an_engine_name_in_as_long_as_it_is_not_an_import,
        "The plausible fix for a false positive. It removes the half of the rule that catches "
        "a string literal, a dictionary key and a branch.",
    ),
    Mutation(
        "round a crop to the nearest pixel",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_a_crop_is_never_smaller_than_the_box_it_came_from",
        _round_a_crop_to_the_nearest_pixel,
        "Makes claim 2 fail in the dangerous direction: the verifier re-reads a damaged crop "
        "and reports a correct record as false.",
    ),
    Mutation(
        "let two boxes that only touch overlap",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_boxes_that_only_touch_do_not_intersect",
        _let_two_boxes_that_only_touch_overlap,
        "Exactly what the first version of the module did. The gate has to keep refusing the "
        "version that was already written once.",
    ),
    Mutation(
        "clamp a coordinate that left the page instead of refusing it",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_a_coordinate_far_outside_the_page_is_refused_rather_than_clamped",
        _clamp_a_coordinate_that_left_the_page,
        "Reads as robustness. What it accepts is an adapter with a sign error, and an archive "
        "of provenance records all pointing at the top-left corner. Accepted on first run — "
        "the gap was in the suite, and only planting the violation showed it.",
    ),
    Mutation(
        "let a blank crop verify",
        "provenance · ink",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_blank_crop_is_refused_by_the_ink_layer",
        _let_a_blank_crop_verify,
        "Not a deletion — a tuning. Lowered once to stop a complaint, and again, until it "
        "measures ink and never refuses on it.",
    ),
    Mutation(
        "let a shorter re-read verify a longer record",
        "provenance · containment",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_reread_carrying_less_than_the_record_is_refused",
        _let_a_shorter_reread_verify_a_longer_record,
        "Reads as symmetry. Accepts `89` as verification of `8959`, which is the direction "
        "where the difference is money.",
    ),
    Mutation(
        "stop re-reading the crop",
        "provenance · second path",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_ink_present_but_the_wrong_ink_is_refused_by_the_reread_layer",
        _stop_re_reading_the_crop,
        "The expensive layer. Layer A still runs, so most of claim 2 keeps its shape while the "
        "only layer that can tell ink from the *right* ink stops running.",
    ),
    Mutation(
        "report an unreadable page as verified",
        "provenance · uncheckable",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_page_that_cannot_be_read_is_uncheckable_rather_than_verified",
        _report_an_unreadable_page_as_verified,
        "'We could not check' becoming 'it was fine'. A batch that lost its rasters would "
        "publish everything.",
    ),
    Mutation(
        "let a container number skip its own arithmetic",
        "provenance · arithmetic",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_self_checking_field_is_refused_by_its_own_arithmetic_first",
        _let_a_container_number_skip_its_own_arithmetic,
        "Looks like removing redundancy. Removes the only check on these documents that can "
        "prove a read wrong without a second opinion.",
    ),
)


# ── Running them ─────────────────────────────────────────────────────────────


def _argv(command: list[str]) -> list[str]:
    """The full argv for a mutation's check, run out of the mutated copy.

    `python -m pytest` rather than `pytest`, so the copy is the code under test rather than
    whatever is on PATH. A bare script path is run as a script; anything else is passed
    through untouched.
    """
    if command[0] == "pytest":
        return [sys.executable, "-m", "pytest", *command[1:]]
    return list(command)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # PYTHONPATH points at the *copy*, not at the editable install. Without this the mutation
    # is planted in a directory nothing imports from, every gate passes, and the script
    # reports a perfect score while proving nothing at all.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(cwd / "src")
    return subprocess.run(  # noqa: S603 — fixed command lists, no shell
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def main() -> int:
    print("gate-proof: establishing the baseline")
    # `tests/` only, and deliberately not the slow eval harnesses: the baseline exists to prove
    # the repository is green before a violation is planted, and a baseline that took four
    # minutes would be a gate-proof nobody ran while changing a gate.
    baseline = _run([sys.executable, "-m", "pytest", "-q"], ROOT)
    if baseline.returncode != 0:
        print("the suite is not green; every mutation below would be meaningless", file=sys.stderr)
        print(baseline.stdout[-4000:], file=sys.stderr)
        return 1
    print("  baseline green\n")

    passes: list[str] = []
    failures: list[str] = []
    stale: list[str] = []

    for mutation in MUTATIONS:
        with tempfile.TemporaryDirectory() as raw:
            copy = Path(raw) / "manifest"
            shutil.copytree(
                ROOT,
                copy,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    ".venv-checkov",
                    ".git",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    "out",
                    # The rendered corpus is gigabytes and no mutation touches it. Copying it
                    # per mutation would make this take an hour, and a proof nobody has time
                    # to run is a proof that stops being run.
                    "rendered",
                ),
            )
            try:
                applied = mutation.apply(copy)
            except (ValueError, OSError) as exc:
                applied = False
                print(f"         ({type(exc).__name__}: {exc})")
            if not applied:
                stale.append(mutation.name)
                print(f"  STALE  {mutation.name} — its target has moved; the proof is not running")
                continue

            result = _run(_argv(mutation.command), copy)
            output = (result.stdout + result.stderr).lower()

            if result.returncode == 0:
                failures.append(mutation.name)
                print(f"  FAIL   {mutation.name} — {mutation.gate} accepted the violation")
            elif mutation.expect.lower() not in output:
                failures.append(mutation.name)
                print(
                    f"  FAIL   {mutation.name} — something failed, but not {mutation.gate}; "
                    f"{mutation.expect!r} is absent from the output"
                )
            else:
                passes.append(mutation.name)
                print(f"  ok     {mutation.name} — refused by {mutation.gate}")

    print()
    print(f"gate-proof: {len(passes)} refused, {len(failures)} accepted, {len(stale)} stale")
    if stale:
        print("\nstale mutations point at code that has moved. Update them:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
    return 1 if failures or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
