"""The envelope, applied to arriving traffic instead of to the generator.

`scripts/check_corpus_envelope.py` turns the build red when the **generator** leaves the declared
operating range. This harness proves the same declaration works on the other side: given a window
of arriving documents, it says whether what is coming in still resembles what every threshold in
this repository was derived from.

**The property under test is that the check bites in both directions.** A drift detector that only
fires when quality falls is half a detector. Confidence going *up* is the more alarming direction
and the one nobody watches: a reader suddenly certain about pages it used to abstain on has
usually stopped seeing something — a preprocessing change, a different rasteriser, a template
whose fields moved under a caption that still matches.

Three windows are run: the corpus itself, which must sit inside; a degraded window, which must be
caught; and an improved one, which must also be caught. Plus a window too small to have an
opinion, which must come back `UNDECIDED` rather than `INSIDE`.

**Nothing here adjusts anything.** No band widens, no threshold moves. An envelope that expanded
to accommodate the traffic would be a control agreeing with whatever happened.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from evals.harness import score_all
from manifest.core.calibration import Outcome
from manifest.core.drift import Band, Verdict, Window, assess

ENVELOPE = Path(__file__).resolve().parents[2] / "corpus" / "envelope.yaml"

#: The declared floor for a window to have an opinion. A scenario parameter — see
#: `manifest.core.drift.assess`, which refuses to default it.
MINIMUM_DOCUMENTS = 50


def _bands() -> tuple[Band, Band]:
    declared = yaml.safe_load(ENVELOPE.read_text(encoding="utf-8"))["overall"]
    confidence = declared["median_confidence"]
    abstention = declared["abstention_rate"]
    return (
        Band("median_confidence", float(confidence["min"]), float(confidence["max"])),
        Band("abstention_rate", float(abstention["min"]), float(abstention["max"])),
    )


def _window_from_corpus(shift: float = 0.0) -> Window:
    """The corpus as an arriving window, optionally shifted to simulate a change upstream.

    `shift` moves every confidence by a constant. That is a crude model of a scanning change and
    it is labelled one: what it exercises is whether the *detector* fires, not what a real
    supplier change would look like. A more elaborate simulation would be a second invented
    distribution presented as though it described the world.
    """
    entries = score_all()
    confidences = [
        min(max(entry.extracted.confidence + shift, 0.0), 1.0)
        for entry in entries
        if entry.extracted.confidence is not None
    ]
    documents = len({(entry.shipment, entry.document) for entry in entries})
    abstained = sum(1 for entry in entries if entry.outcome is Outcome.MISSING)
    return Window(
        documents=documents,
        confidences=tuple(confidences),
        unscored_documents=0,
        abstained_fields=abstained,
        total_fields=len(entries),
    )


def main() -> int:
    confidence_band, abstention_band = _bands()
    problems: list[str] = []

    print("production drift — the declared envelope, applied to arriving traffic\n")
    low, high = confidence_band.lower, confidence_band.upper
    print(f"    declared median confidence  [{low:.3f}, {high:.3f}]")
    low, high = abstention_band.lower, abstention_band.upper
    print(f"    declared abstention rate    [{low:.1%}, {high:.1%}]")
    print(f"    minimum window              {MINIMUM_DOCUMENTS} documents\n")

    scenarios = [
        ("the corpus itself", 0.0, False),
        ("scanning quality falls (-0.25)", -0.25, True),
        ("reader suddenly certain (+0.25)", 0.25, True),
    ]

    for name, shift, expect_drift in scenarios:
        window = _window_from_corpus(shift)
        findings = assess(
            window=window,
            confidence_band=confidence_band,
            abstention_band=abstention_band,
            minimum_documents=MINIMUM_DOCUMENTS,
        )
        drifted = [finding for finding in findings if finding.drifted]
        verdict = "DRIFTED" if drifted else "inside"
        print(f"  {name}: {verdict}  ({window.documents:,} documents)")
        for finding in findings:
            mark = "  <-- " if finding.drifted else "      "
            print(f"    {mark}{finding.reason}")
        print()

        if expect_drift and not drifted:
            problems.append(
                f"{name}: the envelope did not fire. A drift detector that misses a shift this "
                f"large is a green light over thresholds that no longer describe the traffic"
            )
        if not expect_drift and drifted:
            problems.append(
                f"{name}: the envelope fired on the corpus its own bands were declared for. A "
                f"detector that cries on the baseline is one somebody mutes in a week"
            )

    # Too small to have an opinion.
    small = Window(
        documents=9,
        confidences=(0.9,) * 40,
        unscored_documents=0,
        abstained_fields=1,
        total_fields=40,
    )
    findings = assess(
        window=small,
        confidence_band=confidence_band,
        abstention_band=abstention_band,
        minimum_documents=MINIMUM_DOCUMENTS,
    )
    undecided = all(finding.verdict is Verdict.UNDECIDED for finding in findings)
    print(f"  a window of 9 documents: {'UNDECIDED' if undecided else 'gave a verdict'}")
    print(f"    {findings[0].reason}")
    if not undecided:
        problems.append(
            "a window below the declared minimum produced a verdict. `UNDECIDED` and `INSIDE` "
            "are different answers: a quiet Sunday must not read as evidence that nothing "
            "changed"
        )

    if problems:
        print("\ndrift: FAILED\n")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(
        "\ndrift: the declared envelope fires in both directions and abstains on a window too "
        "\n  small to judge. Nothing here adjusts a band or a threshold."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
