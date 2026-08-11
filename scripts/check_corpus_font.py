#!/usr/bin/env python3
"""The corpus font loads, and can draw every script the corpus contains.

**Two ceremonies died discovering this the slow way.** One built the image, started eight jobs
and failed on `No module named 'reportlab'`. The next got further and failed on *"postscript
outlines are not supported"* — Noto Sans CJK is OpenType with CFF outlines and reportlab reads
TrueType `glyf` only. Each cost an hour to find out, an hour into a run.

So the font is probed at image build time instead, in seconds, and asked for a glyph in each of
the three scripts the corpus actually contains: Latin, Greek, and the Chinese characters in its
party names.

**Why a glyph check and not just "the font loaded".** A font missing a script does not fail at
render time. It draws an empty box. The page still renders, the corpus still generates, every
check downstream still passes — and the claims are scored against a page with nothing on it.
That is not hypothetical: `corpus/sheet.py` used to take the first font it found, and on a Linux
runner that was DejaVu, which covers no CJK. Thirteen Chinese party names became tofu and
nothing said a word.
"""

from __future__ import annotations

import sys

from corpus.sheet import _FONT_CANDIDATES, _FONT_SUBFONT_INDEX

#: One string per script the corpus draws. Taken from the corpus itself rather than invented:
#: the Greek is a bill of lading's title, the CJK is a party name that appears in it.
SAMPLES: dict[str, str] = {
    "latin": "Bill of Lading",
    "greek": "Φορτωτική",
    "cjk": "北方桥货运",
}


def main() -> int:
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415 - a build-time probe

    path = _FONT_CANDIDATES[0]
    try:
        font = TTFont("probe", path, subfontIndex=_FONT_SUBFONT_INDEX)
    except Exception as error:
        print(f"the corpus font at {path} will not load: {error}", file=sys.stderr)
        print(
            "reportlab reads TrueType `glyf` outlines only. An OpenType font with CFF outlines "
            "fails here — which is what Noto Sans CJK did, an hour into a ceremony.",
            file=sys.stderr,
        )
        return 1

    missing = {
        script: "".join(
            character
            for character in text
            if not character.isspace() and font.face.charToGlyph.get(ord(character), 0) == 0
        )
        for script, text in SAMPLES.items()
    }
    missing = {script: absent for script, absent in missing.items() if absent}
    if missing:
        print(f"{path} has no glyph for: {missing}", file=sys.stderr)
        print(
            "Box geometry comes from font metrics, and a missing glyph renders as an empty box "
            "rather than an error. The corpus generates, every check passes, and the claims are "
            "scored against a page with nothing on it.",
            file=sys.stderr,
        )
        return 1

    print(f"corpus font: {path} draws Latin, Greek and CJK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
