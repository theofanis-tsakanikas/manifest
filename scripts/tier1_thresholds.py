#!/usr/bin/env python3
"""Derive tier 1's thresholds from the Textract recording, and say what they buy.

**The one number this changes is the one that caps everything else.** Thirty of forty fields are
*quality-limited* at tier 0 — real errors survive at high confidence, and no threshold fixes
over-confidence. The cascade exists for those pages, and until now tier 1 could not help: it
reports a confidence per word and **no threshold here was derived from it**, so an escalated
value could be rescued for a human and never published.

This runs the derivation that already exists over the second recording. Same corpus, same
labelled set, same contracts, same `core.calibration.derive`, same error budgets — the only thing
that changes is which reader produced the confidences. That is the whole design of the
normalised representation, arriving at the place it was built for.

    python3 scripts/tier1_thresholds.py            # derive and report, write nothing
    ACCEPT=1 python3 scripts/tier1_thresholds.py   # write recordings/thresholds.textract.json

**A ceremony, like every other thing that moves a threshold.** The report prints each field at
both tiers, side by side, with N — and the honest outcome is allowed to be *"tier 1 is no
better"*: Textract may be exactly as over-confident on these pages. That answer costs three
dollars and fifty cents to have instead of assume, and it is written down either way.

**What it does not do.** It does not let tier 1 publish. That is a change to
`contracts/cascade/routing.yaml`, and it should be made by a person who has read the table below
— which is the same rule `make ocr-record` follows for the reader that already publishes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

RECORDING = ROOT / "recordings" / "textract"
OUT = ROOT / "recordings" / "thresholds.textract.json"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def main() -> int:
    if not (RECORDING / "manifest.json").exists():
        print(
            f"{RED}{RECORDING} does not exist.{RESET} Record it first:\n"
            f"  python3 scripts/textract_record.py --record",
            file=sys.stderr,
        )
        return 1

    # Imported here rather than at the top, because the refusal above must be readable on a
    # machine where the recording does not exist — and importing the harness scores the whole
    # tier-0 corpus on the way in.
    from evals.harness import by_field, field_contract, score_all  # noqa: PLC0415

    from manifest.core.calibration import derive  # noqa: PLC0415
    from manifest.extraction.local.recording import read_manifest  # noqa: PLC0415

    manifest = read_manifest(RECORDING)
    committed = (ROOT / "corpus/ground_truth/fingerprint.txt").read_text(encoding="utf-8").strip()
    if manifest.corpus_fingerprint != committed:
        # A recording of a corpus that has since been regenerated is a recording of different
        # pages. Comparing its thresholds against tier 0's would be comparing two readers on two
        # corpora and calling the difference the reader.
        print(
            f"{RED}the recording is of corpus {manifest.corpus_fingerprint[:16]} and the "
            f"committed corpus is {committed[:16]}{RESET}",
            file=sys.stderr,
        )
        return 1

    tier0 = by_field(score_all())
    tier1 = by_field(score_all(RECORDING))

    print(
        f"\n  reader {manifest.reader_name}@{manifest.reader_version}, "
        f"{manifest.pages:,} page(s), {manifest.words:,} word(s)\n"
    )
    print(f"  {'field':26} {'tier 0':>14} {'N':>7}   {'tier 1':>14} {'N':>7}   {'budget':>8}\n")

    derived: dict[str, dict[str, object]] = {}
    gained, lost, unchanged = [], [], 0
    for field in sorted(set(tier0) | set(tier1)):
        budget = field_contract(field).error_budget
        if budget is None:
            continue
        before = derive(field, [entry.observation for entry in tier0.get(field, [])], budget)
        after = derive(field, [entry.observation for entry in tier1.get(field, [])], budget)

        def show(value: float | None) -> str:
            return "always-review" if value is None else f"{value:.3f}"

        colour = ""
        if before.value is None and after.value is not None:
            colour, _ = GREEN, gained.append(field)
        elif before.value is not None and after.value is None:
            colour, _ = YELLOW, lost.append(field)
        else:
            unchanged += 1

        print(
            f"  {colour}{field:24}{RESET} {show(before.value):>14} {before.observations:>7}   "
            f"{show(after.value):>14} {after.observations:>7}   {budget!s:>8}"
        )
        derived[field] = {
            "threshold": after.value,
            "always_review": after.value is None,
            "observations": after.observations,
            "error_budget": str(budget),
        }

    print(
        f"\n  {GREEN}{len(gained)} field(s) tier 1 can publish that tier 0 cannot{RESET}"
        f" · {len(lost)} the other way · {unchanged} unchanged"
    )
    if gained:
        print(f"  {DIM}{', '.join(gained)}{RESET}")
    else:
        print(
            f"  {DIM}Tier 1 is no better on this corpus, and that is a result rather than a "
            f"failure: the escalation buys a reading for a human and not a published field. "
            f"Three dollars fifty to know it instead of assuming it, with an N and a date.{RESET}"
        )

    if not os.environ.get("ACCEPT"):
        print(
            f"\n  {DIM}Reported, nothing written. Re-run with ACCEPT=1 to write "
            f"{OUT.relative_to(ROOT)}; letting tier 1 *publish* is a separate, deliberate edit "
            f"to contracts/cascade/routing.yaml by somebody who has read the table above.{RESET}"
        )
        return 0

    OUT.write_text(json.dumps(derived, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n  {GREEN}ok{RESET}    wrote {OUT.relative_to(ROOT)} for {len(derived)} field(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
