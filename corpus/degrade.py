"""Turning a clean render into a scan of a fax of a scan.

Every operation here is seeded and deterministic. The corpus is the thing every claim is
measured against, so a degradation that varied between runs would make every number in this
repository a number about a corpus nobody else can produce.

**The rule that shapes this module: ground truth follows the pixels.** A skew rotates the page,
which moves every value on it. If the recorded boxes stayed where they were, claim 2's ground
truth would be describing an image that no longer exists — and the provenance gate would be
scored against boxes that are wrong for a reason the gate did not cause. So the same transform
that rotates the raster rotates the boxes, and `tests/corpus/` asserts it by planting a marker,
rotating, and checking the transformed box still lands on the ink.

**Skew is background, not a pathology.** `docs/AWS-CONSTRAINTS.md`: both managed readers
document full support for in-plane rotation, up to 45°. A corpus whose difficulty came mostly
from skew would produce a cascade that never escalates and an abstention rate that means
nothing. So skew is applied to every page as ordinary imperfection, and the pathologies that a
claim is *scored* against are the ones that actually destroy information.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from corpus.sheet import Placed
from corpus.world import Pathology
from manifest.core.geometry import Box, PageSize


@dataclass(frozen=True, slots=True)
class Degraded:
    """A degraded page and the placements as they now stand on it."""

    image: Image.Image
    placements: tuple[Placed, ...]


def degrade_page(
    image: Image.Image,
    placements: tuple[Placed, ...],
    pathologies: tuple[Pathology, ...],
    generator: random.Random,
) -> Degraded:
    """Apply every degradation to one page, moving its ground truth with it."""
    working = image.convert("L")
    moved = placements

    if Pathology.BLEED_THROUGH in pathologies:
        working = _bleed_through(working, generator)

    for placement in placements:
        if Pathology.STAMP_OVER_FIELD in pathologies and placement.field == "country_of_origin":
            working = _stamp(working, placement.box, generator)
        if Pathology.ILLEGIBLE_FIELD in pathologies and placement.field in _ILLEGIBLE_TARGETS:
            working = _obliterate(working, placement.box, generator)
        if (
            Pathology.HANDWRITTEN_CORRECTION in pathologies
            and placement.field in _HANDWRITTEN_TARGETS
        ):
            working = _handwrite_over(working, placement.box, generator)

    angle = generator.uniform(-1.4, 1.4)
    working = working.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=245, expand=False)
    moved = tuple(
        Placed(
            field=placement.field,
            value=placement.value,
            page=placement.page,
            box=rotate_box(
                placement.box, angle, PageSize(width=working.width, height=working.height)
            ),
        )
        for placement in moved
    )

    working = _noise(working, generator)
    working = _recompress(working, generator)
    return Degraded(image=working, placements=moved)


#: Fields the illegibility pathology may destroy. Chosen so that an abstention is *countable*:
#: each is a declared field with a real consequence, so a reader that publishes anything for it
#: has published something it could not have read.
_ILLEGIBLE_TARGETS = frozenset({"container_number", "country_of_origin", "declared_value"})

#: Fields a handwritten correction may land on. The tier-0 reader reads effectively none of it
#: (ADR-0005), so this is an abstention generator by construction rather than a test of
#: handwriting recognition — and the README says so rather than implying the system reads hands.
_HANDWRITTEN_TARGETS = frozenset({"gross_weight", "package_count"})


def rotate_box(box: Box, degrees: float, page: PageSize) -> Box:
    """Where a box lands after the page is rotated about its centre.

    The result is the axis-aligned hull of the rotated corners, which is slightly larger than
    the rotated rectangle. That is the correct direction to be wrong in: a box that grew still
    contains its value, and ADR-0003's crop rule already rounds outward for the same reason.

    PIL rotates counter-clockwise as displayed, and image coordinates have y increasing
    downwards, which is why the sine terms sit the way they do. Getting this backwards produces
    boxes that are wrong by twice the skew — invisible on a lightly skewed page, and exactly the
    kind of silent error `tests/corpus/test_degrade.py` plants a marker to catch.
    """
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    centre_x, centre_y = 0.5, 0.5

    corners = [
        (box.left, box.top),
        (box.right, box.top),
        (box.left, box.bottom),
        (box.right, box.bottom),
    ]
    # The page is not square, so a rotation in *fractions* is not a rotation. Convert to a
    # square space using the aspect ratio, rotate there, convert back — otherwise a 1° skew
    # moves a box by the wrong amount in y, and the error grows with distance from the centre.
    aspect = page.height / page.width
    moved = []
    for x, y in corners:
        dx = x - centre_x
        dy = (y - centre_y) * aspect
        rx = dx * cosine + dy * sine
        ry = -dx * sine + dy * cosine
        moved.append((centre_x + rx, centre_y + ry / aspect))

    left = min(x for x, _ in moved)
    top = min(y for _, y in moved)
    right = max(x for x, _ in moved)
    bottom = max(y for _, y in moved)

    # Clamped rather than refused. A value rotated off the edge of the page is a document that
    # would be re-scanned in real life; here it is a box that must stay constructible so the
    # generator does not fail on one page in ten thousand.
    left, top = max(0.0, min(left, 0.999)), max(0.0, min(top, 0.999))
    right, bottom = max(left + 1e-4, min(right, 1.0)), max(top + 1e-4, min(bottom, 1.0))
    return Box(left=left, top=top, width=right - left, height=bottom - top)


def _to_pixels(box: Box, image: Image.Image) -> tuple[int, int, int, int]:
    return box.to_pixels(PageSize(width=image.width, height=image.height)).as_tuple()


def _stamp(image: Image.Image, box: Box, generator: random.Random) -> Image.Image:
    """A chamber's stamp, landing over the field.

    Drawn as a ring with text across it, at low opacity, rotated — which is what a rubber stamp
    on a scanned certificate actually looks like. It does not erase the value; it interferes
    with it, which is the harder and more realistic case: the reader returns *something*, with
    a lower confidence, and claim 1 has to decide whether that something may be published.
    """
    left, top, right, bottom = _to_pixels(box, image)
    radius = int(max(right - left, bottom - top) * generator.uniform(1.1, 1.5))
    centre = (
        (left + right) // 2 + generator.randint(-radius // 4, radius // 4),
        (top + bottom) // 2 + generator.randint(-radius // 5, radius // 5),
    )
    size = radius * 4
    stamp = Image.new("L", (size, size), 255)
    pen = ImageDraw.Draw(stamp)
    inset = size // 2 - radius
    grey = generator.randint(70, 130)
    pen.ellipse(
        [inset, inset, size - inset, size - inset], outline=grey, width=max(3, radius // 12)
    )
    pen.ellipse(
        [
            inset + radius // 4,
            inset + radius // 4,
            size - inset - radius // 4,
            size - inset - radius // 4,
        ],
        outline=grey,
        width=max(2, radius // 20),
    )
    pen.line([size // 2 - radius, size // 2, size // 2 + radius, size // 2], fill=grey, width=3)
    stamp = stamp.rotate(
        generator.uniform(-30, 30), resample=Image.Resampling.BICUBIC, fillcolor=255
    )
    stamp = stamp.filter(ImageFilter.GaussianBlur(0.6))

    target = image.copy()
    patch_box = (centre[0] - size // 2, centre[1] - size // 2)
    region = target.crop((patch_box[0], patch_box[1], patch_box[0] + size, patch_box[1] + size))
    if region.size != stamp.size:
        return target
    target.paste(Image.blend(region, ImageChops_min(region, stamp), 0.85), patch_box)
    return target


def ImageChops_min(a: Image.Image, b: Image.Image) -> Image.Image:
    """Per-pixel darker-of-the-two.

    Ink on paper is subtractive: a stamp over text makes the page darker where either mark is,
    never lighter. Compositing by averaging would *lighten* the text under the stamp, which is
    the opposite of what a rubber stamp does and would make the pathology easier rather than
    harder.
    """
    return Image.fromarray(np.minimum(np.asarray(a), np.asarray(b)))


def _obliterate(image: Image.Image, box: Box, generator: random.Random) -> Image.Image:
    """Destroy a field, so an abstention there is exact.

    A heavy local blur plus noise rather than a black rectangle: a blacked-out field is trivial
    to detect and would let a system abstain for the wrong reason — recognising a redaction
    rather than failing to read. What this leaves is a smear that still *looks* like text, which
    is what a reader has to be uncertain about.
    """
    left, top, right, bottom = _to_pixels(box.padded(0.004), image)
    region = image.crop((left, top, right, bottom))
    region = region.filter(ImageFilter.GaussianBlur(generator.uniform(2.6, 4.2)))
    array = np.asarray(region).astype(np.int16)
    rng = np.random.default_rng(generator.randrange(2**32))
    array = array + rng.normal(0, 26, array.shape)
    target = image.copy()
    target.paste(Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)), (left, top))
    return target


def _handwrite_over(image: Image.Image, box: Box, generator: random.Random) -> Image.Image:
    """A struck-through value with a correction scrawled beside it.

    Not handwriting recognition: the tier-0 reader reads effectively none of this, so what the
    corpus is exercising is whether the system *abstains* on a field a human has amended, rather
    than publishing the printed value that has been struck out. Publishing the crossed-out
    number is the failure worth planting, and it is one a system with no notion of a strike
    would make silently.
    """
    left, top, right, bottom = _to_pixels(box, image)
    target = image.copy()
    pen = ImageDraw.Draw(target)
    ink = generator.randint(20, 70)

    middle = (top + bottom) // 2
    points = []
    for step in range(9):
        x = left + (right - left) * step / 8
        points.append((x, middle + generator.randint(-3, 3)))
    pen.line(points, fill=ink, width=generator.randint(2, 4), joint="curve")

    # The correction: a wobbling polyline per character position, which is unreadable to the
    # reader and visibly a human's mark to a person.
    x = right + 8
    for _ in range(generator.randint(3, 5)):
        stroke = [
            (x + generator.randint(-2, 2), top + generator.randint(-4, 4)),
            (x + generator.randint(2, 7), middle + generator.randint(-5, 5)),
            (x + generator.randint(-1, 5), bottom + generator.randint(-3, 5)),
        ]
        pen.line(stroke, fill=ink, width=generator.randint(2, 3), joint="curve")
        x += generator.randint(9, 15)
    return target


def _bleed_through(image: Image.Image, generator: random.Random) -> Image.Image:
    """Faint mirrored text from the reverse side.

    Produced by mirroring the page itself and compositing it back at low contrast. Using the
    page's own content is deliberate: it puts *text-shaped* interference in the background,
    which is what actually confuses a layout reader, rather than generic noise a denoiser
    removes.
    """
    reverse = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    reverse = reverse.point(lambda value: 255 - int((255 - value) * generator.uniform(0.10, 0.20)))
    reverse = reverse.filter(ImageFilter.GaussianBlur(1.4))
    return ImageChops_min(image, reverse)


def _noise(image: Image.Image, generator: random.Random) -> Image.Image:
    """Gaussian and salt-and-pepper, plus the uneven illumination of a flatbed scanner."""
    rng = np.random.default_rng(generator.randrange(2**32))
    array = np.asarray(image).astype(np.float32)

    height, width = array.shape
    # A gentle diagonal gradient: the lamp is brighter at one edge. This is what makes a global
    # threshold the wrong tool and an adaptive one the right one, which is a real property of
    # scanned pages and not an inconvenience invented here.
    gradient_x = np.linspace(generator.uniform(-14, -4), generator.uniform(4, 14), width)
    gradient_y = np.linspace(generator.uniform(-8, -2), generator.uniform(2, 8), height)
    array = array + gradient_x[None, :] + gradient_y[:, None]

    array = array + rng.normal(0, generator.uniform(4.0, 11.0), array.shape)

    pepper = rng.random(array.shape) < generator.uniform(0.0004, 0.0022)
    salt = rng.random(array.shape) < generator.uniform(0.0004, 0.0022)
    array[pepper] = 0
    array[salt] = 255
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))


def _recompress(image: Image.Image, generator: random.Random) -> Image.Image:
    """JPEG, once or twice, at a quality a fax machine would be ashamed of.

    Twice sometimes, because the scenario is scans *of scans*: the artefacts of the first
    compression become the input to the second, and the ringing around glyph edges is what
    actually costs a reader its confidence.
    """
    for _ in range(generator.choice((1, 1, 2))):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=generator.randint(28, 68))
        image = Image.open(io.BytesIO(buffer.getvalue())).convert("L")
    return image
