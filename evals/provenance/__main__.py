"""CLAIM 2 — every published field traces to a page and a box, checked against the page.

Two halves, and the second is the one that matters.

**Does the gate pass honest records?** A gate that refuses everything is trivially sound and
useless: it would be switched off in a week. So the harness verifies a sample of fields the
extractor read correctly and reports the rate.

**Does it refuse a corrupted one?** Four fixtures, each a different way a recorded box is wrong,
because "the box is wrong" is not one failure:

1. **on whitespace** — the box moved into the margin. Layer A refuses: there is no ink there.
2. **shifted half a line** — ink present, wrong ink. Layer A passes; **Layer B** refuses. This
   is the case that decides whether Layer B was worth building.
3. **right box, wrong page** — the coordinates are perfect and the page index is off by one.
   Refused only if the verifier resolves the page from the *record*.
4. **an identical string elsewhere on the page** — **expected not caught**, stated in ADR-0003
   in advance. Every layer passes, because the value genuinely is at those coordinates. The
   fixture exists so the limitation is measured rather than hoped about, and so that a future
   change which makes it catchable is noticed.

The claim, restated because it is the thing most easily oversold: this says a published field is
**where the record says it is**. It does not say the value is right.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from evals.harness import contracts, ground_truth, recorded_pages, score_all
from manifest.core.calibration import Outcome
from manifest.core.geometry import Box, GeometryError
from manifest.core.text import normalise
from manifest.extraction.local.raster import for_document
from manifest.gates.provenance import Layer, Provenance, Verdict, verify

RENDERED = Path(__file__).resolve().parents[2] / "corpus" / "rendered"

#: How many honest records to verify, and how many of each corruption to plant. Every one costs
#: a re-read through the reader binary, so the sample is bounded and the bound is printed —
#: a harness that quietly scored fifty records and reported a percentage would be a harness
#: whose number nobody could weigh.
SAMPLE = 120

#: The share of correct records the gate must verify. Below this it is not a control, it is a
#: tax: every honest record it refuses is a queue item ADR-0001 has already declared scarce, and
#: a gate that spends more capacity than the errors it catches gets muted on a busy afternoon.
#: Declared here rather than fitted to the current number, and the current number is printed
#: beside it so the margin is visible.
HONEST_FLOOR = 0.70


@dataclass(frozen=True, slots=True)
class Case:
    label: str
    provenance: Provenance
    raster: object
    expect_refusal: bool
    expect_layer: Layer | None


def _provenance(entry, box: Box, page: int) -> Provenance:
    field = contracts().document(entry.document).field(entry.field)
    return Provenance(
        field=entry.field,
        value=entry.extracted.value or "",
        page=page,
        box=box,
        language=entry.language,
        comparison=tuple(field.comparison),
        self_checking=field.type.value == "container_number",
    )


def _pages(entry, truth_by_key) -> int:
    return int(truth_by_key[(entry.shipment, entry.document)]["pages"])


def _duplicate_elsewhere(entry) -> Box | None:
    """A box on a *different* occurrence of the same string on the same page.

    Only where the corpus provides one. Constructing it by moving a box onto arbitrary other
    text would be building fixture 2 again with a different name.
    """
    pages = recorded_pages().get((entry.shipment, entry.document))
    if not pages or entry.extracted.box is None:
        return None
    rules = tuple(contracts().document(entry.document).field(entry.field).comparison)
    wanted = normalise(entry.extracted.value or "", rules)
    if not wanted:
        return None
    for page in pages:
        if page.number != entry.extracted.page:
            continue
        for word in page.words:
            # A *different* occurrence: same text after the field's own comparison rules, and
            # not overlapping the box the record already points at.
            if normalise(word.text, rules) == wanted and word.box.iou(entry.extracted.box) < 0.1:
                return word.box
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=SAMPLE)
    arguments = parser.parse_args()

    if not RENDERED.exists():
        print(f"{RENDERED} does not exist; run `make corpus`", file=sys.stderr)
        return 1

    scored = score_all()
    truth_by_key = {
        (document["shipment_id"], document["document_id"]): document
        for document in ground_truth()["documents"]
    }

    # Only fields the extractor read correctly. A record whose value is already wrong is claim
    # 1's business; asking claim 2 about it conflates a misread with a mislocated box.
    honest = [
        entry
        for entry in scored
        if entry.outcome is Outcome.CORRECT and entry.extracted.box and entry.extracted.page
    ]
    generator = random.Random(20260809)
    sample = generator.sample(honest, min(arguments.sample, len(honest)))

    results = {
        "honest": {"verified": 0, "refused": 0, "reasons": []},
        "whitespace": {"caught": 0, "missed": 0, "layers": []},
        "shifted": {"caught": 0, "missed": 0, "layers": []},
        "wrong_page": {"caught": 0, "missed": 0, "layers": []},
        "duplicate": {"caught": 0, "missed": 0, "available": 0},
    }

    for entry in sample:
        pages = _pages(entry, truth_by_key)
        raster = for_document(RENDERED, entry.shipment, entry.document, pages)
        box = entry.extracted.box
        page = entry.extracted.page
        assert box is not None and page is not None

        honest_check = verify(_provenance(entry, box, page), raster)
        if honest_check.verdict is Verdict.VERIFIED:
            results["honest"]["verified"] += 1
        else:
            results["honest"]["refused"] += 1
            if len(results["honest"]["reasons"]) < 6:
                results["honest"]["reasons"].append(
                    f"{entry.document}.{entry.field}: {honest_check.reason[:150]}"
                )

        # 1 — the margin.
        try:
            # Inside the printed margin. The page is A4 with a 48-point margin, which is 8%
            # of the width, so anything left of that is paper. An earlier version used 0.012
            # with the field's own width and reached into the text column — the harness
            # reported it refused by the wrong *layer*, which is how a fixture that is not
            # testing what it says gets caught.
            margin = Box(left=0.004, top=box.top, width=0.020, height=box.height)
        except GeometryError:
            margin = None
        if margin is not None:
            _record(results["whitespace"], verify(_provenance(entry, margin, page), raster))

        # 2 — half a line down.
        try:
            shifted = Box(
                left=box.left,
                top=min(box.top + box.height * 0.9, 0.97),
                width=box.width,
                height=box.height,
            )
        except GeometryError:
            shifted = None
        if shifted is not None:
            _record(results["shifted"], verify(_provenance(entry, shifted, page), raster))

        # 3 — the right box on the wrong page.
        if pages > 1:
            other = page + 1 if page < pages else page - 1
            _record(results["wrong_page"], verify(_provenance(entry, box, other), raster))

        # 4 — expected not caught.
        duplicate = _duplicate_elsewhere(entry)
        if duplicate is not None:
            results["duplicate"]["available"] += 1
            _record(results["duplicate"], verify(_provenance(entry, duplicate, page), raster))

    return _report(results, len(sample))


def _record(bucket: dict, check) -> None:
    if check.refuses:
        bucket["caught"] += 1
        if "layers" in bucket and check.layer:
            bucket["layers"].append(check.layer.value)
    else:
        bucket["missed"] += 1


def _report(results: dict, sample: int) -> int:
    honest = results["honest"]
    total = honest["verified"] + honest["refused"]
    print(f"claim 2 — provenance checked against the page  (sample of {sample} records)\n")
    rate = honest["verified"] / total if total else 0.0
    print(f"  honest records verified   {honest['verified']}/{total}  ({rate:.1%})")
    for reason in honest["reasons"]:
        print(f"      refused: {reason}")

    failures: list[str] = []
    if rate < HONEST_FLOOR:
        failures.append(
            f"only {rate:.1%} of correct records verified, against a declared floor of "
            f"{HONEST_FLOOR:.0%}. Below that the gate is not a control, it is a tax: every "
            f"honest record it refuses is a review item, and ADR-0001 declared that capacity "
            f"finite"
        )
    print(
        f"      The {total - honest['verified']} refusals above are real disagreements between "
        f"two recognition paths — a shared classifier reading COO as C00, a word split in two. "
        f"Each is a queue item, and at {1 - rate:.1%} of published fields that is the standing "
        f"cost of Layer B. It is a cost, it is measured, and it is why the layer is declared "
        f"separately rather than folded into one verdict."
    )
    for name, description, layer in (
        ("whitespace", "box moved into the margin", "ink"),
        ("shifted", "box shifted half a line down", "reread"),
        ("wrong_page", "right box, wrong page", None),
    ):
        bucket = results[name]
        attempted = bucket["caught"] + bucket["missed"]
        if attempted == 0:
            print(f"  {description:32} — not exercised on this corpus")
            continue
        share = bucket["caught"] / attempted
        counted = {name: bucket["layers"].count(name) for name in set(bucket["layers"])}
        print(f"  {description:32} {bucket['caught']}/{attempted} refused  {counted or ''}")
        if share < 0.9:
            failures.append(
                f"{description}: only {bucket['caught']}/{attempted} refused. A corrupted box "
                f"that verifies is claim 2 reporting green over a record nothing checked"
            )
        if layer and counted and max(counted, key=counted.get) != layer:
            failures.append(
                f"{description}: refused mostly by {max(counted, key=counted.get)!r} rather "
                f"than {layer!r}. The right answer for the wrong reason is not evidence — "
                f"gate-proof's second rule, applied to the gate's own layers"
            )

    duplicate = results["duplicate"]
    attempted = duplicate["caught"] + duplicate["missed"]
    print(
        f"\n  identical string elsewhere       {duplicate['caught']}/{attempted} refused "
        f"— **expected not caught** (ADR-0003)"
    )
    print(
        "      Every layer passes, because the value genuinely is at those coordinates. This "
        "is a field-assignment defect, not a provenance one, and it is measured here rather "
        "than hoped about."
    )

    print(
        "\n  What this claim says: a published field is WHERE the record says it is. It does "
        "not say the value is right — that is claim 1's threshold and the human loop."
    )
    if failures:
        print("\nclaim 2: FAILED\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nclaim 2: the gate passes honest records and refuses corrupted ones, by layer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
