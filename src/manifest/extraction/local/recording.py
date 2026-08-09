"""Reading and writing the engine recording.

ADR-0005, decision 19. The tier-0 reader is a binary whose confidences differ across versions
and platforms. If CI re-ran it, a threshold would move because a runner image was updated,
claim 1 would go red for reasons unrelated to this repository, and within a month somebody
would delete the check. So the reader runs on the author's machine, over the real corpus, and
its **normalised output** is committed here.

The recording is the unit of evidence. Every threshold in this repository is derived from it;
nothing derives one from a live run.

**Format: gzipped JSON Lines, one page per line, plus a manifest.** Line-oriented so that a
diff between two recordings is readable — an engine upgrade should produce a reviewable change,
not a single line that differs. Gzipped because 778 pages of word geometry is thirty-odd
megabytes of repetitive JSON and about a tenth of that compressed.

**The manifest is what makes it evidence rather than data.** It records the reader version, the
language data, the corpus fingerprint, and a digest of the pages themselves. A recording whose
corpus fingerprint does not match the committed ground truth is refused: a threshold derived
from a recording of a different corpus is the worst kind of green.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from manifest.core.document import Line, Page, ReadDocument, ReaderIdentity, Word
from manifest.core.geometry import Box, PageSize

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[4] / "recordings" / "ocr"
PAGES_FILE = "pages.jsonl.gz"
MANIFEST_FILE = "manifest.json"


class RecordingError(RuntimeError):
    """A recording that cannot support the thresholds derived from it."""


@dataclass(frozen=True, slots=True)
class RecordingManifest:
    """What this recording is of, and what read it."""

    reader_name: str
    reader_version: str
    language_data: tuple[str, ...]
    corpus_fingerprint: str
    corpus_seed: int
    pages: int
    words: int
    digest: str

    @property
    def reader(self) -> ReaderIdentity:
        return ReaderIdentity(name=self.reader_name, version=self.reader_version)


def _page_payload(document_id: str, shipment_id: str, page: Page) -> dict[str, Any]:
    return {
        "shipment": shipment_id,
        "document": document_id,
        "page": page.number,
        "width": page.size.width,
        "height": page.size.height,
        "language": page.language,
        "language_confidence": page.language_confidence,
        "lines": [
            {
                "confidence": round(line.confidence, 6),
                "words": [
                    {
                        "text": word.text,
                        "confidence": round(word.confidence, 6),
                        "box": [
                            round(word.box.left, 6),
                            round(word.box.top, 6),
                            round(word.box.width, 6),
                            round(word.box.height, 6),
                        ],
                    }
                    for word in line.words
                ],
            }
            for line in page.lines
        ],
    }


def _page_from_payload(payload: dict[str, Any]) -> Page:
    return Page(
        number=int(payload["page"]),
        size=PageSize(width=int(payload["width"]), height=int(payload["height"])),
        lines=tuple(
            Line(
                words=tuple(
                    Word(
                        text=word["text"],
                        confidence=float(word["confidence"]),
                        box=Box(*word["box"]),
                    )
                    for word in line["words"]
                ),
                confidence=float(line["confidence"]),
            )
            for line in payload["lines"]
        ),
        language=payload["language"],
        language_confidence=payload["language_confidence"],
    )


def write(
    directory: Path,
    readings: list[tuple[str, str, ReadDocument]],
    language_data: tuple[str, ...],
    corpus_fingerprint: str,
    corpus_seed: int,
) -> RecordingManifest:
    """Write a recording, returning its manifest.

    Pages are sorted by (shipment, document, page) before writing. A recording whose line order
    depended on how the work happened to be scheduled would produce a different digest on every
    run, and the whole point of the digest is that it does not.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payloads = sorted(
        (
            _page_payload(document_id, shipment_id, page)
            for shipment_id, document_id, reading in readings
            for page in reading.pages
        ),
        key=lambda entry: (entry["shipment"], entry["document"], entry["page"]),
    )

    body = "".join(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n" for payload in payloads
    ).encode("utf-8")
    # `mtime=0`: gzip stamps the current time into its header by default, so the same input
    # would produce different bytes on every run and the file would show as changed in every
    # diff. A recording that always looks modified is a recording nobody reviews.
    with gzip.GzipFile(directory / PAGES_FILE, "wb", mtime=0) as handle:
        handle.write(body)

    readers = {str(reading.reader) for _, _, reading in readings}
    if len(readers) != 1:
        raise RecordingError(
            f"a recording is of one reader; these pages came from {sorted(readers)}. Mixing "
            f"them would mean thresholds derived from a population nothing can be recalibrated "
            f"against"
        )
    reader = next(iter(reading.reader for _, _, reading in readings))

    manifest = RecordingManifest(
        reader_name=reader.name,
        reader_version=reader.version,
        language_data=tuple(sorted(language_data)),
        corpus_fingerprint=corpus_fingerprint,
        corpus_seed=corpus_seed,
        pages=len(payloads),
        words=sum(len(line["words"]) for page in payloads for line in page["lines"]),
        digest=hashlib.sha256(body).hexdigest(),
    )
    (directory / MANIFEST_FILE).write_text(
        json.dumps(asdict(manifest), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def read_manifest(directory: Path = DEFAULT_DIRECTORY) -> RecordingManifest:
    path = directory / MANIFEST_FILE
    if not path.exists():
        raise RecordingError(
            f"no recording at {path}. Every threshold in this repository is derived from the "
            f"committed recording, never from a live run — see ADR-0005. Run `make ocr-record`"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["language_data"] = tuple(payload["language_data"])
    return RecordingManifest(**payload)


def read_pages(directory: Path = DEFAULT_DIRECTORY) -> list[tuple[str, str, Page]]:
    """`(shipment, document, page)` for every recorded page, verified against the manifest."""
    manifest = read_manifest(directory)
    raw = (directory / PAGES_FILE).read_bytes()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
        body = handle.read()
    if hashlib.sha256(body).hexdigest() != manifest.digest:
        raise RecordingError(
            f"{directory / PAGES_FILE} does not match the digest in its manifest. Something "
            f"edited the recording without going through the ceremony, and every threshold "
            f"derived from it is derived from something nobody reviewed"
        )
    return [
        (payload["shipment"], payload["document"], _page_from_payload(payload))
        for payload in (json.loads(line) for line in body.decode("utf-8").splitlines() if line)
    ]
