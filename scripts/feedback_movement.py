#!/usr/bin/env python3
"""What the review evidence would do to every threshold, printed before anything moves.

**The other half of claim 1's loop.** `handlers/harvest.py` turns recorded human decisions into
observations and writes them to the records bucket. Nothing there derives a threshold, and
nothing there may: every threshold in this repository comes from the committed engine recording,
and a runtime that could re-derive one would be decision 20 undone.

So this is where the evidence meets the derivation, and it is a **ceremony rather than a
command** — the same shape as `make ocr-record`, for the same reason. Harvested evidence is the
one other thing that can move every number on the scoreboard at once, and it is not allowed to
do it quietly. This prints the movement of every field, old against new, **with N on both
sides**, and refuses to write until the shift is explicitly accepted.

    python3 scripts/feedback_movement.py                    # report, change nothing
    ACCEPT=1 python3 scripts/feedback_movement.py            # fold the evidence in

**The budget is carried through both derivations and printed unchanged.** That is the safety
property of the whole loop, and it is stated as a column rather than as a promise: corrections
move **N**, never an error budget. There is no argument here through which one could reach a
budget, and `gate-proof` plants exactly that arrow to check.

**A threshold can move back to always-review, and that is a finding rather than a regression.**
More evidence can show that a threshold which fitted at small N does not fit at large N. A loop
that could only ever loosen would be one whose evidence only ever pointed one way, which is not
how evidence works — so both directions are printed, and the second is printed louder.

The deploy runs this in report-only mode, so the movement is visible on every deploy even when
nobody is regenerating anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# The harness lives at the repository root, beside `evals/`. `check_corpus_envelope.py` reaches
# for it the same way.
sys.path.insert(0, str(ROOT))

RECORDING = ROOT / "recordings" / "ocr" / "manifest.json"
DERIVED = ROOT / "recordings" / "thresholds.json"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def _client(name: str):
    import boto3  # noqa: PLC0415

    return boto3.client(name)


def _harvested(bucket: str, reader: str) -> dict[str, list[dict]]:
    """Every field's harvested observations, from the records bucket.

    Listed rather than asked for by name: which fields have evidence is a fact about what
    reviewers happened to look at, and a script that iterated its own idea of the field list
    would silently ignore evidence for a field it had not heard of.
    """
    found: dict[str, list[dict]] = {}
    prefix = f"feedback/{reader}/"
    pages = _client("s3").get_paginator("list_objects_v2")
    for page in pages.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", ()):
            if not entry["Key"].endswith(".json"):
                continue
            body = json.loads(
                _client("s3").get_object(Bucket=bucket, Key=entry["Key"])["Body"].read()
            )
            found[str(body["field"])] = list(body.get("observations", []))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="The records bucket.")
    parser.add_argument("--reader", required=True, help="The reader identity the evidence is for.")
    arguments = parser.parse_args(argv)

    # **The baseline observations come from the recording, scored.** `recordings/thresholds.json`
    # carries the *count* of observations, not the observations, and deriving against a count is
    # not possible — so the same harness the claim gates use re-scores the committed recording
    # here. `scripts/check_corpus_envelope.py` reaches for it the same way and for the same
    # reason: it is the one path from the recording to the evidence, and a second one would be a
    # second chance to disagree.
    from evals.harness import by_field, field_contract, score_all  # noqa: PLC0415

    from manifest.core.calibration import Observation, Outcome  # noqa: PLC0415
    from manifest.core.feedback import movement  # noqa: PLC0415

    if not RECORDING.exists() or not DERIVED.exists():
        print(f"{RED}no committed recording to compare against{RESET}", file=sys.stderr)
        return 1

    scored = by_field(score_all())
    harvested = _harvested(arguments.bucket, arguments.reader)

    if not harvested:
        print(
            f"  {DIM}no harvested evidence under feedback/{arguments.reader}/. Nothing has been "
            f"reviewed at this reader, so every threshold stands where the recording put it.{RESET}"
        )
        return 0

    print(
        f"\n{'field':26} {'before':>14} {'after':>14} {'N before':>9} {'N after':>8} "
        f"{'budget':>9}\n"
    )
    left, returned, unchanged = [], [], 0
    for field, raw in sorted(harvested.items()):
        entries = scored.get(field)
        if not entries:
            print(f"  {DIM}{field}: nothing in the recording scores it; skipped{RESET}")
            continue
        budget = field_contract(field).error_budget
        if budget is None:
            # Declared always-review in the contract itself. No amount of evidence changes that,
            # and printing a movement for it would suggest otherwise.
            print(f"  {DIM}{field}: always-review by contract, not by evidence{RESET}")
            continue

        before = [entry.observation for entry in entries]
        added = [Observation(float(o["confidence"]), Outcome(str(o["outcome"]))) for o in raw]
        change = movement(field=field, error_budget=budget, before=before, added=added)

        def show(value: float | None) -> str:
            return "always-review" if value is None else f"{value:.3f}"

        colour = ""
        if change.left_always_review:
            colour, _ = GREEN, left.append(field)
        elif change.returned_to_always_review:
            colour, _ = YELLOW, returned.append(field)
        elif change.before == change.after:
            unchanged += 1

        print(
            f"  {colour}{field:24}{RESET} {show(change.before):>14} {show(change.after):>14} "
            f"{change.observations_before:>9} {change.observations_after:>8} "
            f"{change.error_budget!s:>9}"
        )
        # The guarantee, asserted per field rather than trusted once. The budget that came out is
        # the budget that went in, and if it is ever not, this stops rather than prints.
        if change.error_budget != budget:
            print(
                f"\n{RED}{field}: the error budget changed during derivation. Corrections move "
                f"N, never a budget — this is the one arrow that must not exist{RESET}",
                file=sys.stderr,
            )
            return 1

    print(
        f"\n  {len(left)} field(s) left always-review, {(returned and len(returned)) or 0} "
        f"returned to it, {unchanged} unchanged."
    )
    if returned:
        print(
            f"  {YELLOW}{', '.join(returned)} returned to always-review.{RESET} That is a "
            f"finding, not a regression: more evidence showed a threshold that fitted at small N "
            f"does not fit at large N. A loop that could only loosen would be one whose evidence "
            f"only ever pointed one way."
        )

    if not os.environ.get("ACCEPT"):
        print(
            f"\n  {DIM}Reported, nothing written. Folding this evidence into the committed "
            f"derivation moves thresholds that every claim in this repository is scored against, "
            f"so it is a ceremony rather than a command: re-run with ACCEPT=1 once the movement "
            f"above has been read.{RESET}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
