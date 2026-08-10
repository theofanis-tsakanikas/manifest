"""A sharded recording and an unsharded one are the same recording.

The ceremony reads 3,255 degraded pages, which takes a runner longer than a job may live — two
dispatches died at their timeout. It is sharded now, and sharding is only safe if it leaves no
trace: the same pages read in eight pieces must produce the same bytes and the same digest as
the same pages read in one.

If they differed, the shard count would be inside the digest. Changing it — from eight to
sixteen, because a runner got slower — would show up as every threshold's evidence having
moved, and nobody could tell that apart from the reader having changed. That is the failure this
file exists to prevent, and it is why the merge sorts the page lines back into canonical order
rather than concatenating them in whatever order the jobs finished.
"""

from __future__ import annotations

import gzip

import pytest

from manifest.core.document import Line, Page, ReadDocument, ReaderIdentity, Word
from manifest.core.geometry import Box, PageSize
from manifest.extraction.local.recording import (
    MANIFEST_FILE,
    PAGES_FILE,
    RecordingError,
    merge,
    read_manifest,
    write,
)

READER = ReaderIdentity(name="reference-ocr", version="tesseract 5.5.0")
FINGERPRINT = "0" * 64
SEED = 1234


def _page(number: int, text: str) -> Page:
    return Page(
        number=number,
        size=PageSize(width=1000, height=1400),
        lines=(
            Line(
                words=(
                    Word(text=text, confidence=0.91, box=Box(0.1, 0.2, 0.3, 0.05)),
                    Word(text=f"{text}-2", confidence=0.77, box=Box(0.5, 0.2, 0.2, 0.05)),
                ),
                confidence=0.84,
            ),
        ),
        language="en",
        language_confidence=0.95,
    )


def _reading(shipment: str, document: str, pages: tuple[Page, ...]) -> ReadDocument:
    return ReadDocument(
        source_id=f"{shipment}/{document}",
        source_digest="a" * 64,
        reader=READER,
        pages=pages,
    )


def _corpus() -> list[tuple[str, str, ReadDocument]]:
    """Two shipments, two documents each, several pages — enough that order can go wrong."""
    return [
        (
            shipment,
            document,
            _reading(
                shipment,
                document,
                tuple(_page(n, f"{shipment}-{document}-{n}") for n in range(1, pages + 1)),
            ),
        )
        for shipment, document, pages in (
            ("SHP1", "bill_of_lading", 3),
            ("SHP1", "commercial_invoice", 2),
            ("SHP2", "bill_of_lading", 1),
            ("SHP2", "packing_list", 4),
        )
    ]


def _write_shard(directory, readings):
    return write(
        directory=directory,
        readings=readings,
        language_data=("en",),
        corpus_fingerprint=FINGERPRINT,
        corpus_seed=SEED,
    )


def test_merging_shards_reproduces_the_unsharded_bytes(tmp_path):
    """The property the whole sharding rests on."""
    corpus = _corpus()

    whole = tmp_path / "whole"
    unsharded = _write_shard(whole, corpus)

    # Split so that one shipment's pages land in both shards — the arrangement most likely to
    # expose an ordering bug, and the one striding actually produces.
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_shard(left, [corpus[0], corpus[2]])
    _write_shard(right, [corpus[1], corpus[3]])

    merged_into = tmp_path / "merged"
    merged = merge(
        shards=[right, left],  # deliberately out of order; the merge sorts, the caller does not
        into=merged_into,
        language_data=("en",),
        corpus_fingerprint=FINGERPRINT,
        corpus_seed=SEED,
    )

    assert merged.digest == unsharded.digest
    assert merged.pages == unsharded.pages
    assert merged.words == unsharded.words
    assert (merged_into / PAGES_FILE).read_bytes() == (whole / PAGES_FILE).read_bytes()
    assert (merged_into / MANIFEST_FILE).read_text() == (whole / MANIFEST_FILE).read_text()

    # And the merged file is still readable as what it claims to be.
    assert read_manifest(merged_into) == unsharded


def test_a_page_in_two_shards_is_refused(tmp_path):
    """Overlap would count an observation twice, and an error budget is a rate.

    Doubling a denominator with copies of pages the reader already got right makes every
    threshold look better than it is — and it would look like more evidence, not less.
    """
    corpus = _corpus()
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_shard(left, [corpus[0], corpus[1]])
    _write_shard(right, [corpus[1], corpus[2]])

    with pytest.raises(RecordingError, match="more than one shard"):
        merge(
            shards=[left, right],
            into=tmp_path / "merged",
            language_data=("en",),
            corpus_fingerprint=FINGERPRINT,
            corpus_seed=SEED,
        )


def test_shards_from_two_readers_are_refused(tmp_path):
    """A recording is of one reader. Two readers' 0.8 are different events."""
    corpus = _corpus()
    left = tmp_path / "left"
    _write_shard(left, [corpus[0]])

    other = tmp_path / "other"
    elsewhere = ReaderIdentity(name="reference-ocr", version="tesseract 5.5.2")
    shipment, document, reading = corpus[1]
    _write_shard(
        other,
        [
            (
                shipment,
                document,
                ReadDocument(
                    source_id=reading.source_id,
                    source_digest=reading.source_digest,
                    reader=elsewhere,
                    pages=reading.pages,
                ),
            )
        ],
    )

    with pytest.raises(RecordingError, match="one reader"):
        merge(
            shards=[left, other],
            into=tmp_path / "merged",
            language_data=("en",),
            corpus_fingerprint=FINGERPRINT,
            corpus_seed=SEED,
        )


def test_a_shard_of_a_different_corpus_is_refused(tmp_path):
    """A recording of a corpus that drifted is the worst kind of green."""
    corpus = _corpus()
    left = tmp_path / "left"
    _write_shard(left, [corpus[0]])

    drifted = tmp_path / "drifted"
    write(
        directory=drifted,
        readings=[corpus[1]],
        language_data=("en",),
        corpus_fingerprint="f" * 64,
        corpus_seed=SEED,
    )

    with pytest.raises(RecordingError, match="corpora"):
        merge(
            shards=[left, drifted],
            into=tmp_path / "merged",
            language_data=("en",),
            corpus_fingerprint=FINGERPRINT,
            corpus_seed=SEED,
        )


def test_an_edited_shard_is_refused(tmp_path):
    """The digest is checked per shard, before anything is merged."""
    corpus = _corpus()
    left = tmp_path / "left"
    _write_shard(left, [corpus[0]])

    with gzip.GzipFile(left / PAGES_FILE, "wb", mtime=0) as handle:
        handle.write(b'{"shipment": "SHP1", "document": "edited", "page": 1, "lines": []}\n')

    with pytest.raises(RecordingError, match="digest"):
        merge(
            shards=[left],
            into=tmp_path / "merged",
            language_data=("en",),
            corpus_fingerprint=FINGERPRINT,
            corpus_seed=SEED,
        )
