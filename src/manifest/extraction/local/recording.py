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


def merge(
    shards: list[Path],
    into: Path,
    language_data: tuple[str, ...],
    corpus_fingerprint: str,
    corpus_seed: int,
) -> RecordingManifest:
    """Combine per-shard recordings into one, byte-identical to an unsharded run.

    **Why the ceremony is sharded at all.** Reading 3,255 degraded pages at 300 DPI takes a
    GitHub runner longer than the six hours a job is allowed — two dispatches died at their
    timeout, one at 90 minutes and one at five hours, and a job timeout is reported as
    `cancelled`, which reads like somebody pressed a button. Splitting the corpus across
    parallel jobs turns an afternoon into forty minutes and, more usefully, into a ceremony that
    finishes.

    **Merged at the line level, deliberately.** Each shard writes the same gzipped JSON Lines
    this module already produces, and this concatenates and re-sorts the lines rather than
    reconstructing documents from them. Sorting is what makes the result independent of how the
    work was divided: the same pages in the same order produce the same bytes and the same
    digest, so a sharded run and an unsharded one are indistinguishable afterwards. A merge that
    preserved shard order would put the shard count into the digest, and every change to it
    would look like the reader moved.

    Every shard must have been read by the same reader and be of the same corpus. Two shards
    that disagree are not a merge problem to reconcile — they are two recordings, and thresholds
    derived from their union would describe a population that never existed.
    """
    if not shards:
        raise RecordingError("no shards to merge; a recording of nothing has no thresholds in it")

    manifests = [read_manifest(shard) for shard in shards]
    readers = {(entry.reader_name, entry.reader_version) for entry in manifests}
    if len(readers) != 1:
        raise RecordingError(
            f"the shards were read by {sorted(readers)}. A recording is of one reader: two "
            f"readers' 0.8 are different events, and thresholds derived from the union would "
            f"describe a population nothing can be recalibrated against"
        )
    fingerprints = {entry.corpus_fingerprint for entry in manifests}
    if fingerprints != {corpus_fingerprint}:
        raise RecordingError(
            f"the shards are of corpora {sorted(fingerprints)} and the merge was told "
            f"{corpus_fingerprint!r}. A recording of a corpus that drifted is the worst kind of "
            f"green: every figure derived from it is about documents nobody has"
        )

    payloads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for shard in shards:
        for entry in _payloads(shard):
            key = (entry["shipment"], entry["document"], entry["page"])
            if key in seen:
                raise RecordingError(
                    f"page {key} appears in more than one shard. Overlapping shards would count "
                    f"the same observation twice, and an error budget is a rate — doubling its "
                    f"denominator with copies of pages the reader already got right makes every "
                    f"threshold look better than it is"
                )
            seen.add(key)
            payloads.append(entry)

    payloads.sort(key=lambda entry: (entry["shipment"], entry["document"], entry["page"]))
    body = "".join(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n" for payload in payloads
    ).encode("utf-8")

    into.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(into / PAGES_FILE, "wb", mtime=0) as handle:
        handle.write(body)

    name, version = next(iter(readers))
    manifest = RecordingManifest(
        reader_name=name,
        reader_version=version,
        language_data=tuple(sorted(language_data)),
        corpus_fingerprint=corpus_fingerprint,
        corpus_seed=corpus_seed,
        pages=len(payloads),
        words=sum(len(line["words"]) for page in payloads for line in page["lines"]),
        digest=hashlib.sha256(body).hexdigest(),
    )
    (into / MANIFEST_FILE).write_text(
        json.dumps(asdict(manifest), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _payloads(directory: Path) -> list[dict[str, Any]]:
    """The raw page payloads, digest-checked. Deliberately not `read_pages`.

    `read_pages` parses each payload into a `Page`, which is what a consumer wants and exactly
    what a merge must not do: re-serialising a parsed object would let a float round-trip or a
    key order change the bytes, and the digest of a sharded recording would then differ from an
    unsharded one for reasons nobody could see in a diff.
    """
    manifest = read_manifest(directory)
    with gzip.GzipFile(fileobj=io.BytesIO((directory / PAGES_FILE).read_bytes())) as handle:
        body = handle.read()
    if hashlib.sha256(body).hexdigest() != manifest.digest:
        raise RecordingError(
            f"{directory / PAGES_FILE} does not match the digest in its manifest. Something "
            f"edited a shard between writing and merging"
        )
    return [json.loads(line) for line in body.decode("utf-8").splitlines() if line]
