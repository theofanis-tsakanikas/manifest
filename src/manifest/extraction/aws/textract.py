"""Mapping a managed OCR response into the normalised representation.

Written against the documented response schema (`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09).
**Called for the first time on 2026-08-15** — 2,336 eligible pages, 127,142 words, normalised and
committed to `recordings/textract/` — and the documented shape held. Its fixtures stay authored
from that schema rather than captured, for the reason in
`tests/extraction/fixtures/AUTHORED.md`: the recording proves what the service returns, and the
fixture proves the documentation was read correctly. They are different claims.

Three things the documentation decides, each of which would otherwise be a guess:

**Geometry is already fractions of the page.** `BoundingBox.Left` is documented as "the left
coordinate of the bounding box as a ratio of overall document page width", origin top-left.
That is exactly `manifest.core.geometry.Box`, so this adapter renames and does not convert —
and the tier-0 adapter is the one that divides, because a per-word local reader reports pixels.

**Confidence is 0–100.** Divided here, once, by the adapter that knows its own reader's scale.
That is arithmetic and not calibration: ADR-0004 forbids rescaling two readers' scores into a
common range, because two readers' 0.8 are different events and claim 1 exists to derive the
difference rather than assume it.

**Blocks are a flat list with parent-child relationships.** A `LINE` names its `WORD` children
through a `CHILD` relationship rather than containing them, so reconstructing a line means
resolving ids. Reading the `LINE` blocks' own text instead would be easier and would lose the
per-word confidence that claim 1 is derived from.
"""

from __future__ import annotations

from typing import Any

from manifest.core.document import (
    DocumentError,
    Page,
    ReadDocument,
    ReaderIdentity,
    Word,
    build_line,
)
from manifest.core.geometry import Box, PageSize

#: The documented confidence scale for this service: 0 to 100.
_SCALE = 100.0


class ResponseError(ValueError):
    """A response that does not match the documented schema.

    Raised rather than skipped. An adapter that quietly drops a block it did not understand
    produces a reading that is short by an unknown amount, and nothing downstream can tell the
    difference between a page with less text on it and a page whose adapter gave up.
    """


def to_document(
    *,
    source_id: str,
    source_digest: str,
    response: dict[str, Any],
    page_sizes: dict[int, PageSize],
    language: str,
    service_version: str,
) -> ReadDocument:
    """One documented response, as a `ReadDocument`.

    `page_sizes` is passed in because the response gives geometry as fractions and never states
    the raster's pixel dimensions — the caller rasterised the page and is the only thing that
    knows. A default here would be a made-up page size baked into every provenance record.
    """
    blocks = response.get("Blocks")
    if not isinstance(blocks, list):
        raise ResponseError("the response has no `Blocks` list; this is not the documented shape")

    by_id = {block["Id"]: block for block in blocks if "Id" in block}
    words_by_page: dict[int, dict[str, Word]] = {}
    lines_by_page: dict[int, list[list[str]]] = {}

    for block in blocks:
        kind = block.get("BlockType")
        page = int(block.get("Page", 1))
        if kind == "WORD":
            words_by_page.setdefault(page, {})[block["Id"]] = _word(block, page_sizes, page)
        elif kind == "LINE":
            children = [
                child
                for relationship in block.get("Relationships", [])
                if relationship.get("Type") == "CHILD"
                for child in relationship.get("Ids", [])
            ]
            if children:
                lines_by_page.setdefault(page, []).append(children)

    pages = []
    for number in sorted(page_sizes):
        words = words_by_page.get(number, {})
        lines = []
        for children in lines_by_page.get(number, []):
            members = [words[child] for child in children if child in words]
            if members:
                lines.append(build_line(members))
        unknown = {
            child
            for children in lines_by_page.get(number, [])
            for child in children
            if child not in words and child in by_id
        }
        if unknown:
            raise ResponseError(
                f"page {number} has a LINE whose CHILD ids resolve to blocks that are not "
                f"WORDs: {sorted(unknown)[:3]}. Dropping them would produce a reading short by "
                f"an unknown amount, and nothing downstream could tell that from a shorter page"
            )
        pages.append(
            Page(
                number=number,
                size=page_sizes[number],
                lines=tuple(lines),
                language=language,
                # The service does not return a detected language (`docs/AWS-CONSTRAINTS.md`:
                # "Amazon Textract will not return the language detected in its output"). So
                # the caller's assertion is recorded at full confidence and the fact that it is
                # an assertion rather than a detection is stated here — a fabricated detection
                # confidence would make claim 4's language routing look measured.
                language_confidence=1.0,
            )
        )

    if not pages:
        raise ResponseError("no pages were reconstructed from this response")

    return ReadDocument(
        source_id=source_id,
        source_digest=source_digest,
        reader=ReaderIdentity(name="managed-ocr", version=service_version),
        pages=tuple(pages),
    )


def _word(block: dict[str, Any], page_sizes: dict[int, PageSize], page: int) -> Word:
    if page not in page_sizes:
        raise ResponseError(f"the response has a block on page {page} and no size was given for it")
    geometry = block.get("Geometry", {}).get("BoundingBox")
    if not isinstance(geometry, dict):
        raise ResponseError(f"word block {block.get('Id')} has no BoundingBox")
    try:
        box = Box(
            left=float(geometry["Left"]),
            top=float(geometry["Top"]),
            width=float(geometry["Width"]),
            height=float(geometry["Height"]),
        )
    except (KeyError, TypeError) as exc:
        raise ResponseError(f"word block {block.get('Id')} has an incomplete BoundingBox") from exc

    try:
        return Word(
            text=str(block["Text"]),
            confidence=min(float(block["Confidence"]) / _SCALE, 1.0),
            box=box,
        )
    except (KeyError, DocumentError) as exc:
        raise ResponseError(f"word block {block.get('Id')}: {exc}") from exc
