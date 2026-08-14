#!/usr/bin/env python3
"""Read the corpus once with Amazon Textract, and record what it produced.

**Why this exists, and it is the one thing that changes what this system can publish.** Thirty
of forty fields are *quality-limited* at tier 0: real errors survive at high confidence, which is
over-confidence rather than thin evidence, and no threshold fixes it. The cascade exists for
exactly those pages — and tier 1 may not publish, **not because it reports no confidence but
because no threshold here is derived from the confidences it does report**. So escalation today
rescues a value for a human and cannot raise the publish rate by one field.

This is the missing half. Textract reads the same 3,255 labelled pages the tier-0 recording
covers, its per-word confidences are committed under `recordings/textract/`, and the derivation
that already exists runs over them: for each field, the lowest Textract score whose 95% upper
bound on the published-and-wrong rate still fits that field's declared error budget.

**It bills, and it is the first thing in this repository that does.** 3,255 pages against a
published, dated unit price — the arithmetic is printed before anything is called, and nothing is
called without `--record`. A ceremony, exactly like `make ocr-record`, for the same reason: this
is the one act that can move every number on the scoreboard, and it is not allowed to be quiet.

    python3 scripts/textract_record.py                  # print the cost, call nothing
    python3 scripts/textract_record.py --record         # read the corpus, write the recording

**What it must not become.** A recording made from a partial corpus is a recording that silently
covers less than it claims, so a missing page refuses rather than skips — the rule `ocr_record.py`
already follows. And the corpus fingerprint is written into the manifest, so a recording made
against a corpus that has since been regenerated announces itself instead of being compared
against a different set of pages.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RENDERED = ROOT / "corpus" / "rendered"
GROUND_TRUTH = ROOT / "corpus" / "ground_truth" / "corpus.json"
DIRECTORY = ROOT / "recordings" / "textract"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Published, cited, dated — the same figure `evals/scale/` prices tier 1 with, and it is quoted
#: from one place for the same reason every other number here is: two copies disagree eventually.
#: `docs/AWS-CONSTRAINTS.md` records that the pricing page states this for US West (Oregon) and
#: states no Frankfurt rate on the same page, so the estimate below is a floor, not a quote.
PER_PAGE_USD = Decimal("0.0015")
PRICE_SOURCE = "Amazon Textract, DetectDocumentText, US West (Oregon), first 1M pages/month"
PRICE_READ_ON = "2026-08-09"

#: Textract's own identity in the recording. The estate looks a threshold up by the reader that
#: produced the value, so this string is a key rather than a label: it must be what
#: `handlers/escalate.py` stamps on a tier-1 reading, or the artefact will be looked up under a
#: name nothing wrote.
READER_NAME = "textract"

#: **Found by being refused, not by reading.** Eight workers produced
#: `ProvisionedThroughputExceededException ... reached max retries: 4` within seconds, and the
#: quota is nowhere on the page `docs/AWS-CONSTRAINTS.md` cites: that page documents *set* quotas
#: — formats, sizes, page counts — and the synchronous transaction rate is an account-level one it
#: does not list. Recorded there now, because the next person will look on the same page.
#:
#: Two at a time, with adaptive retry underneath. A recording is a one-off that runs for a few
#: minutes; there is nothing to gain by pushing a rate limit and a whole recording to lose.
WORKERS = 2

#: Textract's own page numbering for a single-image request. Always 1, because it numbers what it
#: was handed rather than what the document is.
TEXTRACT_PAGE = 1


@dataclass(frozen=True, slots=True)
class PageJob:
    shipment: str
    document: str
    page: int
    language: str
    path: Path


def _jobs(limit: int | None) -> list[PageJob]:
    """Every page of the committed corpus, refusing rather than skipping a missing one."""
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
                    f"would produce a recording that silently covers less than it claims"
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


def _eligible(jobs: list[PageJob]) -> list[PageJob]:
    """Only the languages the routing contract lets tier 1 read.

    **Textract publishes six input languages and Greek and Dutch are in neither list.** Sending a
    Greek page to it does not fail: it returns confident-looking text over a language the model
    never saw, and that score would then enter a threshold derivation as evidence. Recording it
    would be manufacturing calibration data out of a service reading an alphabet it does not have.

    So the contract decides, not this script — the same contract `handlers/escalate.py` routes by.
    """
    import yaml  # noqa: PLC0415

    routing = yaml.safe_load((ROOT / "contracts/cascade/routing.yaml").read_text(encoding="utf-8"))
    allowed = {
        str(entry["language"])
        for entry in routing["languages"]
        if 1 in [int(tier) for tier in entry["eligible_tiers"]]
    }
    return [job for job in jobs if job.language in allowed]


def _read(job: PageJob):  # pragma: no cover - one billed call per page
    import boto3  # noqa: PLC0415 — worker-local, and the offline suite imports this without AWS
    from PIL import Image  # noqa: PLC0415

    from manifest.core.document import digest_bytes  # noqa: PLC0415
    from manifest.core.geometry import PageSize  # noqa: PLC0415
    from manifest.extraction.aws.textract import to_document  # noqa: PLC0415

    with Image.open(job.path) as image:
        size = PageSize(width=image.width, height=image.height)
    body = job.path.read_bytes()

    # `adaptive` rather than the default: it slows the client down when the service says it is
    # being pushed, which is the behaviour a bulk recording wants. `standard` would retry the
    # same rate four times and give up, which is exactly what happened.
    from botocore.config import Config  # noqa: PLC0415

    client = boto3.client(
        "textract", config=Config(retries={"max_attempts": 10, "mode": "adaptive"})
    )
    response = client.detect_document_text(Document={"Bytes": body})
    # **Textract numbers what it was given, not what the document is.** One image in means
    # `Page 1` out, every time — so page 2 of a bill of lading comes back as page 1 and the
    # adapter refuses it against a size keyed by 2: *"a block on page 1 and no size was given"*.
    # The service is right and the caller was wrong; the document's own numbering is restored
    # below, where the page is the only thing that knows which page it is.
    reading = to_document(
        source_id=f"{job.shipment}_{job.document}",
        source_digest=digest_bytes(body),
        response=response,
        page_sizes={TEXTRACT_PAGE: size},
        language=job.language,
        # The service has no version to ask for. Dated instead, which is the honest identity for
        # a managed engine: *what Textract was on this day*, and a recording made later is a
        # different reader whether or not anybody announced a change.
        service_version=f"detect-document-text@{PRICE_READ_ON}",
    )
    return job, replace(
        reading, pages=tuple(replace(page, number=job.page) for page in reading.pages)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="Actually call Textract. It bills.")
    parser.add_argument("--limit", type=int, default=None, help="Fewer pages, for a smoke run.")
    arguments = parser.parse_args(argv)

    jobs = _eligible(_jobs(arguments.limit))
    pages = len(jobs)
    modelled = (PER_PAGE_USD * pages).quantize(Decimal("0.01"))

    print(
        f"\n  {pages:,} page(s) of the corpus are eligible for tier 1 by "
        f"contracts/cascade/routing.yaml\n"
        f"  {DIM}{PER_PAGE_USD} USD/page x {pages:,} = {modelled} USD, modelled{RESET}\n"
        f"  {DIM}{PRICE_SOURCE}, read {PRICE_READ_ON}{RESET}\n"
        f"  {DIM}Modelled, not quoted: the pricing page states no Frankfurt rate beside this "
        f"one, and the estate is in Frankfurt. Treat it as a floor.{RESET}\n"
    )
    if not arguments.record:
        print(
            f"  {DIM}Nothing called. Re-run with --record once the figure above is accepted.{RESET}"
        )
        return 0

    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    readings = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for done, (job, reading) in enumerate(pool.map(_read, jobs), start=1):
            readings.append((job.shipment, job.document, reading))
            if done % 250 == 0:
                print(f"  {DIM}{done:,}/{pages:,}{RESET}")

    from manifest.extraction.local.recording import write  # noqa: PLC0415

    manifest = write(
        DIRECTORY,
        readings,
        language_data=tuple(sorted({job.language for job in jobs})),
        corpus_fingerprint=(ROOT / "corpus/ground_truth/fingerprint.txt")
        .read_text(encoding="utf-8")
        .strip(),
        corpus_seed=int(truth["seed"]),
    )
    print(
        f"\n  {GREEN}ok{RESET}    {manifest.pages:,} page(s), {manifest.words:,} word(s) recorded\n"
        f"    reader   {manifest.reader_name}@{manifest.reader_version}\n"
        f"    digest   {manifest.digest[:16]}\n"
        f"    cost     {modelled} USD, modelled — and this is the first billed read this "
        f"repository has paid for\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
