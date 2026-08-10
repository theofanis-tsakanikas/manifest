"""The out-of-distribution column: does the reader's confidence mean the same thing on real paper?

**The circle this closes.** Every other figure in this repository is scored against a corpus this
repository generated, and the sharpest available challenge is *did you tune the generator until
the claims passed?* The declared envelope is the answer, and the envelope is also ours — two
declarations by the same author are not independent evidence.

This harness puts the reader's reliability curve on documents nobody here designed next to the one
on documents we did. `corpus/external/LICENCE.md` records which set, under what licence, read on
what date; `scripts/external_record.py` produces the recording.

**Read the result the right way round.** The accuracy on the external set is *much worse* than on
the generated corpus, and that is expected and is not the measurement. These are photographs of
thermal-printed receipts — creased, hand-held, unevenly lit — against rendered-and-degraded trade
documents. Comparing accuracy would be comparing difficulty.

What is comparable is **calibration**: whether a confidence of 0.9 means the same thing in both
places. A reader whose 0.9 is right 90% of the time on our paper and 60% of the time on real paper
is a reader whose confidences do not transport, and every threshold in this repository is a
statement about confidences.

**What this cannot do, and must not be read as doing.** These are receipts. No field in
`contracts/documents/` appears here, no threshold is derived from them, and this says nothing
about extraction accuracy on trade documents. It is one question, asked once: does the number the
reader emits behave like a probability when the paper was not ours?
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness import score_all
from manifest.core.calibration import Observation, Outcome, calibrate

RECORDING = Path(__file__).resolve().parents[2] / "recordings" / "external"

#: How far the two expected-calibration errors may differ before this is a finding rather than a
#: reassurance. Declared here, as a number somebody chose, because there is no principled value
#: for it — and a tolerance presented as derived would be exactly the thing this repository spends
#: its time refusing.
ECE_TOLERANCE = 0.15


def _external() -> list[Observation]:
    lines = (RECORDING / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        Observation(
            confidence=float(entry["confidence"]),
            outcome=Outcome.CORRECT if entry["correct"] else Outcome.WRONG,
        )
        for entry in (json.loads(line) for line in lines if line.strip())
    ]


def _print_curve(name: str, calibration) -> None:
    print(f"  {name}")
    ece = calibration.expected_calibration_error
    print(f"    observations {calibration.observations:,}   ECE {ece:.4f}")
    for entry in calibration.bins:
        if entry.count == 0:
            continue
        arrow = "over" if entry.gap > 0 else "under"
        print(
            f"      [{entry.lower:.1f}, {entry.upper:.1f})  n={entry.count:6,}  "
            f"mean conf {entry.mean_confidence:.3f}  accuracy {entry.accuracy:.3f}  "
            f"{arrow} by {abs(entry.gap):.3f}"
        )
    print()


def main() -> int:
    if not (RECORDING / "observations.jsonl").exists():
        print(
            "external: no recording present.\n"
            "  `make external-record` fetches the licensed set and produces one. Until then\n"
            "  this column is absent, which is a stated gap rather than a passing check — see\n"
            "  `corpus/external/README.md`."
        )
        return 0

    manifest = json.loads((RECORDING / "manifest.json").read_text(encoding="utf-8"))
    external = _external()
    generated = [entry.observation for entry in score_all()]

    print("the out-of-distribution column — does a confidence mean the same thing on real paper?\n")
    reader = f"{manifest['reader_name']}@{manifest['reader_version']}"
    print(f"  external set: {manifest['pages']} page(s), reader {reader}")
    print("  licence, source and date read: corpus/external/LICENCE.md")
    print("  no image from that set is redistributed here; these are confidences and flags\n")

    ours = calibrate(generated)
    theirs = calibrate(external)
    _print_curve("generated corpus (ours)", ours)
    _print_curve("real photographed paper (not ours)", theirs)

    difference = abs(ours.expected_calibration_error - theirs.expected_calibration_error)
    print(
        f"  ECE {ours.expected_calibration_error:.4f} on ours against "
        f"{theirs.expected_calibration_error:.4f} on theirs — a difference of {difference:.4f}.\n"
    )

    if difference <= ECE_TOLERANCE:
        print(
            f"  Within the declared tolerance of {ECE_TOLERANCE}. The reader's confidences are\n"
            f"  about as well calibrated on paper this repository did not design as on paper it\n"
            f"  did, which is the strongest thing that can be said for the generator without\n"
            f"  the generator saying it."
        )
    else:
        print(
            f"  **Outside the declared tolerance of {ECE_TOLERANCE}.** The reader's confidences\n"
            f"  do not transport: a score means one thing on the generated corpus and another on\n"
            f"  real capture. Every threshold here is a statement about confidences, so every\n"
            f"  one of them is a statement about the generated distribution and should be\n"
            f"  read that way. This is a finding, reported rather than tuned away — widening the\n"
            f"  tolerance until it passed would make this harness agree with whatever it measured."
        )

    print(
        "\n  Accuracy is deliberately not compared. Photographs of thermal-printed receipts\n"
        "  against rendered trade documents is a difficulty comparison, not a calibration one,\n"
        "  and the difficulty of somebody else's corpus is not a fact about this system."
    )

    # Never a failure. The difference is information about *our* corpus, and a harness that
    # turned somebody else's document set into a red build would be one that gets deleted the
    # first time that set changes.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
