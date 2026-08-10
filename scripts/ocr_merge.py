#!/usr/bin/env python3
"""Combine the sharded ceremony's recordings into one.

**Why the ceremony is sharded.** Reading 3,255 degraded pages at 300 DPI takes a GitHub runner
longer than a job is allowed to live. Two dispatches proved it: one died at a 90-minute timeout
and one at five hours, and a job timeout is reported as `cancelled` — a word that reads like
somebody pressed a button. Splitting the corpus across parallel jobs turns the ceremony from
something that does not finish into something that takes forty minutes.

**The merge must be invisible afterwards.** A sharded recording and an unsharded one have to be
the same bytes, or the shard count is baked into the digest and every change to it looks like
the reader moved. `manifest.extraction.local.recording.merge` sorts the page lines back into the
canonical order and re-writes them through the same serialisation, so the only thing sharding
changes is how long it took.

What this script adds on top is the part a merge cannot infer: the corpus fingerprint and seed
it is *supposed* to be of. They come from the committed ground truth, and a shard that disagrees
is refused rather than absorbed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from manifest.extraction.local.recording import DEFAULT_DIRECTORY, RecordingError, merge

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "corpus" / "ground_truth" / "corpus.json"
FINGERPRINT = ROOT / "corpus" / "ground_truth" / "fingerprint.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shards", nargs="+", type=Path, help="Each shard's recording directory.")
    parser.add_argument("--into", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument(
        "--expect-pages",
        type=int,
        default=0,
        help="Refuse a merge that does not have this many pages. 0 reads it from the corpus.",
    )
    arguments = parser.parse_args()

    if not GROUND_TRUTH.exists():
        print(f"{GROUND_TRUTH} does not exist; run `make corpus` first", file=sys.stderr)
        return 1
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    expected = arguments.expect_pages or sum(int(d["pages"]) for d in truth["documents"])

    languages = tuple(sorted({str(document["language"]) for document in truth["documents"]}))

    try:
        manifest = merge(
            shards=list(arguments.shards),
            into=arguments.into,
            language_data=languages,
            corpus_fingerprint=FINGERPRINT.read_text(encoding="utf-8").strip(),
            corpus_seed=int(truth["seed"]),
        )
    except RecordingError as error:
        print(f"merge refused: {error}", file=sys.stderr)
        return 1

    # **The check that makes sharding safe to trust.** A shard that failed, or a matrix entry
    # that silently produced nothing, would otherwise merge into a recording that is short by a
    # few hundred pages — and nothing downstream would notice, because every figure would simply
    # be derived from fewer observations while printing its N as though that were the corpus.
    # Claim 1's whole argument is N-on-the-face-of-the-report; a quietly smaller N is that
    # argument reporting green about a set nobody chose.
    if manifest.pages != expected:
        print(
            f"the merged recording has {manifest.pages} pages and the corpus has {expected}. A "
            f"shard is missing or short.\n\n"
            f"This is refused rather than reported because the failure is invisible downstream: "
            f"every threshold would be derived from fewer observations and would print its N as "
            f"though that N were the corpus.",
            file=sys.stderr,
        )
        return 1

    print(
        f"merged {len(arguments.shards)} shard(s) into {arguments.into}\n"
        f"    reader   {manifest.reader_name}@{manifest.reader_version}\n"
        f"    corpus   seed {manifest.corpus_seed}, fingerprint "
        f"{manifest.corpus_fingerprint[:16]}\n"
        f"    volume   {manifest.pages} pages, {manifest.words} words\n"
        f"    digest   {manifest.digest[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
