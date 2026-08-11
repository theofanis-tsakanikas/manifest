"""Drawing a page and recording exactly where every field landed.

This is where ground truth comes from, and the reason it is exact rather than approximate: the
generator *puts* the value at a known point in a known font at a known size, so the box is
computed from the same numbers that drew it. Nothing infers a box from a rendered image, which
is what makes claim 2's fixtures — "the recorded box is deliberately wrong" — meaningful. A
ground truth derived by reading the page back would be the reader's opinion, and claim 2 would
be checking the reader against itself.

**Coordinates.** ReportLab's origin is bottom-left with y increasing upwards; the normalised
representation's is top-left with y increasing downwards. The conversion happens once, here,
in `_box`. A second place doing it is a sign flip waiting to happen, and a sign flip in a
provenance box is invisible on a symmetric page.

**The box is the glyph run's, not the line's.** Height comes from the font's ascent and descent
at the size drawn, so a crop taken at this box contains the ascenders and descenders a reader
needs. ADR-0003's Layer B re-reads that crop; a box that clipped them would make the verifier
disagree with a correct record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Final

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from manifest.core.geometry import Box

#: A4 in points. Every document in this corpus is A4, because the pathologies are about what is
#: printed on the page rather than about the paper.
PAGE_WIDTH, PAGE_HEIGHT = A4

#: One font, covering Latin, Greek and CJK. A corpus that fell back to a Latin-only font for a
#: Greek document would render boxes instead of letters and the page would be unreadable for a
#: reason that has nothing to do with degradation.
#:
#: Arabic is deliberately **not** rendered on a page. ReportLab does no bidirectional layout and
#: no Arabic contextual shaping, so it would draw disconnected letter forms in the wrong order —
#: a page no reader could read, degraded or not, and one that would silently make an abstention
#: rate look worse than the degradation warrants. The Arabic surface forms stay in the party
#: register, where claim 6 uses them as strings without ever needing them printed.
#: **One font, in one place, and no fallback. This used to be a list.**
#:
#: It held three paths and took the first that existed: Arial Unicode on the author's laptop,
#: DejaVu on a Linux runner, FreeSans elsewhere. Box geometry comes from font metrics, so each
#: of those produces a *different ground truth* — and DejaVu covers no CJK, so on a runner the
#: thirteen Chinese characters in the corpus's party names rendered as empty boxes while every
#: check stayed green.
#:
#: The generator already knew this mattered: `--check` has an error written for a changed font,
#: saying a different font is a different ground truth. It never fired, because it compared the
#: runner's font against the corpus.json the runner had just written.
#:
#: So the font is the image's, exactly like the reader binary, and there is nothing to fall back
#: to. A corpus that cannot be generated outside the image is a corpus whose boxes mean the same
#: thing on a laptop and in the estate.
_FONT_CANDIDATES: Final = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",)

#: Noto ships as a TrueType *collection*; index 0 is the regular face. Named rather than left as
#: a bare zero because a collection with a different order would silently change every glyph.
_FONT_SUBFONT_INDEX: Final = 0

BODY = "corpus-body"
BOLD = "corpus-bold"
_MONO = "Courier"

#: What a machine that is not the image may fall back to, and **only** for tests.
#:
#: The corpus that ships is generated in the image and nowhere else. But the tests in
#: `tests/corpus/` build a four-shipment corpus in-process to assert *structural* properties —
#: that a table breaks across a page boundary, that every placement carries a box, that one seed
#: gives one corpus. None of those depends on which font drew the glyphs, and a suite that can
#: only run inside a container is a suite a reader cannot run.
#:
#: The artefact stays protected by the layer that matters: the font path is recorded in the
#: ground truth, and `--check` compares it, so a corpus generated on a laptop can be built and
#: can never be mistaken for the committed one.
_FALLBACK_CANDIDATES: Final = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)

_registered = False
#: The path chosen this process, so a non-strict call made by a test is honoured by the strict
#: calls `build()` makes afterwards. Without it the fixture would register a fallback and the
#: generator would immediately refuse the same machine.
_chosen: str | None = None


class FontError(RuntimeError):
    """No font on this machine covers the corpus's scripts."""


def register_fonts(strict: bool = True) -> str:
    """Register the corpus font, returning the path used.

    Raises rather than falling back to a built-in font. A Latin-only fallback would render the
    Greek documents as empty boxes, every claim scored on them would be scored on a page nobody
    could read, and the run would still be green — which is not a hypothetical: on a Linux
    runner the old candidate list picked DejaVu, and the corpus's Chinese party names became
    tofu while every check passed.

    `strict=False` is for `tests/corpus/` and for nothing else. Those tests build a small corpus
    in-process to assert structural properties — a table breaking across a page, every placement
    carrying a box, one seed giving one corpus — and none of them depends on which font drew the
    glyphs. A suite that can only run inside a container is a suite a reader cannot run. The
    artefact stays protected where it matters: the font path is recorded in the ground truth and
    `--check` compares it, so a laptop-built corpus can never be mistaken for the committed one.
    """
    global _registered, _chosen  # noqa: PLW0603 — reportlab's font registry is process-global
    if _chosen is not None:
        return _chosen
    candidates = _FONT_CANDIDATES if strict else (*_FONT_CANDIDATES, *_FALLBACK_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            if not _registered:
                index = _FONT_SUBFONT_INDEX if path.suffix == ".ttc" else 0
                pdfmetrics.registerFont(TTFont(BODY, str(path), subfontIndex=index))
                pdfmetrics.registerFont(TTFont(BOLD, str(path), subfontIndex=index))
                _registered = True
            _chosen = str(path)
            return _chosen
    raise FontError(
        f"the corpus font is not on this machine; expected {_FONT_CANDIDATES[0]}.\n\n"
        f"That path is inside the reader image, and the corpus is generated there on purpose: "
        f"box geometry comes from font metrics, so a corpus rendered with whatever font a "
        f"machine happens to have is a different ground truth on every machine. This used to "
        f"fall back through a list, and on a Linux runner it picked DejaVu — which covers no "
        f"CJK, so the Chinese party names became empty boxes and every check stayed green.\n\n"
        f"Generate the corpus the way the ceremony does:\n"
        f'  docker build -t manifest-reader . && docker run --rm -v "$PWD:/work" -w /work \\\n'
        f"    -e PYTHONPATH=/work/src --entrypoint /usr/local/bin/python manifest-reader \\\n"
        f"    -m corpus.generate"
    )


@dataclass(frozen=True, slots=True)
class Placed:
    """A value drawn on a page, and where it is.

    `page` is 1-based to match the normalised representation. `box` is in fractions of the
    page, already converted from ReportLab's coordinate system.
    """

    field: str
    value: str
    page: int
    box: Box


@dataclass
class Sheet:
    """One document being drawn, accumulating its own ground truth."""

    title: str
    placements: list[Placed] = field(default_factory=list)
    _pdf: canvas.Canvas | None = None
    _buffer: BytesIO = field(default_factory=BytesIO)
    _page: int = 1

    def __post_init__(self) -> None:
        register_fonts()
        self._pdf = canvas.Canvas(self._buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
        self._pdf.setTitle(self.title)

    # ── Drawing ──────────────────────────────────────────────────────────────

    @property
    def canvas(self) -> canvas.Canvas:
        assert self._pdf is not None
        return self._pdf

    @property
    def page(self) -> int:
        return self._page

    def text(self, x: float, y: float, value: str, size: float = 9, font: str = BODY) -> Box:
        """Draw a string and return its box. Not recorded as a field."""
        self.canvas.setFont(font, size)
        self.canvas.drawString(x, y, value)
        return _box(x, y, pdfmetrics.stringWidth(value, font, size), font, size)

    def field(
        self,
        name: str,
        x: float,
        y: float,
        value: str,
        size: float = 9,
        font: str = BODY,
    ) -> Box:
        """Draw a value and record it as ground truth for `name`."""
        box = self.text(x, y, value, size=size, font=font)
        self.placements.append(Placed(field=name, value=value, page=self._page, box=box))
        return box

    def labelled(
        self,
        name: str,
        x: float,
        y: float,
        caption: str,
        value: str,
        size: float = 9,
        gap: float = 11,
    ) -> Box:
        """A small-caps caption with the value beneath it — the shape of a real form box.

        The caption is drawn as ordinary text and **not** recorded. Only the value is ground
        truth: a reader that returns the caption instead of the value has made a mistake, and
        recording both would let that mistake verify.
        """
        self.text(x, y, caption, size=size - 2.2)
        return self.field(name, x, y - gap, value, size=size, font=BOLD)

    def rule(self, x1: float, y: float, x2: float, width: float = 0.4) -> None:
        self.canvas.setLineWidth(width)
        self.canvas.line(x1, y, x2, y)

    def box_outline(self, x: float, y: float, width: float, height: float) -> None:
        self.canvas.setLineWidth(0.4)
        self.canvas.rect(x, y, width, height, stroke=1, fill=0)

    def new_page(self) -> None:
        self.canvas.showPage()
        self._page += 1

    def render(self) -> bytes:
        self.canvas.showPage()
        self.canvas.save()
        return self._buffer.getvalue()


def _box(x: float, baseline: float, width: float, font: str, size: float) -> Box:
    """A glyph run's box, converted from ReportLab's coordinates to the page's fractions.

    The one conversion in the corpus. ReportLab's y is a baseline measured upward from the
    bottom; the box's top is the ascent above that baseline, measured downward from the top.
    """
    ascent, descent = pdfmetrics.getAscentDescent(font, size)
    top_pt = PAGE_HEIGHT - (baseline + ascent)
    height_pt = ascent - descent  # descent is negative
    return Box(
        left=x / PAGE_WIDTH,
        top=top_pt / PAGE_HEIGHT,
        width=max(width, 0.5) / PAGE_WIDTH,
        height=max(height_pt, 0.5) / PAGE_HEIGHT,
    )


def money(amount: Decimal, currency: str) -> str:
    """A monetary amount as it is printed, with the grouping a European invoice uses.

    Thousands with a dot and decimals with a comma, which is what makes the parser's refusal of
    an ambiguous single separator worth having: this is the convention that produces `1.250,50`,
    and the Anglo-American documents in the same corpus produce `1,250.50`.
    """
    quantised = amount.quantize(Decimal("0.01"))
    whole, _, fraction = f"{quantised:,.2f}".partition(".")
    return f"{currency} {whole.replace(',', '.')},{fraction}"


def plain_money(amount: Decimal) -> str:
    """The Anglo-American convention, for the documents that use it."""
    return f"{amount.quantize(Decimal('0.01')):,.2f}"
