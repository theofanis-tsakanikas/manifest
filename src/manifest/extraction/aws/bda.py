"""Mapping a managed document-automation response into the normalised representation.

Written against the documented standard output for documents
([BDA output — documents](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-output-documents.html),
read 2026-08-10). Its fixtures are authored from that schema and marked as authored.

**This service reports no confidence. Anywhere.**

That is the fact that shapes the whole module, and it is worth stating flatly because it is not
what a reader expects from a document-extraction service. The documented word entity is:

    {"id": ..., "text": ..., "line_id": ..., "reading_order": ..., "page_index": ...,
     "locations": {"page_index": ..., "bounding_box": {"left","top","width","height"}}}

There is no confidence field on the word, none on the line, none on the element, and none on
the page. So every word this adapter produces carries `confidence=None`, and the consequences
are real and are declared rather than worked around:

- **Nothing read by this service can publish on its score**, because it has no score. Every
  field it supports goes to a human with `Reason.UNSCORED`.
- **It cannot contribute to claim 1.** A threshold is derived from a distribution of scores; a
  reader with no scores has no distribution. `evals/calibration/` never sees this reader.
- **The cascade cannot escalate *out* of it on confidence.** Routing a page here is a decision
  to spend a human on it, and `contracts/cascade/` has to say so where the tier is declared.

The alternative — handing the core a 1.0, or the mean of some other reader's scores, or a
"typical" value from the vendor's marketing — is doctrine rule 3 in its most expensive form.
1.0 clears every derived threshold in this repository, silently, on every page.

**What it is genuinely better at**, and why it is in the cascade at all: reading order, table
structure, and page geometry. Two things it gives that the per-page OCR service does not:

- `pages[].asset_metadata.rectified_image_width_pixels` / `..._height_pixels`, so the raster
  size arrives *in the response* rather than having to be passed in by the caller.
- `detected_page_number`, the number printed on the page, which is not the same as the index
  and is what a human reviewer is looking at when they say "page 3".

**Word-level granularity is not on by default.** The documentation is explicit: default output
reports lines, and `text_words` appears only when word granularity is requested. A response
without it is refused here rather than silently reconstructed from line text, because splitting
a line into words invents geometry per word, and invented geometry is a provenance record
pointing at a box nobody measured — claim 2 defeated by a helpful default.
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


class ResponseError(ValueError):
    """A response that does not match the documented schema.

    Raised rather than skipped, for the same reason as every other adapter here: a reading that
    is short by an unknown amount is indistinguishable downstream from a page with less text on
    it, and one of those is a bug while the other is a fact.
    """


def to_document(
    *,
    source_id: str,
    source_digest: str,
    response: dict[str, Any],
    language: str,
    service_version: str,
) -> ReadDocument:
    """One documented standard-output response, as a `ReadDocument`.

    No `page_sizes` argument, unlike the per-page OCR adapter: this service reports the
    rectified raster's pixel dimensions in `pages[].asset_metadata`, so the caller does not have
    to tell it, and there is no opportunity for the caller to tell it something different from
    what the service actually measured.
    """
    pages_in = response.get("pages")
    if not isinstance(pages_in, list) or not pages_in:
        raise ResponseError("the response has no `pages` list; this is not the documented shape")

    words_in = response.get("text_words")
    if not isinstance(words_in, list):
        raise ResponseError(
            "the response has no `text_words`, so word granularity was not enabled on the "
            "request. Reconstructing words by splitting line text would invent a box per word, "
            "and an invented box is a provenance record pointing somewhere nobody measured"
        )

    sizes = {index: size for index, size in (_page_size(page) for page in pages_in)}
    lines_in = response.get("text_lines")
    line_order = _line_order(lines_in) if isinstance(lines_in, list) else {}

    grouped: dict[int, dict[str, list[tuple[int, Word]]]] = {}
    for entry in words_in:
        index, line_id, order, word = _word(entry, sizes)
        grouped.setdefault(index, {}).setdefault(line_id, []).append((order, word))

    pages = []
    for index in sorted(sizes):
        by_line = grouped.get(index, {})
        # Lines in the service's own reading order where it gave one, then by the reading order
        # of their first word. Sorting by line id would be alphabetical over opaque strings,
        # which is an ordering that looks stable and means nothing.
        ordered = sorted(
            by_line.items(),
            key=lambda item: (line_order.get(item[0], 10**9), min(order for order, _ in item[1])),
        )
        lines = [
            build_line([word for _, word in sorted(members, key=lambda member: member[0])])
            for _, members in ordered
            if members
        ]
        if not lines:
            continue
        pages.append(
            Page(
                number=index + 1,
                size=sizes[index],
                lines=tuple(lines),
                language=language,
                # The documented output carries no detected language and no language
                # confidence. The caller's assertion is recorded at full confidence, and the
                # fact that it is an assertion rather than a detection is stated here — a
                # fabricated detection confidence would make the language routing in ADR-0004
                # look measured when it is declared.
                language_confidence=1.0,
            )
        )

    if not pages:
        raise ResponseError(
            "no page in this response carried any word. The request reported pages, so this is "
            "a response whose words did not map to them rather than an empty document"
        )

    return ReadDocument(
        source_id=source_id,
        source_digest=source_digest,
        # Named for what it does rather than for who sells it: the core must not be able to
        # branch on this string, and `manifest.gates.core_purity` refuses the vendor's name
        # inside `core/` in any case.
        reader=ReaderIdentity(name="managed-document-automation", version=service_version),
        pages=tuple(pages),
    )


def _page_size(page: dict[str, Any]) -> tuple[int, PageSize]:
    """The page index and the rectified raster's size, both from the response."""
    if not isinstance(page, dict) or "page_index" not in page:
        raise ResponseError("a `pages` entry has no `page_index`")
    index = int(page["page_index"])
    if index < 0:
        raise ResponseError(f"`page_index` is {index}; the documented index is zero-based")

    metadata = page.get("asset_metadata")
    if not isinstance(metadata, dict):
        raise ResponseError(
            f"page {index} has no `asset_metadata`, so the raster size is unknown. Defaulting "
            f"to a nominal page would bake a made-up size into every provenance record on it"
        )
    try:
        size = PageSize(
            width=int(metadata["rectified_image_width_pixels"]),
            height=int(metadata["rectified_image_height_pixels"]),
        )
    except (KeyError, TypeError, ValueError, DocumentError) as exc:
        raise ResponseError(f"page {index} has no usable rectified image size: {exc}") from exc
    return index, size


def _line_order(lines: list[Any]) -> dict[str, int]:
    order: dict[str, int] = {}
    for line in lines:
        if isinstance(line, dict) and "id" in line and "reading_order" in line:
            order[str(line["id"])] = int(line["reading_order"])
    return order


def _word(entry: Any, sizes: dict[int, PageSize]) -> tuple[int, str, int, Word]:
    if not isinstance(entry, dict):
        raise ResponseError("a `text_words` entry is not an object")

    locations = entry.get("locations")
    # The documentation shows `locations` as a single object on `text_words` and `text_lines`,
    # and as a *list* on elements. Both are accepted here rather than one being called correct:
    # the shape is the vendor's to change, and an adapter that refused the other form would fail
    # on a response the service is entitled to send.
    if isinstance(locations, list):
        locations = locations[0] if locations else None
    if not isinstance(locations, dict):
        raise ResponseError(f"word {entry.get('id')!r} has no `locations`")

    index = int(locations.get("page_index", entry.get("page_index", -1)))
    if index not in sizes:
        raise ResponseError(
            f"word {entry.get('id')!r} is on page index {index}, which is not among the pages "
            f"this response declared ({sorted(sizes)})"
        )

    box_in = locations.get("bounding_box")
    if not isinstance(box_in, dict):
        raise ResponseError(f"word {entry.get('id')!r} has no `bounding_box`")
    try:
        # Documented as fractions of the page with a top-left origin — the same convention as
        # `core.geometry.Box`, so this renames and does not convert.
        box = Box(
            left=float(box_in["left"]),
            top=float(box_in["top"]),
            width=float(box_in["width"]),
            height=float(box_in["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResponseError(f"word {entry.get('id')!r} has an incomplete bounding box") from exc

    try:
        word = Word(
            text=str(entry["text"]),
            # The whole point of this module. See the module docstring: there is no confidence
            # in the documented response, and inventing one here would clear every threshold in
            # the repository on every page this reader touches.
            confidence=None,
            box=box,
        )
    except (KeyError, DocumentError) as exc:
        raise ResponseError(f"word {entry.get('id')!r}: {exc}") from exc

    return (
        index,
        str(entry.get("line_id", entry.get("id", ""))),
        int(entry.get("reading_order", 0)),
        word,
    )
