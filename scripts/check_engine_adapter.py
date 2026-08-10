#!/usr/bin/env python3
"""The tier-0 adapter still understands what the binary emits.

**This script derives nothing and asserts no confidence value.** That separation is the whole
reason it exists, and `docs/DECISIONS.md` 19 is where it was decided.

Thresholds come from `recordings/ocr/`, produced once, here, by a known build of the reader,
under a ceremony that prints every movement and requires it to be accepted. If CI re-read the
corpus and re-derived them, a threshold would move because a runner image was updated — claim 1
would spend its life going red for reasons that have nothing to do with this repository, and the
check would be muted within a month.

What CI *can* check, and what nothing else would catch: that the adapter still parses the
binary's output format. A version bump that renamed a TSV column or changed its ordering would
break every reading silently — the recording would keep deriving perfectly good thresholds from
data nothing could produce any more, and the first sign would be an empty record in production.

So: read one small page, on whatever build this machine has, and require the normalised shape to
come out. Text, geometry and confidence must all be present and structurally sound. Their
*values* are nobody's business here.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manifest.core.geometry import PageSize  # noqa: E402
from manifest.extraction.local import reader as local  # noqa: E402

sys.path.insert(0, str(ROOT))
from corpus.sheet import _FONT_CANDIDATES as FONT_CANDIDATES  # noqa: E402

#: What the page says. Chosen to exercise the two alphabets no managed service reads, because a
#: language pack silently missing is the failure that produces confident text in the wrong
#: script rather than an error.
LINES: tuple[tuple[str, str], ...] = (
    ("eng", "GROSS WEIGHT 27000 KGS"),
    ("ell", "ΠΕΙΡΑΙΑΣ"),
)


def _render(text: str, path: Path) -> PageSize:
    from PIL import (  # noqa: PLC0415 - the check runs without Pillow on a machine that has no reader either
        Image,
        ImageDraw,
        ImageFont,
    )

    image = Image.new("L", (1200, 200), color=255)
    draw = ImageDraw.Draw(image)
    # A font that carries Greek. **The same candidate list the corpus generator uses**, imported
    # rather than restated: two lists of font paths drift, and the day they do, this check draws
    # its page with a different face from the one the recording was made with — and reports a
    # reader failure that is a font difference.
    #
    # If none is present the run fails here. The default bitmap font cannot draw Greek at all,
    # so falling back to it would turn a missing-font problem into an apparent reader failure,
    # which is the most expensive kind of wrong answer this check could give.
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 48)
            break
    else:
        raise SystemExit(
            "no font with Greek coverage on this machine, so this check cannot render its own "
            "input. Refused rather than falling back to a Latin-only bitmap font, which would "
            "turn a missing-font problem into an apparent reader failure"
        )
    draw.text((40, 60), text, fill=0, font=font)
    image.save(path, format="PNG")
    return PageSize(width=image.width, height=image.height)


def main() -> int:
    if not local.available():
        print("engine-adapter: the reader binary is not on PATH", file=sys.stderr)
        return 1

    version = local.version()
    print(f"engine-adapter: reader {version.identity()} on this machine")
    print(
        "  no threshold is derived here. Thresholds come from recordings/ocr/, "
        "under `make ocr-record`'s ceremony — see docs/DECISIONS.md 19."
    )

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as workspace:
        for language, text in LINES:
            path = Path(workspace) / f"{language}.png"
            size = _render(text, path)
            page = local.read_page(path, 1, size, language)

            words = list(page.words)
            if not words:
                problems.append(
                    f"[{language}] the reader returned no words for a page this script drew "
                    f"itself. Either the language data is missing — which does not error, it "
                    f"reads the page in the wrong script — or the adapter no longer finds the "
                    f"columns it parses"
                )
                continue

            # Structure, never values. Whether it read `27000` or `2700O` is this build's
            # business and not this check's; whether every word arrived with a confidence in
            # range and a box on the page is the contract the whole system rests on.
            for word in words:
                if word.confidence is None:
                    problems.append(
                        f"[{language}] {word.text!r} arrived with no confidence. This reader "
                        f"reports one per word; `None` here means the adapter stopped finding "
                        f"the column, and every threshold derived from it would be derived "
                        f"from an absence"
                    )
                    break
                if not 0.0 <= word.confidence <= 1.0:
                    problems.append(
                        f"[{language}] {word.text!r} has confidence {word.confidence}, outside "
                        f"[0, 1] — the adapter has stopped dividing by the reader's scale"
                    )
                    break
                box = word.box
                if not (0.0 <= box.left <= 1.0 and 0.0 <= box.top <= 1.0):
                    problems.append(
                        f"[{language}] {word.text!r} has a box outside the page: {box}. The "
                        f"adapter has stopped dividing pixels by the raster's dimensions"
                    )
                    break
            else:
                print(f"  [{language}] {len(words)} word(s), all with a box and a confidence")

    if problems:
        print("\nengine-adapter: the adapter no longer matches what the binary emits\n")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print("engine-adapter: the normalised shape still comes out of this build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
