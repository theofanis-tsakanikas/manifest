#!/usr/bin/env python3
"""The generator stays inside the operating range it declared. `docs/DECISIONS.md` 20.

A corpus degraded too gently puts every confidence at the top of the range: the reliability
curve is flat, ECE measures nothing, every threshold is trivially satisfiable. Degraded too hard
and everything abstains. **Both report green**, which is why the band is declared in
`corpus/envelope.yaml` and checked here rather than noticed by somebody reading the numbers.

The failure message names the direction, because "out of band" leaves the reader to work out
whether the corpus got easier or harder — and those have opposite fixes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# `evals/` is not part of the installed package — a set of labelled scenarios has no business
# shipping in a wheel — so the repository root goes on the path here. `conftest.py` does the
# same for the suite; a script that only ran under pytest would be a check nobody could run by
# hand.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import score_all

from manifest.core.calibration import Outcome

#: The confidence below which a read is "low". Half, because it is the point at which a score
#: that behaved like a probability would be no better than not reading the field at all.
LOW_CONFIDENCE = 0.5

ENVELOPE = Path(__file__).resolve().parents[1] / "corpus" / "envelope.yaml"


def _figures(entries) -> dict[str, float]:
    located = [entry for entry in entries if entry.outcome is not Outcome.MISSING]
    missing = len(entries) - len(located)
    wrong = sum(1 for entry in located if entry.outcome is Outcome.WRONG)
    confidences = sorted(entry.extracted.confidence for entry in located)
    return {
        "abstention_rate": missing / len(entries) if entries else 0.0,
        "error_rate_of_located": wrong / len(located) if located else 0.0,
        "median_confidence": confidences[len(confidences) // 2] if confidences else 0.0,
        "fraction_below_half": (
            sum(1 for value in confidences if value < LOW_CONFIDENCE) / len(confidences)
            if confidences
            else 0.0
        ),
    }


def _check(where: str, figures: dict[str, float], bands: dict) -> list[str]:
    problems = []
    for name, band in bands.items():
        actual = figures[name]
        if actual < band["min"]:
            problems.append(
                f"{where}.{name} is {actual:.4f}, below its declared floor of {band['min']}. "
                f"The corpus has become EASIER. A claim scored on it is now a claim about a "
                f"document set nobody would have called degraded"
            )
        elif actual > band["max"]:
            problems.append(
                f"{where}.{name} is {actual:.4f}, above its declared ceiling of {band['max']}. "
                f"The corpus has become HARDER. Every claim is now scored against pages the "
                f"reader largely cannot read, which measures the generator rather than the system"
            )
    return problems


def main() -> int:
    envelope = yaml.safe_load(ENVELOPE.read_text(encoding="utf-8"))
    scored = score_all()

    problems = _check("overall", _figures(scored), envelope["overall"])

    by_document: dict[str, list] = {}
    for entry in scored:
        by_document.setdefault(entry.document, []).append(entry)
    for document, bands in envelope["documents"].items():
        if document not in by_document:
            problems.append(f"{document} is declared in the envelope and absent from the corpus")
            continue
        problems.extend(_check(document, _figures(by_document[document]), bands))

    if problems:
        print(f"envelope: {len(problems)} figure(s) outside the declared range\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    figures = _figures(scored)
    print(
        f"envelope: the corpus is inside its declared operating range "
        f"(median confidence {figures['median_confidence']:.3f}, "
        f"{figures['fraction_below_half']:.1%} below 0.5, "
        f"{figures['abstention_rate']:.1%} abstained, "
        f"{figures['error_rate_of_located']:.1%} of located values wrong)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
