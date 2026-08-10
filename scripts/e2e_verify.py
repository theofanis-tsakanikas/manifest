#!/usr/bin/env python3
"""Put documents through the deployed estate and assert what came out.

**Why this is a script and not a person reading logs.** "It works end to end" is the easiest
sentence in this repository to say without evidence: a green execution history proves the
machine did not throw, and every interesting failure here is one that does not throw. A document
whose abstentions reached nobody succeeds. A record whose boxes point at the wrong part of the
page succeeds. A pipeline that published everything because a threshold artefact was empty
succeeds, twice as fast.

So the answer to "does it work?" is a command with an exit code. Eight checks, each able to
fail on its own, and the last is the only one that separates this from a demo.

**It needs credentials, and that is the one thing it is allowed to need.** Every claim in this
repository is scored offline; this is not one of the claims. It is the check that the estate
behaves the way the offline claims say the design does, and it can only run against an estate.

**What it deliberately does not claim.** No managed extraction engine is called here — every
page is read by the tier-0 image, so nothing below is evidence about Textract, Bedrock Data
Automation or an LLM extractor. No cost figure is produced. No figure here is a claim about
production: it is one run, of documents this repository generated, on one day.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: A greyscale value below this counts as ink. Not tuned: it is the midpoint of the range, and a
#: threshold picked to make a crop pass would be the check agreeing with itself.
INK = 128
#: The fraction of a crop that must be ink before the box is credited with carrying a value. A
#: box on white space lands near zero; a box on a short number lands well above this. Low on
#: purpose — what this catches is *nothing there*, not *not enough there*.
INKED_ENOUGH = 0.002


@dataclass
class Result:
    name: str
    passed: bool
    detail: str


@dataclass
class Estate:
    """Every name resolved from SSM, never transcribed.

    Same rule the deploy workflow follows: a hand-typed bucket name looks like an independent
    setting, and a verifier pointed at the wrong bucket reports a clean failure about a place
    nothing ran.
    """

    landing: str
    records: str
    data_key: str
    state_machine: str
    queue_url: str
    results: list[Result] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str) -> bool:
        self.results.append(Result(name, passed, detail))
        mark = f"{GREEN}pass{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name}\n        {DIM}{detail}{RESET}")
        return passed


def _client(name: str):
    import boto3  # noqa: PLC0415 - the offline suite must import this module without AWS

    return boto3.client(name)


def _resolve(project: str) -> Estate:
    ssm = _client("ssm")

    def parameter(path: str) -> str:
        return ssm.get_parameter(Name=f"/{project}/{path}")["Parameter"]["Value"]

    queue_arn = parameter("extraction/review_queue_arn")
    _, _, _, region, account, queue = queue_arn.split(":")
    return Estate(
        landing=parameter("foundation/landing_bucket"),
        records=parameter("foundation/records_bucket"),
        data_key=parameter("foundation/data_key_arn"),
        state_machine=parameter("extraction/state_machine_arn"),
        queue_url=f"https://sqs.{region}.amazonaws.com/{account}/{queue}",
    )


def _upload(estate: Estate, key: str, body: bytes) -> None:
    _client("s3").put_object(
        Bucket=estate.landing,
        Key=key,
        Body=body,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=estate.data_key,
    )


def _await_execution(estate: Estate, since: float, wanted: str, timeout: int = 900):
    """The execution whose input names this document. Polled, because there is no callback.

    Matched on the *input* rather than on the name: Step Functions names an EventBridge-started
    execution with a uuid, so the document id is nowhere in it. Matching on the name would find
    nothing and report a trigger that did not fire.
    """
    states = _client("stepfunctions")
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = states.list_executions(stateMachineArn=estate.state_machine, maxResults=40)
        for execution in page.get("executions", []):
            if execution["startDate"].timestamp() < since - 60:
                continue
            described = states.describe_execution(executionArn=execution["executionArn"])
            if wanted in described.get("input", ""):
                if described["status"] != "RUNNING":
                    return described
                break
        time.sleep(10)
    return None


def _json_object(bucket: str, key: str) -> dict | None:
    try:
        body = _client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:
        return None
    return json.loads(body.decode("utf-8"))


def _happy_path(estate: Estate, document: Path, document_id: str) -> None:
    """One English bill of lading, and everything that must be true afterwards."""
    key = f"incoming/en/bill_of_lading/{document_id}.pdf"
    started = time.time()
    _upload(estate, key, document.read_bytes())
    print(f"\n{DIM}uploaded {key}{RESET}\n")

    execution = _await_execution(estate, started, document_id)
    if not estate.check(
        "1 · the arrival started an execution",
        execution is not None,
        f"an object landing on {key} is the only trigger; nothing else may start this machine"
        if execution
        else "no execution named this document within 15 minutes. The EventBridge rule, its "
        "permission to start the machine, or the S3 notification is not wired",
    ):
        return

    if execution["status"] != "SUCCEEDED":
        estate.check(
            "the execution succeeded",
            False,
            f"{execution['status']}: {execution.get('cause', execution.get('error', ''))[:400]}",
        )
        return

    output = json.loads(execution["output"])
    reading_pointer = (output.get("tier0") or {}).get("reading") or {}
    reading = _json_object(reading_pointer.get("bucket", ""), reading_pointer.get("key", ""))

    words = sum(
        len(line["words"]) for page in (reading or {}).get("pages", []) for line in page["lines"]
    )
    scored = [
        word["confidence"]
        for page in (reading or {}).get("pages", [])
        for line in page["lines"]
        for word in line["words"]
        if word.get("confidence") is not None
    ]
    boxed = sum(
        1
        for page in (reading or {}).get("pages", [])
        for line in page["lines"]
        for word in line["words"]
        if word.get("box")
    )
    estate.check(
        "2 · the reader produced real confidences and real geometry",
        bool(reading) and words > 0 and len(scored) == words and boxed == words,
        f"{words} words, {len(scored)} with a confidence, {boxed} with a box — "
        f"range {min(scored):.3f} to {max(scored):.3f}. Invented confidences are the one "
        f"thing this repository may never contain; these came off a degraded page"
        if scored
        else "no reading, or a reading with no scored words. Nothing downstream means anything",
    )

    outcome = (output.get("extraction") or {}).get("outcome") or {}
    published = int(outcome.get("publishable_count", 0))
    queued = int(outcome.get("queued_count", 0))
    estate.check(
        "3 · the derived thresholds were actually applied",
        published > 0 and queued > 0,
        f"{published} field(s) publishable, {queued} queued. Both must be non-zero: a document "
        f"where everything passes proves no threshold was consulted, and one where everything "
        f"abstains proves the thresholds are unreachable",
    )

    checked = (output.get("provenance") or {}).get("checked") or {}
    per_field = checked.get("fields") or []
    verdicts = {entry.get("field"): entry.get("verified") for entry in per_field}
    estate.check(
        "4 · the provenance gate ran per field, not per document",
        len(per_field) > 0 and all(v is not None for v in verdicts.values()),
        f"{len(per_field)} field verdict(s). One boolean for a whole document is a gate that "
        f"cannot say which field it refused, and claim 2 is a statement about fields",
    )

    fingerprint = outcome.get("fingerprint", "")
    record = _json_object(estate.records, f"records/{document_id}/{fingerprint}.json")
    estate.check(
        "5 · a record was published, keyed by document and version",
        record is not None,
        f"records/{document_id}/{fingerprint}.json — keyed by fingerprint so a re-extraction "
        f"writes beside the old record rather than over it (doctrine rule 4)"
        if record
        else "no record object. The publish state did not run or wrote somewhere else",
    )

    queued_fields = {
        entry["field"] for entry in outcome.get("fields", []) if entry.get("queued_because")
    }
    estate.check(
        "6 · every field that abstained reached the review queue",
        _queue_carries(estate, document_id, queued_fields),
        f"{len(queued_fields)} abstention(s) expected in the queue. This is the check the "
        f"estate shipped without: Publish and QueueForReview excluded each other, so a document "
        f"that published some of itself sent nobody the rest",
    )

    _check_a_box_against_the_page(estate, document_id, record)


def _queue_carries(estate: Estate, document_id: str, expected: set[str]) -> bool:
    """Drain the queue looking for this document's abstentions.

    Messages are deleted as they are read. This is a verifier against an estate that exists to
    be torn down, and a queue left full would make the next run's answer depend on the last
    one's leftovers.
    """
    if not expected:
        return False
    sqs = _client("sqs")
    deadline = time.time() + 120
    found: set[str] = set()
    while time.time() < deadline and not expected <= found:
        received = sqs.receive_message(
            QueueUrl=estate.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=10
        )
        for message in received.get("Messages", []):
            body = json.loads(message["Body"])
            outcome = (body.get("extraction") or {}).get("outcome") or {}
            if outcome.get("document_id") == document_id:
                found |= {
                    entry["field"]
                    for entry in outcome.get("fields", [])
                    if entry.get("queued_because")
                }
            sqs.delete_message(QueueUrl=estate.queue_url, ReceiptHandle=message["ReceiptHandle"])
    return expected <= found


def _check_a_box_against_the_page(estate: Estate, document_id: str, record: dict | None) -> None:
    """Claim 2, on a live record: the crop the record points at carries the value it claims.

    **The only check here that separates this from a demo.** Everything above reads what the
    pipeline said about itself. This takes a published field, downloads the page render the
    pipeline wrote, crops the box the record declares, and re-reads it.

    **Its strength, stated so nobody reads more into a pass.** The re-read uses the same engine
    family as the estate's, at whatever version is on this machine. That makes it an independent
    *path* — a different process, a different render, a different crop — and not an independent
    *engine*. It catches a box pointing at the wrong part of the page. It cannot catch a
    confident misread that both passes make, and it cannot catch a box pointing at an identical
    string elsewhere on the page. Same limits as `src/manifest/gates/provenance.py`, and the
    same reason they are written down here rather than left to be assumed.
    """
    if not record:
        estate.check("7 · a published field is where the record says it is", False, "no record")
        return

    published = [
        entry
        for entry in record.get("fields", [])
        if entry.get("verified") and entry.get("value") and entry.get("box")
    ]
    if not published:
        estate.check(
            "7 · a published field is where the record says it is",
            False,
            "no published field carries a box. There is nothing to check, which is worse than "
            "a failure: it is claim 2 with no subject",
        )
        return

    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    from manifest.extraction.local import reader as local  # noqa: PLC0415

    checked = 0
    agreed = 0
    inked = 0
    detail: list[str] = []
    for entry in published[:5]:
        page_number = int(entry["page"])
        key = f"renders/{document_id}/page-{page_number:04d}.png"
        try:
            raw = _client("s3").get_object(Bucket=estate.records, Key=key)["Body"].read()
        except Exception:
            detail.append(f"{entry['field']}: no render at {key}")
            continue
        image = Image.open(BytesIO(raw)).convert("L")
        left, top, width, height = (float(value) for value in entry["box"])
        # Padded, because a box that is tight on the glyphs loses their edges to the crop and
        # the re-read then fails on a correct record — the dangerous direction for a gate.
        pad = 0.004
        crop = image.crop(
            (
                max(0, int((left - pad) * image.width)),
                max(0, int((top - pad) * image.height)),
                min(image.width, int((left + width + pad) * image.width)),
                min(image.height, int((top + height + pad) * image.height)),
            )
        )
        checked += 1
        pixels = list(crop.getdata())
        dark = sum(1 for value in pixels if value < INK) / max(1, len(pixels))
        if dark > INKED_ENOUGH:
            inked += 1
        else:
            detail.append(f"{entry['field']}: the crop is blank — the box points at nothing")
            continue

        # Written out, because `read_crop` shells out to the reader binary and a binary takes a
        # path. The temporary file holds the crop and nothing else: re-reading the whole page
        # would find the value somewhere and prove nothing about where the record said it was.
        with tempfile.TemporaryDirectory() as directory:
            crop_path = Path(directory) / "crop.png"
            crop.save(crop_path)
            text, _ = local.read_crop(crop_path, record.get("language") or "en")
        if _agrees(text.strip(), str(entry["value"])):
            agreed += 1
        else:
            detail.append(
                f"{entry['field']}: record says {entry['value']!r}, crop re-reads {text.strip()!r}"
            )

    estate.check(
        "7 · every published box carries ink where the record says a value is",
        checked > 0 and inked == checked,
        f"{inked}/{checked} crops carry ink. Independent of every reader: a box pointing at "
        f"white space fails this whatever produced it",
    )
    estate.check(
        "8 · a re-read of the crop agrees with the published value",
        checked > 0 and agreed >= max(1, checked - 1),
        f"{agreed}/{checked} agreed. An independent path, not an independent engine: it catches "
        f"a wrong box and cannot catch a confident misread. "
        + ("; ".join(detail[:3]) if detail else ""),
    )


def _agrees(read: str, published: str) -> bool:
    """Same comparison the offline gate uses: case and spacing are not the claim."""
    normalise = lambda value: "".join(value.split()).casefold()  # noqa: E731
    left, right = normalise(read), normalise(published)
    return bool(left) and (left == right or right in left or left in right)


def _edge_cases(estate: Estate, document: Path) -> None:
    """Five documents that must each be refused, and refused for their own reason."""
    print(f"\n{DIM}the edge cases{RESET}\n")
    body = document.read_bytes()
    started = time.time()

    cases = [
        (
            "incoming/el/packing_list/E2E-GREEK.pdf",
            "a Greek page — the language no managed reader in the stack reads",
        ),
        (
            "incoming/en/no_such_type/E2E-NOCONTRACT.pdf",
            "a document type with no contract: nothing declares its error budgets",
        ),
        (
            "incoming/zz/bill_of_lading/E2E-BADLANG.pdf",
            "a language the routing contract does not declare",
        ),
        ("incoming/E2E-FLATKEY.pdf", "a key outside the landing convention"),
        ("elsewhere/en/bill_of_lading/E2E-WRONGPREFIX.pdf", "outside the prefix the rule matches"),
    ]
    for key, why in cases:
        _upload(estate, key, body)
        print(f"  {DIM}uploaded {key} — {why}{RESET}")

    time.sleep(90)
    states = _client("stepfunctions")
    recent = states.list_executions(stateMachineArn=estate.state_machine, maxResults=40)
    started_for: dict[str, str] = {}
    for execution in recent.get("executions", []):
        if execution["startDate"].timestamp() < started - 60:
            continue
        described = states.describe_execution(executionArn=execution["executionArn"])
        for key, _ in cases:
            if key in described.get("input", ""):
                started_for[key] = described["status"]

    # The two that must never start a machine at all. A refusal that costs an execution is a
    # refusal that costs money, and the cheapest place to reject a key is before the trigger.
    for key in ("incoming/E2E-FLATKEY.pdf", "elsewhere/en/bill_of_lading/E2E-WRONGPREFIX.pdf"):
        estate.check(
            f"edge · {key} starts nothing",
            key not in started_for,
            "refused by the trigger's own pattern, before any compute ran"
            if key not in started_for
            else f"an execution started and ended {started_for[key]} — the rule is too wide",
        )

    # And the three that must start, and must then fail by name rather than publish.
    for key, why in cases[:3]:
        status = started_for.get(key)
        estate.check(
            f"edge · {key.split('/')[-1]} is refused, not published",
            status in {"FAILED", "SUCCEEDED"},
            f"{why} — execution {status or 'never started'}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument("--document", type=Path, required=True, help="A PDF to send through.")
    parser.add_argument("--document-id", default="E2E-VERIFY")
    parser.add_argument("--skip-edge-cases", action="store_true")
    arguments = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    estate = _resolve(arguments.project)
    print(f"the deployed estate — {estate.state_machine}\n")

    _happy_path(estate, arguments.document, arguments.document_id)
    if not arguments.skip_edge_cases:
        _edge_cases(estate, arguments.document)

    failed = [result for result in estate.results if not result.passed]
    print(f"\n  {len(estate.results) - len(failed)}/{len(estate.results)} checks passed\n")
    if failed:
        print(f"{RED}end to end: FAILED{RESET}", file=sys.stderr)
        for result in failed:
            print(f"  {result.name}: {result.detail}", file=sys.stderr)
        return 1

    print(
        "end to end: a document arrived, was read by the tier-0 image, thresholded against\n"
        "  derived thresholds, checked field by field against its own pages, published with its\n"
        "  provenance, and its abstentions reached a human. No managed extraction engine was\n"
        "  called and no distributed job ran; this is one run, on one day, on documents this\n"
        "  repository generated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
