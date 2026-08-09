"""The tier-0 reader: a local OCR binary, and the adapter that normalises what it emits.

ADR-0005. This is the reader that actually runs, on the real degraded corpus, producing the
genuine confidences and genuine geometry claims 1 and 2 are derived from. Without it there is
no honest source for either number, and inventing them would be fabricating a result.

**It shells out to the binary and parses TSV.** No Python wrapper package: a wrapper is one
more dependency whose version can change the output, and turning a process's stdout into the
normalised representation is exactly the work that should be visible in this repository rather
than delegated to somebody else's.

**Two invocation modes, and the difference between them is load-bearing.** A full-page read
runs page segmentation, layout analysis and line finding, and resolves a word in the context of
its neighbours. A single-unit read of a crop does none of that. ADR-0003's Layer B is the
second mode checking the first, and they are genuinely different code paths inside the binary —
which is what makes their agreement mean something, and what stops the check being a
deterministic function replayed on a subset of its own input.

**What it cannot do**, said here rather than discovered: handwriting (effectively nothing), and
layout understanding beyond words and lines. Key–value association and table structure are
built over the normalised representation, which is the right place for them anyway, because
that is the logic claims 2 and 4 are about.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
from dataclasses import dataclass
from enum import IntEnum
from io import StringIO
from pathlib import Path
from typing import Final

from manifest.core.document import (
    DocumentError,
    Page,
    ReadDocument,
    ReaderIdentity,
    Word,
    build_line,
    digest_bytes,
)
from manifest.core.geometry import Box, PageSize

BINARY: Final = "tesseract"

#: The reader's confidence scale. It emits 0–100 with -1 for rows that are not words, so the
#: adapter divides — arithmetic, not calibration. ADR-0004: two readers' 0.8 are different
#: events, and this conversion does not pretend otherwise.
_CONFIDENCE_SCALE: Final = 100.0


#: Page segmentation modes. `FULL_PAGE` is what produces a reading; `SINGLE_LINE` is what
#: ADR-0003's Layer B re-reads a crop with, and the difference between them is the whole of
#: that layer's independence.
class Segmentation(IntEnum):
    FULL_PAGE = 3
    SINGLE_LINE = 7
    SINGLE_WORD = 8


#: Corpus language to the binary's traineddata name.
_LANGUAGES: Final[dict[str, str]] = {
    "en": "eng",
    "el": "ell",
    "nl": "nld",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "zh": "chi_sim",
    "ar": "ara",
}


class ReaderUnavailable(RuntimeError):
    """The binary or its language data is missing.

    Raised rather than skipped. A suite that quietly skips the only reader that runs is a suite
    reporting green for one thing less than it says — and the thing it stopped checking is
    claims 1 and 2.
    """


@dataclass(frozen=True, slots=True)
class ReaderVersion:
    """The binary's version and the language data available to it.

    Part of `ReaderIdentity`, and recorded in `recordings/ocr/`. A reader upgrade is a different
    reader for every purpose in this system: different calibration, different thresholds, and a
    record produced by one is not interchangeable with a record produced by the other.
    """

    binary: str
    languages: tuple[str, ...]

    def identity(self) -> ReaderIdentity:
        return ReaderIdentity(name="reference-ocr", version=self.binary)


def available() -> bool:
    return shutil.which(BINARY) is not None


def version() -> ReaderVersion:
    if not available():
        raise ReaderUnavailable(
            f"`{BINARY}` is not on PATH. It is the only reader in this repository that runs, so "
            f"claims 1 and 2 cannot be derived without it. See ADR-0005"
        )
    reported = _run([BINARY, "--version"])
    first = reported.splitlines()[0].strip()
    listed = _run([BINARY, "--list-langs"])
    languages = tuple(
        line.strip() for line in listed.splitlines()[1:] if line.strip() and " " not in line.strip()
    )
    return ReaderVersion(binary=first, languages=languages)


def require_languages(wanted: set[str]) -> None:
    """Fail — not skip — when a language the corpus contains has no data.

    A skip here silently reduces the corpus to whatever the machine happens to support, and
    every claim scored on it with it. ADR-0004's finding is that Greek and Dutch have no managed
    reader; a run that quietly dropped them would leave that finding unexercised while reporting
    the same green.
    """
    installed = set(version().languages)
    missing = {
        code: _LANGUAGES[code] for code in wanted if _LANGUAGES.get(code, code) not in installed
    }
    if missing:
        raise ReaderUnavailable(
            f"the corpus contains {sorted(missing)} and the reader has no data for "
            f"{sorted(missing.values())}. This is a failure rather than a skip: dropping a "
            f"language would quietly reduce the corpus to the ones this machine supports, and "
            f"every claim scored on it would keep reporting the same green"
        )


def read_page(
    image_path: Path,
    page_number: int,
    page_size: PageSize,
    language: str,
    segmentation: Segmentation = Segmentation.FULL_PAGE,
) -> Page:
    """One page, as the reader saw it, in the normalised representation."""
    trained = _LANGUAGES.get(language, language)
    tsv = _run(
        [
            BINARY,
            str(image_path),
            "stdout",
            "-l",
            trained,
            "--psm",
            str(int(segmentation)),
            "tsv",
        ]
    )
    lines = _lines_from_tsv(tsv, page_size)
    return Page(
        number=page_number,
        size=page_size,
        lines=tuple(lines),
        language=language,
        # The binary does not detect language; it is *told* one. So the confidence recorded is
        # the confidence of the caller's assertion, which is 1.0 when a corpus knows what it
        # generated and is something else entirely in production. Recording it as 1.0 here and
        # nowhere else is the honest version: the routing contract reads this field, and a
        # fabricated detection confidence would make claim 4's language routing look measured.
        language_confidence=1.0,
    )


def read_crop(
    image_path: Path,
    language: str,
    segmentation: Segmentation = Segmentation.SINGLE_LINE,
) -> tuple[str, float]:
    """Re-read one crop through the single-line path. ADR-0003, Layer B.

    Returns the text and the lowest word confidence in it. The lowest, not the mean: a crop
    whose value is nine confident characters and one uncertain one is an uncertain read, and
    averaging is how the uncertain character disappears.
    """
    trained = _LANGUAGES.get(language, language)
    tsv = _run(
        [
            BINARY,
            str(image_path),
            "stdout",
            "-l",
            trained,
            "--psm",
            str(int(Segmentation.SINGLE_LINE)),
            "tsv",
        ]
    )
    words = [
        (row["text"], float(row["conf"]))
        for row in _rows(tsv)
        if row["text"].strip() and float(row["conf"]) >= 0
    ]
    if not words:
        return "", 0.0
    return " ".join(text.strip() for text, _ in words), min(
        conf for _, conf in words
    ) / _CONFIDENCE_SCALE


def _rows(tsv: str) -> list[dict[str, str]]:
    # `QUOTE_NONE`: the binary emits a bare `"` as a word, and csv's default quoting would
    # swallow the rest of the line with it. A quote character in a scanned invoice is ordinary.
    reader = csv.DictReader(StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [row for row in reader if row.get("text") is not None]


def _lines_from_tsv(tsv: str, page_size: PageSize):
    """Group the reader's word rows into lines, in reading order.

    The binary reports a hierarchy — block, paragraph, line, word — and the line key is the
    tuple of the first three. Grouping by anything less merges two columns of a form into one
    line, which turns a labelled value into a sentence and loses the association a field
    depends on.
    """
    grouped: dict[tuple[int, int, int, int], list[Word]] = {}
    order: list[tuple[int, int, int, int]] = []

    for row in _rows(tsv):
        text = row["text"].strip()
        confidence = float(row["conf"])
        if not text or confidence < 0:
            continue
        try:
            box = Box.from_pixels(
                left=float(row["left"]),
                top=float(row["top"]),
                width=float(row["width"]),
                height=float(row["height"]),
                page=page_size,
            )
        except (ValueError, DocumentError):
            # A row whose geometry does not fit the page it claims to be on is dropped rather
            # than clamped. Clamping would put a provenance record at the page edge, which is
            # a location that looks plausible and is not where anything is.
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]), 0)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            Word(text=text, confidence=min(confidence / _CONFIDENCE_SCALE, 1.0), box=box)
        )

    return [build_line(grouped[key]) for key in order if grouped[key]]


def read_document(
    source_id: str,
    pages: list[tuple[Path, int, PageSize, str]],
) -> ReadDocument:
    """A whole document, page by page.

    The source digest is over the **page images**, in order, which is what was actually read.
    Digesting a PDF that was never opened would claim provenance over bytes nothing looked at.
    """
    digest_input = b"".join(path.read_bytes() for path, _, _, _ in pages)
    return ReadDocument(
        source_id=source_id,
        source_digest=digest_bytes(digest_input),
        reader=version().identity(),
        pages=tuple(
            read_page(path, number, size, language) for path, number, size, language in pages
        ),
    )


def _run(command: list[str]) -> str:
    if not available():
        raise ReaderUnavailable(f"`{BINARY}` is not on PATH; see ADR-0005")
    completed = subprocess.run(  # noqa: S603 — fixed command list, no shell, paths from callers
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReaderUnavailable(f"{' '.join(command[:3])} failed: {completed.stderr.strip()[:400]}")
    return completed.stdout
