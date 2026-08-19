#!/usr/bin/env python3
"""Draw a published field's recorded box on the page it was read from.

**The image this repository's own title promised and never produced.** "Every field traces to a
pixel" is the subtitle, claim 2 is the harness, `evals/provenance` scores it — and there was no
picture of it anywhere. The provenance gate crops in memory; `scripts/e2e_verify.py` writes a
crop but only against a deployed estate. So the one argument a reader grasps without running
anything had no artefact at all.

This makes one, offline, from the committed recording and the rendered corpus:

  - the whole page, with the recorded box outlined where the record says the value is;
  - the crop the provenance gate actually re-reads, enlarged beside it;
  - the field name, the published value, the confidence and the threshold it cleared.

**It draws the reader's box, not the generator's.** `Scored.truth_box` is where the generator
*drew* the value and is what claim 2's fixtures corrupt; `extracted.box` is what the record
claims. Drawing the truth box would produce a prettier picture of a weaker statement — the
generator agreeing with itself. The interesting picture is the one the system would have to
defend.

    python3 scripts/provenance_still.py                    # pick a good example automatically
    python3 scripts/provenance_still.py --field vessel_name
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: The box is drawn in the same carmine the banner uses for its rule — a rubber-stamp red that
#: reads on a grey scan without looking like a UI element.
STAMP = (198, 74, 94)
INK = (20, 24, 23)
PAPER = (250, 250, 249)
MUTED = (110, 122, 118)


def _rendered(shipment: str, document: str, page: int) -> pathlib.Path:
    return ROOT / "corpus/rendered" / f"{shipment}_{document}_p{page}.jpg"


def _pick(scored, wanted: str | None):
    """A published field with a box, a page that exists, and a value worth reading.

    Sorted by confidence so the example is a field the system was sure about — the honest
    direction, because a marginal box makes the picture look better than the claim is.
    """
    candidates = [
        s
        for s in scored
        if s.extracted.box is not None
        and s.extracted.page is not None
        and s.extracted.value
        and (wanted is None or s.field == wanted)
        and _rendered(s.shipment, s.document, s.extracted.page).exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.extracted.confidence or 0.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default=None, help="Draw this field instead of the best one.")
    parser.add_argument("--out", default="images/provenance_box.png")
    arguments = parser.parse_args(argv)

    from evals.harness import score_all  # noqa: PLC0415
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    picked = _pick(score_all(), arguments.field)
    if picked is None:
        print(f"{RED}no published field with a box and a rendered page{RESET}", file=sys.stderr)
        return 1

    page_path = _rendered(picked.shipment, picked.document, picked.extracted.page)
    page = Image.open(page_path).convert("RGB")
    width, height = page.size
    box = picked.extracted.box
    left, top = box.left * width, box.top * height
    right, bottom = left + box.width * width, top + box.height * height

    # The crop the gate re-reads, with the same margin `gates/provenance.py` adds before cropping.
    margin = 0.004
    crop = page.crop(
        (
            max(0, int((box.left - margin) * width)),
            max(0, int((box.top - margin) * height)),
            min(width, int((box.left + box.width + margin) * width)),
            min(height, int((box.top + box.height + margin) * height)),
        )
    )

    draw = ImageDraw.Draw(page)
    for offset in range(4):
        draw.rectangle(
            [left - offset, top - offset, right + offset, bottom + offset], outline=STAMP
        )

    def font(path: str, size: int):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    MONO = font("/System/Library/Fonts/Menlo.ttc", 30)
    MONO_BIG = font("/System/Library/Fonts/Menlo.ttc", 46)

    # A caption band under the page, then the crop enlarged to the band's height. The crop is
    # scaled with NEAREST so a reader sees the actual pixels the gate scored rather than a
    # smoothed impression of them.
    band = 260
    scale = min(6, max(2, band // max(1, crop.height)))
    big = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST)

    # **A window around the field, not the whole sheet.** Cropping to "where the ink is" does not
    # work on these pages: they are deliberately degraded, the speckle reaches the bottom margin,
    # and a continuation marker sits near the foot — so every row qualifies and the window is the
    # whole page, two thirds of which is blank paper with a red box floating in it.
    #
    # So the window is declared rather than detected: the top of the sheet down to whichever is
    # lower, the boxed field plus a tenth of the page, or the top 55%. The header, the declared
    # fields and the line items all sit in that band on every document type here. The full page is
    # in `corpus/rendered/` for anyone who wants to check what was cropped away.
    keep = min(height, max(int(bottom + 0.10 * height), int(0.55 * height)))
    page = page.crop((0, 0, width, keep))
    height = keep

    canvas = Image.new("RGB", (width, height + band), PAPER)
    canvas.paste(page, (0, 0))
    plate = ImageDraw.Draw(canvas)
    plate.line([(0, height), (width, height)], fill=STAMP, width=6)

    threshold = picked.extracted.confidence
    plate.text((48, height + 34), f"{picked.field}", font=MONO_BIG, fill=INK)
    plate.text(
        (48, height + 104),
        f'value      "{picked.extracted.value}"',
        font=MONO,
        fill=INK,
    )
    plate.text(
        (48, height + 146),
        f"confidence {threshold:.3f}" if threshold is not None else "confidence  none reported",
        font=MONO,
        fill=INK,
    )
    plate.text(
        (48, height + 188),
        f"page {picked.extracted.page}  ·  box "
        f"[{box.left:.4f} {box.top:.4f} {box.width:.4f} {box.height:.4f}]",
        font=MONO,
        fill=MUTED,
    )
    canvas.paste(big, (width - big.width - 48, height + 40))

    out = ROOT / arguments.out
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)

    print(f"  {GREEN}ok{RESET}    {arguments.out}")
    print(
        f"        {picked.shipment} {picked.document} p{picked.extracted.page} · "
        f"{picked.field} = {picked.extracted.value!r} at {threshold}"
    )
    print(f"  {DIM}the box drawn is the reader's, not the generator's{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
