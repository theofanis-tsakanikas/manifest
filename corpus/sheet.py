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
_FONT_CANDIDATES: Final = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)

BODY = "corpus-body"
BOLD = "corpus-bold"
_MONO = "Courier"

_registered = False


class FontError(RuntimeError):
    """No font on this machine covers the corpus's scripts."""


def register_fonts() -> str:
    """Register the corpus font, returning the path used.

    Raises rather than falling back to a built-in font. A Latin-only fallback would render the
    Greek documents as empty boxes, every claim scored on them would be scored on a page nobody
    could read, and the run would still be green.
    """
    global _registered  # noqa: PLW0603 — reportlab's font registry is process-global
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            if not _registered:
                pdfmetrics.registerFont(TTFont(BODY, str(path)))
                pdfmetrics.registerFont(TTFont(BOLD, str(path)))
                _registered = True
            return str(path)
    raise FontError(
        "no font covering Latin, Greek and CJK was found. The corpus cannot be generated "
        f"without one; looked for {list(_FONT_CANDIDATES)}. Falling back to a Latin-only font "
        "would render every Greek document as empty boxes while the run stayed green"
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
