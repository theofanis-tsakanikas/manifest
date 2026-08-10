#!/usr/bin/env python3
"""Read real photographed paper with the tier-0 engine, and record how its confidence behaved.

**The circle this breaks.** Every figure on this repository's scoreboard is scored against a
corpus this repository generated. The obvious challenge — *did you tune the generator until the
claims passed?* — has had exactly one answer, `corpus/envelope.yaml`, which is also ours. Two
declarations by the same author are not independent evidence.

So: run the same reader, at the same version, over photographs of physical receipts taken by
somebody else's pipeline (`corpus/external/LICENCE.md` records which set, its licence, and the
date the terms were read), and compare the reader's confidence against whether the word was
actually right. That produces a reliability curve on paper nobody here designed, next to the one
on paper we did.

**What is measured, and what is deliberately not.**

Calibration transports; thresholds do not. These are receipts and not bills of lading, so no
field in `contracts/documents/` appears here and **no threshold in this repository is derived
from them**. What is comparable is the *relationship* between a confidence and correctness — and
if that relationship holds on real capture, the generated corpus has earned its credibility on
the axis that matters.

**Matching, and why it is conservative.** For each ground-truth word box, the reader's words
whose boxes overlap it are collected and their text joined. A match is a comparison under the
system's own normalisation rules, not a fuzzy score: `core.text` with case and separators, the
same rules the provenance gate uses to decide whether a re-read agrees. Where the reader found
nothing inside a ground-truth box, that is an **abstention** and is excluded from the reliability
curve for the same reason `derive()` excludes them — the budget is about published-and-wrong, and
a value never produced was never published.

Like `make ocr-record`, this writes a recording rather than a verdict, and the recording is what
`evals/calibration/` reads. Nothing here derives a threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manifest.core.geometry import Box, PageSize  # noqa: E402
from manifest.core.text import Rule, normalise  # noqa: E402
from manifest.extraction.local import reader as local  # noqa: E402

OUT = ROOT / "recordings" / "external"

#: Where the set is fetched from. Named here rather than passed in, so that the recording and its
#: licence file cannot come to describe different data.
SOURCE = "https://huggingface.co/api/datasets/naver-clova-ix/cord-v2/parquet/default/test/0.parquet"

#: The comparison rules. The same ones the provenance gate applies when it decides whether a
#: re-read agrees with a published value — because "did the reader get this word right" has to
#: mean the same thing here as it does there, or the two figures are not about one system.
COMPARISON: tuple[Rule, ...] = (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE, Rule.SEPARATORS)

#: A ground-truth box and a reader box are the same word when they overlap at all. Deliberately
#: permissive: this is not measuring localisation, it is measuring whether the *text* the reader
#: was confident about was right, and a strict IoU here would score box-fitting as reading error.
_TOUCHES = 0.0


def _quad_to_box(quad: dict, size: PageSize) -> Box | None:
    xs = [float(quad[f"x{index}"]) for index in (1, 2, 3, 4)]
    ys = [float(quad[f"y{index}"]) for index in (1, 2, 3, 4)]
    left, right = min(xs) / size.width, max(xs) / size.width
    top, bottom = min(ys) / size.height, max(ys) / size.height
    if right <= left or bottom <= top:
        return None
    try:
        return Box(
            left=max(left, 0.0),
            top=max(top, 0.0),
            width=min(right, 1.0) - max(left, 0.0),
            height=min(bottom, 1.0) - max(top, 0.0),
        )
    except Exception:
        return None


def _observe(
    directory: Path, shard: Path, limit: int, pq, Image
) -> tuple[list[dict], int, int, int]:
    """Read each page and compare the reader against the set's own word boxes.

    Split out of `main` so that the fetching, the reading and the reporting are three things a
    reader can hold separately — and so the loop that does the actual measurement is not buried
    forty lines inside argument parsing.
    """
    observations: list[dict] = []
    pages = abstained = skipped = 0

    for batch in pq.ParquetFile(shard).iter_batches(batch_size=8):
        for row in batch.to_pylist():
            if pages >= limit:
                break
            image_path = directory / f"page-{pages:04d}.png"
            image_path.write_bytes(row["image"]["bytes"])
            with Image.open(image_path) as opened:
                size = PageSize(width=opened.width, height=opened.height)

            read = list(local.read_page(image_path, 1, size, "eng").words)
            truth = json.loads(row["ground_truth"])

            for line in truth.get("valid_line", []):
                for word in line.get("words", []):
                    box = _quad_to_box(word.get("quad", {}), size)
                    if box is None or not str(word.get("text", "")).strip():
                        skipped += 1
                        continue
                    covering = [other for other in read if box.iou(other.box) > _TOUCHES]
                    if not covering:
                        # The reader produced nothing here. An abstention, excluded from the
                        # curve exactly as `derive()` excludes one: a value never produced was
                        # never published, and counting it as wrong would make a reader that
                        # abstains look worse than one that guesses.
                        abstained += 1
                        continue
                    confidences = [
                        part.confidence for part in covering if part.confidence is not None
                    ]
                    if not confidences:
                        skipped += 1
                        continue
                    found = " ".join(part.text for part in covering)
                    observations.append(
                        {
                            "confidence": min(confidences),
                            "correct": normalise(found, COMPARISON)
                            == normalise(str(word["text"]), COMPARISON),
                        }
                    )
            pages += 1
            image_path.unlink(missing_ok=True)
        if pages >= limit:
            break

    return observations, pages, abstained, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="how many pages to read")
    arguments = parser.parse_args()

    if not local.available():
        print("external-record: the tier-0 reader is not on PATH", file=sys.stderr)
        return 1

    try:
        import pyarrow.parquet as pq  # noqa: PLC0415 - only this path needs it
        from PIL import Image  # noqa: PLC0415
    except ImportError as exc:
        print(
            f"external-record: {exc}. This path needs `pyarrow` and `pillow`; they are not "
            f"hard dependencies of the package because nothing else in this repository reads "
            f"parquet.",
            file=sys.stderr,
        )
        return 1

    identity = local.version().identity()
    print(f"external-record: reader {identity}")
    print(f"  source: {SOURCE}")
    print("  licence and terms: corpus/external/LICENCE.md\n")

    with tempfile.TemporaryDirectory() as workspace:
        directory = Path(workspace)
        shard = directory / "shard.parquet"
        if not shard.exists():
            print("  fetching...")
            with urllib.request.urlopen(SOURCE, timeout=300) as response:
                shard.write_bytes(response.read())

        observations, pages, abstained, skipped = _observe(
            directory, shard, arguments.limit, pq, Image
        )

    if not observations:
        print("external-record: no observations produced", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "observations.jsonl").write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in observations) + "\n",
        encoding="utf-8",
    )
    correct = sum(1 for entry in observations if entry["correct"])
    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "_note": (
                    "Derived from an externally licensed document set — see "
                    "corpus/external/LICENCE.md for the set, its licence, and the date its "
                    "terms were read. No image from that set is redistributed here. These are "
                    "confidences and correctness flags with no text in them."
                ),
                "reader_name": identity.name,
                "reader_version": identity.version,
                "source": SOURCE,
                "pages": pages,
                "observations": len(observations),
                "correct": correct,
                "abstained": abstained,
                "skipped": skipped,
                "comparison_rules": [rule.value for rule in COMPARISON],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"  pages read          {pages}")
    print(f"  observations        {len(observations):,}")
    print(f"  correct             {correct:,} ({correct / len(observations):.1%})")
    print(f"  reader abstained    {abstained:,} (excluded from the curve)")
    print(f"  skipped             {skipped:,} (malformed quad or unscored)")
    print(f"\n  written to {OUT.relative_to(ROOT)}/")
    print(
        "\n  No threshold is derived from this. These are receipts, not trade documents: the\n"
        "  fields do not map to any contract here, and what transports is the *calibration*,\n"
        "  not the thresholds. `evals/calibration/` reads it as the out-of-distribution column."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
