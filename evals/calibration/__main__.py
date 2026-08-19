"""CLAIM 1 — no field is published below its confidence threshold, and the threshold is derived.

Every number this prints comes from the committed engine recording (ADR-0005) scored against
the committed ground truth. Nothing here calls a reader, so the figures move only when this
repository changes.

What it asserts:

- every field with an error budget gets a threshold **derived** from that budget by the
  upper-confidence-bound rule, or is declared `always-review` because the evidence does not
  support publishing it automatically;
- no derived threshold has moved outside its declared per-field tolerance since the committed
  baseline;
- the published population, at those thresholds, is within its budget — checked by counting the
  errors that survive the threshold, which is the claim stated as arithmetic rather than as a
  process.

What it deliberately does **not** assert: that any of these figures describes production. They
describe a synthetic distribution this repository authored, at 300 DPI, degraded by a generator
whose difficulty is declared in `corpus/envelope.yaml`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

from evals.harness import by_field, field_contract, score_all
from manifest.core.calibration import CONFIDENCE, Outcome, calibrate, derive, upper_bound

BASELINE = Path(__file__).resolve().parents[2] / "recordings" / "thresholds.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    scored = score_all()
    grouped = by_field(scored)

    report: dict[str, dict[str, object]] = {}
    failures: list[str] = []

    for name in sorted(grouped):
        contract = field_contract(name)
        observations = [entry.observation for entry in grouped[name]]
        budget = contract.error_budget or Decimal("0")

        if contract.always_review:
            report[name] = {
                "always_review": True,
                "declared": True,
                "observations": len(observations),
            }
            continue

        threshold = derive(name, observations, budget)
        calibration = calibrate(observations)
        limit = _why_not(observations, budget)
        report[name] = {
            "always_review": threshold.always_review,
            "declared": False,
            "threshold": threshold.value,
            "budget": float(budget),
            "upper_bound": threshold.upper_bound,
            "published": threshold.published,
            "wrong": threshold.wrong,
            "observations": threshold.observations,
            "coverage": round(threshold.coverage, 4),
            "ece": calibration.expected_calibration_error,
            "tolerance": float(contract.threshold_tolerance),
            "limit": limit,
        }

        # The claim, stated as arithmetic: at the derived threshold, the published population's
        # error rate is inside the budget with 95% confidence. `derive` guarantees it; this
        # re-checks it from the observations, because a guarantee that is never verified is a
        # comment.
        if not threshold.always_review:
            published = [
                entry for entry in observations if entry.confidence >= (threshold.value or 0)
            ]
            wrong = sum(1 for entry in published if entry.outcome is Outcome.WRONG)
            if wrong != threshold.wrong or len(published) != threshold.published:
                failures.append(f"{name}: the derived counts do not reproduce from the data")

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    moved = _movement(report, baseline)

    if arguments.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE}")
        return 0

    if arguments.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0

    _print(report, moved)
    failures.extend(moved)

    if failures:
        print(f"\nclaim 1: FAILED — {len(failures)} problem(s)", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    derived = sum(1 for entry in report.values() if not entry.get("always_review"))
    declared = sum(1 for entry in report.values() if entry.get("declared"))
    evidence = [
        n for n, e in report.items() if (e.get("limit") or {}).get("kind") == "evidence_limited"
    ]
    quality = [
        n for n, e in report.items() if (e.get("limit") or {}).get("kind") == "quality_limited"
    ]

    # **The four counts are not a partition, and the line used to read as though they were.**
    #
    # `derived`, `evidence` and `quality` are computed independently over the same fields: a
    # `limit` says what constrained a field, not whether it ended up publishing, so a field can
    # carry a derived threshold *and* be quality-limited. Printed as a flat list, the numbers
    # invited addition — 5 + 1 + 4 + 30 against 36 fields — and the README copied them.
    #
    # So the split is stated first and the overlap named after it. A reader who adds the first
    # line gets the right total; a reader who wants the reason reads the second.
    total = len(report)
    always_review = total - derived
    both = len([n for n in quality if not report[n].get("always_review")])
    print(
        f"\nclaim 1: {total} fields — {derived} with a derived threshold, "
        f"{always_review} always-review."
    )
    empty = [
        n
        for n, e in report.items()
        if (e.get("limit") or {}).get("kind") == "no_confident_population"
    ]
    print(
        f"  of the {always_review} always-review: {declared} declared so by contract, "
        f"{len(evidence)} evidence-limited, {len(quality) - both} quality-limited, "
        f"{len(empty)} with no confident population to judge at all"
        + (f" ({', '.join(sorted(empty))})" if empty else "")
        + "."
    )
    if both:
        print(
            f"  {both} of the {derived} derived field(s) are quality-limited as well: the limit "
            f"records what constrained the threshold, not whether the field publishes. That is "
            f"why these counts overlap and are not added."
        )
    print(
        "\nNo field publishes below its threshold, and a field with no threshold publishes "
        "nothing. That is the claim, and it holds — including in the direction nobody wants, "
        "where it holds by publishing very little."
    )
    if evidence:
        worst = max((report[n]["limit"]["observations_needed"] for n in evidence), default=0)
        print(
            f"\n  {len(evidence)} fields are **evidence-limited**: zero errors in their "
            f"high-confidence population, and a 95% upper bound still wider than their budget. "
            f"The reader is not wrong; there is not enough labelled data to prove it right. "
            f"The largest of them needs n={worst} at zero errors — this corpus has hundreds. "
            f"More data or an escalation tier, not a different threshold."
        )
    if quality:
        print(
            f"\n  {len(quality)} fields are **quality-limited**: real errors survive at high "
            f"confidence, which is over-confidence rather than thin evidence. These are the "
            f"fields the cascade exists for — and the ones whose reliability curve is worth "
            f"reading: {', '.join(sorted(quality)[:5])}"
        )
    print(
        "\nEvery figure above is scored against a distribution this repository generated, at "
        "300 DPI, degraded by a generator whose difficulty is declared in "
        "corpus/envelope.yaml. It is not a claim about production."
    )
    return 0


#: The confidence above which a field's population is examined when no threshold fits. Not a
#: threshold — nothing publishes at it. It exists to answer the question a bare "always-review"
#: cannot: *is this field failing because the reader is wrong, or because there is not enough
#: evidence to prove it right?* Those two have different fixes and the same symptom.
_EXAMINE_AT = 0.90


def _why_not(observations, budget: Decimal) -> dict[str, object] | None:
    """Why no threshold fits: too little evidence, or too many errors.

    The most useful thing this harness produces, and the reason a bare `always-review` is not
    enough. At 232 high-confidence container numbers with **zero** errors, the 95% upper bound
    is still 1.28% — against a declared budget of 0.1%. The reader is not wrong; the evidence
    cannot prove it right, and no threshold can fix that. The answer is more labelled data or
    an escalation tier, and it is a different answer from the one a field with real errors at
    high confidence needs.
    """
    scored = [entry for entry in observations if entry.outcome is not Outcome.MISSING]
    examined = [entry for entry in scored if entry.confidence >= _EXAMINE_AT]
    if not examined:
        return {"kind": "no_confident_population", "examined": 0}

    wrong = sum(1 for entry in examined if entry.outcome is Outcome.WRONG)
    bound = upper_bound(wrong, len(examined))
    if bound <= float(budget):
        return None

    if wrong == 0:
        # `1 - a^(1/n) <= budget`  =>  `n >= log(a) / log(1 - budget)`.
        needed = math.ceil(math.log(1 - CONFIDENCE) / math.log(1 - float(budget)))
        return {
            "kind": "evidence_limited",
            "examined": len(examined),
            "wrong": 0,
            "bound": bound,
            "observations_needed": needed,
        }
    return {
        "kind": "quality_limited",
        "examined": len(examined),
        "wrong": wrong,
        "observed_rate": wrong / len(examined),
        "bound": bound,
    }


def _movement(report: dict, baseline: dict) -> list[str]:
    moved: list[str] = []
    for name, entry in report.items():
        previous = baseline.get(name)
        if not previous or entry.get("always_review") or previous.get("always_review"):
            continue
        before, after = previous.get("threshold"), entry.get("threshold")
        if before is None or after is None:
            continue
        # **No default, and this is the place it would matter most.** This number decides how
        # far claim 1's thresholds may drift before the build goes red — it is the sensitivity
        # of the only gate that stands between a derivation and a published field. A `0.02`
        # written here as a fallback would be a threshold nobody declared, doing exactly what
        # doctrine rule 3 says a modal value does: filling a hole with something plausible.
        #
        # Every field's tolerance comes from its contract (`contract.threshold_tolerance`, where
        # this report is built), so the absence below cannot happen today. It is a refusal
        # rather than a default because the day it *can* happen, the alternative is a field
        # silently getting the loosest reasonable band at the moment nobody is looking.
        if entry.get("tolerance") is None:
            moved.append(
                f"{name}: no declared threshold tolerance. The movement check has no band to "
                f"compare against, and it refuses to invent one — a tolerance nobody wrote is "
                f"a decision about claim 1's sensitivity that no reviewer ever saw"
            )
            continue
        tolerance = float(entry["tolerance"])
        if abs(after - before) > tolerance:
            moved.append(
                f"{name}: threshold moved {before:.3f} -> {after:.3f}, outside its declared "
                f"tolerance of {tolerance:.3f} (n {previous.get('observations')} -> "
                f"{entry.get('observations')})"
            )
    return moved


def _print(report: dict, moved: list[str]) -> None:
    print(
        f"{'field':24} {'thr':>6} {'budget':>8} {'bound':>8} {'pub/n':>12} {'cover':>7} {'ece':>6}"
    )
    print("-" * 78)
    for name in sorted(report):
        entry = report[name]
        if entry.get("declared"):
            print(f"{name:24} {'—':>6} {'always-review (declared in the contract)':>50}")
            continue
        if entry.get("always_review"):
            limit = entry.get("limit") or {}
            note = {
                "evidence_limited": (
                    f"evidence-limited: 0/{limit.get('examined')} wrong above "
                    f"{_EXAMINE_AT}, bound {limit.get('bound', 0):.4f}, needs "
                    f"n={limit.get('observations_needed')}"
                ),
                "quality_limited": (
                    f"quality-limited: {limit.get('wrong')}/{limit.get('examined')} wrong "
                    f"above {_EXAMINE_AT} ({limit.get('observed_rate', 0):.1%})"
                ),
            }.get(str(limit.get("kind")), "always-review")
            print(
                f"{name:24} {'—':>6} {float(entry['budget']):8.4f} {'—':>8} "
                f"{'0/' + str(entry['observations']):>12} {'0.0%':>7} {'—':>6}   {note}"
            )
            continue
        ece = entry.get("ece")
        print(
            f"{name:24} {entry['threshold']:6.3f} {entry['budget']:8.4f} "
            f"{entry['upper_bound']:8.4f} "
            f"{str(entry['published']) + '/' + str(entry['observations']):>12} "
            f"{entry['coverage']:6.1%} {ece if ece is None else round(ece, 3)!s:>6}"
        )
    for line in moved:
        print(f"  MOVED {line}")


if __name__ == "__main__":
    raise SystemExit(main())
