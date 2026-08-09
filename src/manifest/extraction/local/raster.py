"""The page, as pixels — the adapter behind claim 2's gate.

`manifest.gates.provenance` is a pure function of what this returns. Everything that opens an
image, thresholds it or starts a process lives here, which is what keeps the gate itself
checkable without a page and what stops an imaging library appearing in the core.

**Binarisation is adaptive, not a global threshold.** The corpus renders a lamp gradient across
every page on purpose (`corpus/degrade.py`), because that is what a flatbed scanner does. A
global threshold on a page that is brighter at one edge calls one side blank and the other side
saturated, and Layer A would then refuse honest records at the dark edge and pass empty crops at
the light one. The mean of the crop's own neighbourhood is the threshold, which is what makes
the measurement local.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from manifest.core.geometry import Box, PageSize
from manifest.extraction.local import reader as local
from manifest.gates.provenance import InkStatistics

#: How much darker than its neighbourhood a pixel must be to count as ink. Below this, JPEG
#: ringing around a glyph edge reads as ink and a blank crop looks occupied; far above it, thin
#: strokes on a degraded page disappear and Layer A refuses records that are correct.
INK_MARGIN = 18.0

#: Padding around a crop when measuring its neighbourhood mean. The threshold has to come from
#: paper as well as ink, and a crop that is mostly text would otherwise set its own threshold
#: from the text.
NEIGHBOURHOOD = 0.02

#: How much a crop is enlarged before being re-read. The binary is trained on text around 30
#: pixels tall; a 9 pt value at 300 DPI is about 12, so re-reading at native size would disagree
#: with the full-page pass for a reason that has nothing to do with the record.
UPSCALE = 4

#: White paper added around the crop, in pixels of the upscaled image.
BORDER = 24

#: A crop smaller than this in either direction is not a value; it is a rounding artefact.
SMALLEST_CROP = 4

#: A crop smaller than this in either direction is not a value; it is a rounding artefact.
SMALLEST_CROP = 4


@dataclass(frozen=True, slots=True)
class PageRaster:
    """One document's pages on disk, keyed by page number."""

    pages: dict[int, Path]

    def size(self, page: int) -> PageSize | None:
        path = self.pages.get(page)
        if path is None or not path.exists():
            return None
        with Image.open(path) as image:
            return PageSize(width=image.width, height=image.height)

    def ink(self, page: int, box: Box) -> InkStatistics | None:
        """Ink coverage under `box`, and how much of the box that ink fills.

        `fill` is the ratio of the ink's own bounding box to the recorded one. It is what
        catches a hull taken over the wrong span of words: a box three times too wide still
        contains its value, so coverage alone would pass it, and a human shown that crop would
        be reading a whole line to check one field.
        """
        path = self.pages.get(page)
        if path is None or not path.exists():
            return None
        with Image.open(path) as image:
            grey = image.convert("L")
            size = PageSize(width=grey.width, height=grey.height)
            crop = np.asarray(grey.crop(box.to_pixels(size).as_tuple())).astype(np.float32)
            wide = np.asarray(
                grey.crop(box.padded(NEIGHBOURHOOD).to_pixels(size).as_tuple())
            ).astype(np.float32)

        if crop.size == 0 or wide.size == 0:
            return None
        mask = crop < (wide.mean() - INK_MARGIN)
        coverage = float(mask.mean())

        rows = np.where(mask.any(axis=1))[0]
        columns = np.where(mask.any(axis=0))[0]
        if rows.size == 0 or columns.size == 0:
            return InkStatistics(coverage=coverage, fill=0.0)
        height = (rows[-1] - rows[0] + 1) / mask.shape[0]
        width = (columns[-1] - columns[0] + 1) / mask.shape[1]
        return InkStatistics(coverage=coverage, fill=float(height * width))

    def reread(self, page: int, box: Box, language: str) -> tuple[str, float]:
        """The crop, re-read through the single-unit path. ADR-0003, Layer B.

        Two preparations, and the harness found both by refusing honest records until they
        were there. **Upscaling**, because the binary is trained on text about 30 pixels tall
        and a 9 pt value at 300 DPI is about 12. **A white border**, because the segmenter needs
        paper around the text to find a line at all — a crop cut flush to the glyphs reads as
        nothing, and a verifier that returns nothing for a correct record refuses it. Ten of the
        first forty honest records were refused for exactly that, which is a gate somebody
        mutes rather than a gate that works.
        """
        path = self.pages.get(page)
        if path is None or not path.exists():
            return "", 0.0
        with Image.open(path) as image:
            grey = image.convert("L")
            size = PageSize(width=grey.width, height=grey.height)
            crop = grey.crop(box.to_pixels(size).as_tuple())
            if crop.width < SMALLEST_CROP or crop.height < SMALLEST_CROP:
                return "", 0.0
            crop = crop.resize(
                (crop.width * UPSCALE, crop.height * UPSCALE),
                resample=Image.Resampling.LANCZOS,
            )
            # A white border, and it is not cosmetic. The reader's line finder needs paper
            # around the text to decide where a line is; a crop that starts at the first
            # stroke and ends at the last one reads as a fragment and comes back empty.
            crop = ImageOps.expand(crop, border=BORDER, fill=255)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
                temporary = Path(handle.name)
                crop.save(handle, format="PNG")
        try:
            text, confidence = local.read_crop(temporary, language)
            if text.strip():
                return text, confidence
            # A second attempt in single-*word* mode. A short value — a country code, a
            # currency, a three-letter incoterm — is not a line, and the line finder can
            # refuse to see one. Still a different recognition path from the full-page
            # pass, which is all Layer B claims.
            return local.read_crop(temporary, language, local.Segmentation.SINGLE_WORD)
        except local.ReaderUnavailable:
            return "", 0.0
        finally:
            temporary.unlink(missing_ok=True)


def for_document(directory: Path, shipment: str, document: str, pages: int) -> PageRaster:
    """The raster for one document in the committed corpus."""
    return PageRaster(
        pages={
            number: directory / f"{shipment}_{document}_p{number}.jpg"
            for number in range(1, pages + 1)
        }
    )
