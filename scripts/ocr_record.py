#!/usr/bin/env python3
"""Run the tier-0 reader over the corpus and record what it produced.

ADR-0005, decision 19. The reader is a binary whose confidences differ across versions and
platforms; every threshold in this repository is derived from the recording this writes, never
from a live run.

**Regenerating is a ceremony, not a command.** It is the one act that can move every number on
the scoreboard at once, so `--accept` is required to overwrite an existing recording, and the
run prints what changed — the reader version, the language data, the page and word counts, and
the movement of every derived threshold — before it will do it. An engine upgrade that improves
a field is good news; an engine upgrade that moves a threshold nobody looked at is claim 1
becoming decoration.

Pages are read in parallel because 778 of them take a quarter of an hour otherwise, and a
ceremony nobody has time to attend is a ceremony that gets skipped.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from manifest.core.document import ReadDocument, digest_bytes
from manifest.core.geometry import PageSize
from manifest.extraction.local import reader as local
from manifest.extraction.local.recording import (
    DEFAULT_DIRECTORY,
    RecordingError,
    RecordingManifest,
    read_manifest,
    write,
)

ROOT = Path(__file__).resolve().parents[1]
RENDERED = ROOT / "corpus" / "rendered"
GROUND_TRUTH = ROOT / "corpus" / "ground_truth" / "corpus.json"


@dataclass(frozen=True, slots=True)
class PageJob:
    shipment: str
    document: str
    page: int
    language: str
    path: Path


def _jobs(limit: int | None) -> list[PageJob]:
    if not GROUND_TRUTH.exists():
        raise SystemExit(f"{GROUND_TRUTH} does not exist; run `make corpus` first")
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    jobs: list[PageJob] = []
    for document in truth["documents"]:
        for page in range(1, int(document["pages"]) + 1):
            path = RENDERED / f"{document['shipment_id']}_{document['document_id']}_p{page}.jpg"
            if not path.exists():
                raise SystemExit(
                    f"{path} is missing. The recording is of the corpus, so a partial corpus "
                    f"would produce a recording that silently covers less than it claims. "
                    f"Run `make corpus`"
                )
            jobs.append(
                PageJob(
                    shipment=document["shipment_id"],
                    document=document["document_id"],
                    page=page,
                    language=document["language"],
                    path=path,
                )
            )
    jobs.sort(key=lambda job: (job.shipment, job.document, job.page))
    return jobs[:limit] if limit else jobs


def _read(job: PageJob):
    from PIL import Image  # noqa: PLC0415 — worker-local, keeps the parent import light

    with Image.open(job.path) as image:
        size = PageSize(width=image.width, height=image.height)
    page = local.read_page(job.path, job.page, size, job.language)
    return job, page


def _languages(jobs: list[PageJob]) -> set[str]:
    return {job.language for job in jobs}


def _describe(manifest: RecordingManifest) -> str:
    return (
        f"    reader   {manifest.reader_name}@{manifest.reader_version}\n"
        f"    corpus   seed {manifest.corpus_seed}, fingerprint "
        f"{manifest.corpus_fingerprint[:16]}\n"
        f"    volume   {manifest.pages} pages, {manifest.words} words\n"
        f"    digest   {manifest.digest[:16]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept", action="store_true", help="Overwrite the existing recording.")
    parser.add_argument("--workers", type=int, default=0, help="0 chooses by CPU count.")
    parser.add_argument("--limit", type=int, default=0, help="Read only the first N pages.")
    parser.add_argument(
        "--directory", type=Path, default=DEFAULT_DIRECTORY, help="Where the recording lives."
    )
    arguments = parser.parse_args()

    if not local.available():
        print(
            "the tier-0 reader is not on PATH. It is the only reader in this repository that "
            "runs, so claims 1 and 2 cannot be derived without it — see ADR-0005",
            file=sys.stderr,
        )
        return 1

    jobs = _jobs(arguments.limit or None)
    # Fails rather than skips. Dropping a language would quietly reduce the corpus to the ones
    # this machine supports, and every claim scored on it would keep reporting the same green.
    local.require_languages(_languages(jobs))

    existing: RecordingManifest | None = None
    with contextlib.suppress(RecordingError):
        existing = read_manifest(arguments.directory)

    if existing is not None and not arguments.accept:
        print("A recording already exists:\n")
        print(_describe(existing))
        print(
            "\nRegenerating it can move every threshold in this repository at once. Re-run with"
            "\n  make ocr-record ACCEPT=1"
            "\nand the run will print what changed before writing. See ADR-0005 and "
            "docs/DECISIONS.md 19."
        )
        return 1

    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    fingerprint = (ROOT / "corpus" / "ground_truth" / "fingerprint.txt").read_text().strip()
    version = local.version()

    print(f"reading {len(jobs)} pages with {version.binary}")
    results: dict[tuple[str, str], list] = {}
    workers = arguments.workers or None
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index, (job, page) in enumerate(pool.map(_read, jobs, chunksize=4), start=1):
            results.setdefault((job.shipment, job.document), []).append((job, page))
            if index % 50 == 0 or index == len(jobs):
                print(f"  {index}/{len(jobs)}")

    readings: list[tuple[str, str, ReadDocument]] = []
    for (shipment, document), entries in sorted(results.items()):
        entries.sort(key=lambda entry: entry[0].page)
        digest = digest_bytes(b"".join(entry[0].path.read_bytes() for entry in entries))
        readings.append(
            (
                shipment,
                document,
                ReadDocument(
                    source_id=f"{shipment}/{document}",
                    source_digest=digest,
                    reader=version.identity(),
                    pages=tuple(page for _, page in entries),
                ),
            )
        )

    manifest = write(
        directory=arguments.directory,
        readings=readings,
        language_data=tuple(sorted(_languages(jobs))),
        corpus_fingerprint=fingerprint,
        corpus_seed=int(truth["seed"]),
    )

    print("\nrecorded:")
    print(_describe(manifest))
    if existing is not None:
        print("\nwhat changed:")
        for name, before, after in (
            (
                "reader",
                f"{existing.reader_name}@{existing.reader_version}",
                f"{manifest.reader_name}@{manifest.reader_version}",
            ),
            ("pages", existing.pages, manifest.pages),
            ("words", existing.words, manifest.words),
            ("corpus", existing.corpus_fingerprint[:16], manifest.corpus_fingerprint[:16]),
        ):
            mark = "  " if str(before) == str(after) else "→ "
            print(f"  {mark}{name:8} {before}  ->  {after}")
        print(
            "\nThresholds are derived from this recording. Run `make thresholds` to see where "
            "they now sit, and read the movement before trusting any number on the scoreboard."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
