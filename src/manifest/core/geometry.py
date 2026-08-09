"""Where a value is on a page — the half of provenance that can be checked.

Claim 2 says every published field traces to a page, a bounding box and a document version,
*verified independently*. Everything downstream of that sentence rests on the coordinates
meaning one thing, so this module is where they are given that meaning and nowhere else.

**Normalised to the page, origin top-left, x right, y down.** A box is four fractions of the
page in `[0, 1]`, never pixels. The reason is not taste: the corpus is rendered at one
resolution, the tier-0 engine reads it at another, an escalated page could be read at a third,
and a managed service returns fractions of the page rather than pixels at all. Pixels in the
record would make a stored box a fact about the raster somebody happened to produce, and the
first time a page is re-rendered at a different DPI every provenance record in the archive
would point somewhere slightly wrong — silently, because a box a few pixels off still lands on
ink most of the time. Fractions survive re-rendering; pixels do not.

**Boxes are `left, top, width, height`, not two corners.** Because that is the shape both a
page-level layout reader and a per-word reader emit, and a conversion nobody asked for is a
place to put a sign error.

**Rounding out, never in.** `to_pixels` floors the near edges and ceilings the far ones, so a
crop is never smaller than the box it came from. Rounding to nearest is defensible and wrong
here: it can shave a column of pixels off a digit, and the verifier would then be re-reading a
`3` with its top stroke missing and reporting that the record was false. The error a rounding
rule makes should be the one that costs a person a second look, not the one that fabricates a
failure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Self

#: The tolerance for a coordinate that a float round-trip has pushed marginally outside the
#: page. A reader that reports `top = 1.0000000000000002` has not made a mistake worth
#: refusing a document over; a reader that reports `1.4` has. Anything inside this band is
#: clamped, anything outside it raises.
_EPSILON: Final = 1e-6


class GeometryError(ValueError):
    """A box that cannot describe a location on a page.

    A subclass rather than a bare `ValueError` so a caller can tell a malformed box apart from
    every other bad argument — the two get handled differently, because a malformed box means
    the adapter is wrong and a bad argument means the caller is.
    """


@dataclass(frozen=True, slots=True)
class Box:
    """A rectangle on a page, as fractions of the page's width and height.

    Validated on construction. An unvalidated box is worse than no box: it will be stored,
    published as provenance, and only discovered when somebody tries to look at it.
    """

    left: float
    top: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name, value in (
            ("left", self.left),
            ("top", self.top),
            ("width", self.width),
            ("height", self.height),
        ):
            if not isinstance(value, int | float) or math.isnan(value):
                raise GeometryError(f"{name} is not a number: {value!r}")

        object.__setattr__(self, "left", _clamp_or_raise("left", self.left))
        object.__setattr__(self, "top", _clamp_or_raise("top", self.top))

        if self.width <= 0 or self.height <= 0:
            raise GeometryError(
                f"a box with no area cannot locate anything: width={self.width}, "
                f"height={self.height}"
            )
        right, bottom = self.left + self.width, self.top + self.height
        if right > 1 + _EPSILON or bottom > 1 + _EPSILON:
            raise GeometryError(
                f"the box leaves the page: right={right}, bottom={bottom}; coordinates are "
                f"fractions of the page, so an adapter reporting pixels lands here"
            )
        object.__setattr__(self, "width", min(self.width, 1.0 - self.left))
        object.__setattr__(self, "height", min(self.height, 1.0 - self.top))

    # ── Reading it ───────────────────────────────────────────────────────────

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    # ── Building one ─────────────────────────────────────────────────────────

    @classmethod
    def from_pixels(
        cls, left: float, top: float, width: float, height: float, page: PageSize
    ) -> Self:
        """The box a per-word reader reported in pixels, as fractions of its page.

        This is the one place a pixel measurement is allowed to become a stored coordinate,
        and it is called from an adapter. Anything downstream that still holds pixels is a
        bug that will survive until the day the corpus is re-rendered.
        """
        return cls(
            left=left / page.width,
            top=top / page.height,
            width=width / page.width,
            height=height / page.height,
        )

    @classmethod
    def hull(cls, boxes: tuple[Box, ...] | list[Box]) -> Self:
        """The smallest box containing all of them.

        A field is usually several words, and its provenance is one rectangle. Taking the hull
        rather than a list of word boxes is a deliberate loss: it is what a human can be shown
        and what a crop can be taken from. The word boxes stay on the blocks that produced it.
        """
        if not boxes:
            raise GeometryError("the hull of no boxes is not a location")
        left = min(box.left for box in boxes)
        top = min(box.top for box in boxes)
        return cls(
            left=left,
            top=top,
            width=max(box.right for box in boxes) - left,
            height=max(box.bottom for box in boxes) - top,
        )

    # ── Relating two ─────────────────────────────────────────────────────────

    def intersection(self, other: Box) -> Box | None:
        """The overlap, or None when they do not touch.

        None rather than a zero-area box, because a zero-area box cannot be constructed — and
        making it constructible so that this method could return one would be letting a
        convenience decide an invariant.
        """
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        # Against `_EPSILON`, not against zero. Two boxes meeting exactly at an edge produce
        # `right - left` of the order of 1e-17, because `0.1 + 0.2` is not `0.3` — and a
        # comparison against zero therefore answers "they overlap" and hands back a rectangle
        # a hundred-billionth of a pixel wide. It is not a rounding curiosity: `iou` would
        # then report a non-zero overlap for two boxes that share only a boundary, and a
        # provenance check comparing a recorded box against a neighbouring word would score
        # above nothing where the honest answer is nothing.
        if right - left <= _EPSILON or bottom - top <= _EPSILON:
            return None
        return Box(left=left, top=top, width=right - left, height=bottom - top)

    def iou(self, other: Box) -> float:
        """Intersection over union, in `[0, 1]`.

        The measure a provenance check uses to ask *"is the box the record stored the same box
        the page actually has ink in?"* — not equality, because two readers segment a word
        differently by a pixel or two and equality would refuse every honest record.
        """
        overlap = self.intersection(other)
        if overlap is None:
            return 0.0
        return overlap.area / (self.area + other.area - overlap.area)

    def contains(self, other: Box) -> bool:
        return (
            other.left >= self.left - _EPSILON
            and other.top >= self.top - _EPSILON
            and other.right <= self.right + _EPSILON
            and other.bottom <= self.bottom + _EPSILON
        )

    # ── Turning it back into somewhere to look ───────────────────────────────

    def padded(self, margin: float) -> Box:
        """The same box grown by `margin` on every side, clipped to the page.

        A crop taken at exactly the reported box cuts the ascenders and descenders a reader
        used to recognise the word, and re-reading that crop produces a worse answer than the
        one being verified — which would make the verifier's disagreement evidence about the
        crop rather than about the record. The margin is the fix, and it is an argument rather
        than a constant because the right amount depends on the check being run.
        """
        if margin < 0:
            raise GeometryError(f"a negative margin shrinks the crop: {margin}")
        left = max(0.0, self.left - margin)
        top = max(0.0, self.top - margin)
        return Box(
            left=left,
            top=top,
            width=min(1.0, self.right + margin) - left,
            height=min(1.0, self.bottom + margin) - top,
        )

    def to_pixels(self, page: PageSize) -> PixelRect:
        """Where to cut, in whole pixels, on a raster of this size.

        Out, never in — see the module docstring. The rectangle is half-open on its far edges,
        which is what every image library means by a crop box, so `right - left` is the width
        in pixels and no caller has to add one.
        """
        left = math.floor(self.left * page.width)
        top = math.floor(self.top * page.height)
        right = max(left + 1, math.ceil(self.right * page.width))
        bottom = max(top + 1, math.ceil(self.bottom * page.height))
        return PixelRect(
            left=min(left, page.width - 1),
            top=min(top, page.height - 1),
            right=min(right, page.width),
            bottom=min(bottom, page.height),
        )


@dataclass(frozen=True, slots=True)
class PageSize:
    """A raster's dimensions in pixels.

    Carried explicitly rather than read off an image, because the core is given its pages and
    does not go and find them. The adapter that opened the raster passes this in.
    """

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise GeometryError(f"a page with no pixels: {self.width}x{self.height}")


@dataclass(frozen=True, slots=True)
class PixelRect:
    """A crop rectangle, half-open on `right` and `bottom`."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_tuple(self) -> tuple[int, int, int, int]:
        """`(left, top, right, bottom)` — the shape an image library's crop takes."""
        return (self.left, self.top, self.right, self.bottom)


def _clamp_or_raise(name: str, value: float) -> float:
    if -_EPSILON <= value <= 1 + _EPSILON:
        return min(max(value, 0.0), 1.0)
    raise GeometryError(
        f"{name}={value} is outside the page; coordinates are fractions of the page, so an "
        f"adapter that forgot to divide by the page size lands here"
    )
