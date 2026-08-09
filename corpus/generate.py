"""Building the corpus: world, plant, render, rasterise, degrade, record.

One seed produces one corpus. `--check` regenerates the ground truth and compares it against
what is committed, without writing anything — which is what CI runs, and what turns "the corpus
is deterministic" from an intention into a build step.

**What is committed and what is not.** The ground truth is committed: it is the labels every
claim is scored against, it is small, and it is the thing a reader should be able to inspect
without running anything. The rendered pages are **not**: they are large, binary, and
reproducible from the seed by `make corpus`.

**Why the ground truth is committed rather than regenerated in CI.** It depends on font
metrics, and a runner with a different font produces different boxes. That difference must be a
loud failure rather than a silent one, so the manifest records the font that was used and
`--check` refuses when it does not match — the same discipline as the engine recording in
ADR-0005, and for the same reason: a number derived from a different input is not a number
about this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2

from corpus.degrade import degrade_page
from corpus.documents import BUILDERS, Rendered
from corpus.plant import Planted, plant
from corpus.sheet import register_fonts
from corpus.world import PlantedMismatch, build_world

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "corpus" / "ground_truth"
RENDERED = ROOT / "corpus" / "rendered"

#: 300 DPI. ADR-0005: the tier-0 reader's documentation recommends at least 300, both managed
#: readers cap an image at 10,000 pixels a side (A4 at 300 is 2,480 × 3,508, comfortably
#: inside), and both document a 15-pixel minimum character height, which at 300 DPI is about a
#: 4 pt character — so ordinary body text is clear of the floor and the deliberately tiny text
#: legitimately is not.
DPI = 300

DEFAULT_SEED = 20260809
DEFAULT_SHIPMENTS = 120


@dataclass(frozen=True, slots=True)
class DocumentTruth:
    """Everything known about one rendered document, exactly."""

    document_id: str
    shipment_id: str
    language: str
    pathologies: tuple[str, ...]
    pages: int
    fields: tuple[dict[str, object], ...]


def _truth(rendered: Rendered, placements, pages: int) -> DocumentTruth:
    return DocumentTruth(
        document_id=rendered.document_id,
        shipment_id=rendered.shipment_id,
        language=rendered.language.value,
        pathologies=tuple(sorted(p.value for p in rendered.pathologies)),
        pages=pages,
        fields=tuple(
            {
                "field": placement.field,
                "value": placement.value,
                "page": placement.page,
                "box": [
                    round(placement.box.left, 6),
                    round(placement.box.top, 6),
                    round(placement.box.width, 6),
                    round(placement.box.height, 6),
                ],
            }
            for placement in placements
        ),
    )


def build(seed: int, shipments: int, write_images: bool) -> dict[str, object]:
    """Generate the corpus, returning the ground-truth payload."""
    font = register_fonts()
    world, parties = build_world(seed, shipments)
    generator = random.Random(seed ^ 0x5EED)

    documents: list[DocumentTruth] = []
    mismatches: list[PlantedMismatch] = []

    if write_images:
        RENDERED.mkdir(parents=True, exist_ok=True)

    for shipment in world:
        planted = plant(shipment, generator)
        mismatches.extend(planted.mismatches)
        for builder in BUILDERS.values():
            rendered = builder(shipment, planted, generator)
            documents.append(_render_one(rendered, planted, generator, write_images))

    return {
        "seed": seed,
        "shipments": shipments,
        "dpi": DPI,
        "font": font,
        "parties": [asdict(party) for party in parties],
        "planted_mismatches": [asdict(mismatch) for mismatch in mismatches],
        "documents": [asdict(document) for document in documents],
    }


def _render_one(
    rendered: Rendered, planted: Planted, generator: random.Random, write_images: bool
) -> DocumentTruth:
    pdf = pypdfium2.PdfDocument(rendered.pdf)
    pages = len(pdf)
    moved: list = []

    for index in range(pages):
        number = index + 1
        image = pdf[index].render(scale=DPI / 72).to_pil()
        on_this_page = tuple(p for p in rendered.placements if p.page == number)
        degraded = degrade_page(image, on_this_page, rendered.pathologies, generator)
        moved.extend(degraded.placements)
        if write_images:
            # JPEG, not PNG. The page has already been JPEG-recompressed by the
            # degradation — it is a scan of a scan — so storing it losslessly would be storing
            # the artefacts at four times the size. 720 pages of lossless noise is two and a
            # half gigabytes of a directory that is git-ignored and reproducible from a seed.
            name = f"{rendered.shipment_id}_{rendered.document_id}_p{number}.jpg"
            degraded.image.save(RENDERED / name, format="JPEG", quality=92, optimize=True)
    pdf.close()
    return _truth(rendered, moved, pages)


def fingerprint(payload: dict[str, object]) -> str:
    """A digest over the ground truth, excluding the font path.

    The font *path* differs between a laptop and a runner even when the font is the same file,
    so hashing it would make the fingerprint machine-dependent for no benefit. The font is
    recorded and compared separately, where a mismatch can say what it actually is.
    """
    without_font = {key: value for key, value in payload.items() if key != "font"}
    return hashlib.sha256(
        json.dumps(without_font, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def write(payload: dict[str, object]) -> None:
    GROUND_TRUTH.mkdir(parents=True, exist_ok=True)
    (GROUND_TRUTH / "corpus.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (GROUND_TRUTH / "fingerprint.txt").write_text(fingerprint(payload) + "\n", encoding="utf-8")


def load_committed() -> dict[str, object]:
    path = GROUND_TRUTH / "corpus.json"
    if not path.exists():
        raise SystemExit(f"{path} does not exist; run `make corpus` first")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--shipments", type=int, default=DEFAULT_SHIPMENTS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate the ground truth and compare it against what is committed.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Ground truth only. Much faster, and what --check uses.",
    )
    arguments = parser.parse_args()

    if arguments.check:
        committed = load_committed()
        regenerated = build(
            seed=int(committed["seed"]),
            shipments=int(committed["shipments"]),
            write_images=False,
        )
        if regenerated["font"] != committed["font"]:
            print(
                f"corpus-check: the font has changed.\n"
                f"  committed: {committed['font']}\n"
                f"  here:      {regenerated['font']}\n"
                f"Box geometry comes from font metrics, so a different font is a different "
                f"ground truth. This is a loud failure on purpose: the alternative is every "
                f"claim being scored against boxes that quietly moved.",
                file=sys.stderr,
            )
            return 1
        if fingerprint(regenerated) != fingerprint(committed):
            print(
                "corpus-check: the generated corpus does not reproduce the committed ground "
                "truth. Either the generator changed and the corpus must be rebuilt and "
                "reviewed, or something non-deterministic has crept in — and if it is the "
                "second, every claim scored against this corpus was scored against a different "
                "corpus than the one that was reviewed.",
                file=sys.stderr,
            )
            return 1
        print(
            f"corpus-check: {len(committed['documents'])} documents reproduce exactly "
            f"(seed {committed['seed']}, {committed['shipments']} shipments)"
        )
        return 0

    payload = build(
        seed=arguments.seed, shipments=arguments.shipments, write_images=not arguments.no_images
    )
    write(payload)
    print(
        f"corpus: {len(payload['documents'])} documents from {arguments.shipments} shipments, "
        f"{len(payload['planted_mismatches'])} planted mismatches, seed {arguments.seed}"
    )
    print(f"  fingerprint {fingerprint(payload)[:16]}")
    if not arguments.no_images:
        print(f"  images in {RENDERED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
