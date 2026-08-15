#!/usr/bin/env python3
"""Render the committed thresholds into the shape the extraction handler reads.

**The gap this closes.** `manifest.handlers.publish` reads
`s3://<records>/thresholds/<reader>@<version>.json` and nothing created it. The first invocation
after a deploy would have failed on `NoSuchKey` — the same family as the provenance gate that
was invoked by a name no layer created, and equally invisible: every offline check passed,
because every offline check reads `recordings/thresholds.json` directly.

**Why a rendering step rather than shipping the file as-is.** `recordings/thresholds.json` is the
derivation's full output — bounds, N, ECE, coverage, whether the limit is evidence or quality.
That belongs in the repository, where a reader can see how a threshold was arrived at. What the
handler needs is the decision: a number, or `null` meaning always-review. Shipping the whole
thing would put the *evidence* inside the runtime and invite something there to re-derive from
it, which is exactly what the ceremony in `make ocr-record` exists to prevent.

**The key carries the reader identity, and that is not decoration.** A threshold derived for one
reader says nothing about another: two readers' 0.8 are different events. The handler looks the
artefact up by the identity of the reading in front of it, so a deployment carrying thresholds
for a different reader fails by name instead of applying the wrong numbers silently.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from manifest.core.document import ReaderIdentity

ROOT = Path(__file__).resolve().parents[1]
#: Each reader this repository has recorded, and where its derivation lives. Tier 1 arrived on
#: 2026-08-15: the estate looks a threshold up by the reader that produced the value, so an
#: escalated reading found no artefact and `handlers/publish.py` refused it by name — correctly,
#: and permanently, until this renders one.
READERS = {
    "tier0": (ROOT / "recordings/ocr/manifest.json", ROOT / "recordings/thresholds.json"),
    "tier1": (
        ROOT / "recordings/textract/manifest.json",
        ROOT / "recordings/thresholds.textract.json",
    ),
}

RECORDING = READERS["tier0"][0]
DERIVED = READERS["tier0"][1]


def _floor() -> float:
    """The declared minimum a threshold must clear before it ships.

    In `contracts/cascade/routing.yaml` with its reasoning, and read rather than restated: it is
    a policy about what counts as evidence, and a copy of it here would be a second policy.
    """
    from manifest.contracts.loader import load  # noqa: PLC0415

    return float(load(ROOT / "contracts").cascade.minimum_useful_threshold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="where to write the artefact")
    parser.add_argument("--print-key", action="store_true", help="print the object key and exit")
    parser.add_argument(
        "--reader",
        choices=sorted(READERS),
        default="tier0",
        help="Which recorded reader's derivation to render. Each ships its own artefact.",
    )
    arguments = parser.parse_args()

    recording, derivation = READERS[arguments.reader]
    if not recording.exists():
        print(
            f"{recording} does not exist; nothing recorded for {arguments.reader}", file=sys.stderr
        )
        return 1

    manifest = json.loads(recording.read_text(encoding="utf-8"))
    identity = ReaderIdentity(name=manifest["reader_name"], version=manifest["reader_version"])
    reader = str(identity)
    # The **same** function the handler uses to look it up. Two places building this string is
    # two places it can drift, and the drift is a `NoSuchKey` at the first request after a
    # deploy — which reads as a missing artefact rather than as a naming disagreement.
    key = f"thresholds/{identity.slug}.json"

    if arguments.print_key:
        print(key)
        return 0

    if arguments.out is None:
        parser.error("--out is required unless --print-key is given")

    derived = json.loads(derivation.read_text(encoding="utf-8"))
    floor = _floor()
    floored: list[str] = []

    thresholds: dict[str, float | None] = {}
    for field, entry in sorted(derived.items()):
        # `always_review` and `threshold: null` mean the same thing and are checked together, so
        # that a derivation which set one without the other cannot ship a field the handler would
        # treat as publishable.
        if entry.get("always_review"):
            if entry.get("threshold") is not None:
                print(
                    f"{field}: declared always-review and carries a threshold of "
                    f"{entry['threshold']}. One of the two is wrong and this refuses to guess "
                    f"which — shipping either reading would publish or queue a field on a rule "
                    f"nobody wrote.",
                    file=sys.stderr,
                )
                return 1
            thresholds[field] = None
            continue

        threshold = entry.get("threshold")
        if threshold is None:
            print(
                f"{field}: no threshold and not declared always-review. A field in this state "
                f"is a derivation that did not finish, and the handler refuses a field it "
                f"cannot find rather than treating the absence as always-review.",
                file=sys.stderr,
            )
            return 1
        # **A threshold below the declared floor ships as always-review.** Zero is arithmetically
        # correct and operationally empty — the derivation never saw this reader be wrong, so the
        # confidence was never asked to separate anything — and shipping it would publish on a
        # score that has not been shown to mean anything. See the contract for the argument.
        if float(threshold) < floor:
            floored.append(f"{field} ({float(threshold):.3f})")
            thresholds[field] = None
            continue
        thresholds[field] = float(threshold)

    payload = {
        "reader": reader,
        "recording_digest": manifest["digest"],
        "corpus_fingerprint": manifest["corpus_fingerprint"],
        "thresholds": thresholds,
        "_note": (
            "Derived from the committed engine recording under the ceremony in `make "
            "ocr-record`, never recomputed at runtime. Every figure is a statement about a "
            "distribution this repository generated. See recordings/thresholds.json for how "
            "each one was arrived at, including its N and whether the limit was evidence or "
            "quality."
        ),
    }

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    review = sum(1 for value in thresholds.values() if value is None)
    print(f"thresholds artefact: {len(thresholds)} field(s) for {reader}")
    print(f"  {review} always-review, {len(thresholds) - review} with a derived threshold")
    if floored:
        print(
            f"  {len(floored)} below the declared floor of {floor} and shipped as "
            f"always-review: {', '.join(floored)}"
        )
    print(f"  key: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
