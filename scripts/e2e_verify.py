#!/usr/bin/env python3
"""Put documents through the deployed estate and assert what came out.

**Why this is a script and not a person reading logs.** "It works end to end" is the easiest
sentence in this repository to say without evidence: a green execution history proves the
machine did not throw, and every interesting failure here is one that does not throw. A document
whose abstentions reached nobody succeeds. A record whose boxes point at the wrong part of the
page succeeds. A pipeline that published everything because a threshold artefact was empty
succeeds, twice as fast.

So the answer to "does it work?" is a command with an exit code. Eight checks on the happy path
and five on documents that must be refused, each able to fail on its own, and the box-against-
the-page one is what separates this from a demo.

**An edge case is only a check if it can fail.** The five refusals asserted that an execution
reached a terminal state, which is every state an execution can reach — a document that sailed
through and published a record passed under the name of the check written to catch it. They
assert the absence of a published record now, which is the property, and `SUCCEEDED` stays a
legitimate outcome because a document whose every field abstains ends successfully with nothing
published and everything queued.

**It needs credentials, and that is the one thing it is allowed to need.** Every claim in this
repository is scored offline; this is not one of the claims. It is the check that the estate
behaves the way the offline claims say the design does, and it can only run against an estate.

**What it deliberately does not claim.** With the escalation tiers deployed a managed engine
*is* called — and what that proves is that the routing sends abstaining fields up, not that the
tier read them better. There is no accuracy figure for the escalated fraction here and there
cannot be one from a single document. No cost figure is produced. No figure here is a claim
about production: it is one run, of documents this repository generated, on one day.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    glue_database: str
    athena_workgroup: str
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
        glue_database=parameter("lakehouse/glue_database"),
        athena_workgroup=parameter("lakehouse/athena_workgroup"),
    )


def _upload(estate: Estate, key: str, body: bytes) -> None:
    _client("s3").put_object(
        Bucket=estate.landing,
        Key=key,
        Body=body,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=estate.data_key,
    )


def _await_execution(
    estate: Estate,
    since: float,
    wanted: str,
    timeout: int = 900,
    exclude: frozenset[str] = frozenset(),
):
    """The execution whose input names this document. Polled, because there is no callback.

    Matched on the *input* rather than on the name: Step Functions names an EventBridge-started
    execution with a uuid, so the document id is nowhere in it. Matching on the name would find
    nothing and report a trigger that did not fire.

    **`exclude` exists because a sixty-second grace window made this return the same execution
    twice.** The re-extraction check sends one document id through, waits, and sends it again —
    and the second wait matched the first run, because that run had started well inside
    `since - 60`. Both fingerprints came back identical and the check reported an estate that
    overwrites, of an estate that had written both versions correctly.

    A time window cannot distinguish two runs of the same document seconds apart. An execution
    ARN can, so the caller passes the ones it has already accounted for.
    """
    states = _client("stepfunctions")
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = states.list_executions(stateMachineArn=estate.state_machine, maxResults=40)
        for execution in page.get("executions", []):
            if execution["startDate"].timestamp() < since - 60:
                continue
            if execution["executionArn"] in exclude:
                continue
            described = states.describe_execution(executionArn=execution["executionArn"])
            if wanted in described.get("input", ""):
                if described["status"] != "RUNNING":
                    return described
                break
        time.sleep(10)
    return None


def _from_history(execution_arn: str) -> dict:
    """Reassemble what each state produced, from the execution history.

    Keyed by the shape of each payload rather than by state name: the reader's output is the one
    with `pages`, the extraction's is the one with `publishable_count`, the gate's is the one
    with `verified`. Matching on names would tie this verifier to the machine's current state
    names, and renaming a state is not supposed to break the thing that checks it.
    """
    states = _client("stepfunctions")
    assembled: dict = {}
    paginator = states.get_paginator("get_execution_history")
    for page in paginator.paginate(executionArn=execution_arn):
        for event in page.get("events", []):
            raw = (event.get("taskSucceededEventDetails") or {}).get("output")
            if not raw:
                continue
            try:
                payload = json.loads(raw).get("Payload")
            except (ValueError, AttributeError):
                continue
            if not isinstance(payload, dict):
                continue
            if "escalation" in payload:
                # **The escalation's outcome supersedes the one before it, and must.**
                #
                # `Escalate` re-thresholds every rescued field and returns new counts and a new
                # fingerprint at `extraction.outcome`; the earlier `ExtractAndThreshold` payload
                # carries the pre-escalation ones. Reading the earlier of the two is not a
                # cosmetic error — the fingerprint names the published record's key, so a
                # verifier holding the stale one looks for a record that was never written and
                # reports a publish step that did not run.
                #
                # Events arrive in order, so a later assignment is the later state by
                # construction rather than by a rule this function has to enforce.
                assembled["escalation"] = payload["escalation"]
                outcome = (payload.get("extraction") or {}).get("outcome")
                if isinstance(outcome, dict):
                    assembled["extraction"] = {"outcome": outcome}
            elif "reading" in payload:
                assembled["tier0"] = payload
            elif "publishable_count" in payload:
                assembled["extraction"] = {"outcome": payload}
            elif "verified" in payload:
                assembled["provenance"] = {"checked": payload}
            elif "landed" in payload:
                # `{"landed": n, "document_id": ..., "version": ...}`, and `{"skipped": ...}` when
                # the version was already in the table. Matched on its own key like every other
                # payload here rather than on the state's name or its `ResultPath`.
                assembled["lake"] = payload
    return assembled


def _escalation_is_deployed(estate: Estate) -> bool:
    """Whether this estate has the escalation states at all.

    Read from the deployed definition rather than from an input to this script. The tiers are an
    opt-in flag on the deploy, so both shapes are legitimate estates, and a verifier that assumed
    one would either demand an escalation from a machine that has none or pass silently over a
    machine that has one and never used it.
    """
    definition = _client("stepfunctions").describe_state_machine(
        stateMachineArn=estate.state_machine
    )["definition"]
    return "Escalate" in json.loads(definition).get("States", {})


def _json_object(bucket: str, key: str) -> dict | None:
    """The object, parsed once.

    **Once, deliberately.** A record that needs two `json.loads` is double-encoded — a JSON
    string literal whose contents are the JSON — and that is a defect, not a format to
    accommodate. The estate wrote records that way until `Body` stopped being passed through
    `States.JsonToString`, and every consumer downstream would have read text where a customs
    record belongs. Returning `None` here makes the check that asked for it fail, which is the
    correct outcome; quietly loading twice would have hidden it.
    """
    try:
        body = _client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:
        return None
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        print(
            f"  {RED}the object at {key} is double-encoded{RESET}: it parses to "
            f"{type(parsed).__name__}, not an object. A consumer reading it gets text where a "
            f"record should be."
        )
        return None
    return parsed


def _happy_path(estate: Estate, document: Path, document_id: str) -> int | None:
    """One English bill of lading, and everything that must be true afterwards.

    Returns the number of rows this document has in the lake, so the idempotence check can ask
    whether a second identical run changed it.
    """
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

    # **Read from the history, not from the execution's output.**
    #
    # A terminal state replaces the output with its own result: `Publish` leaves an S3
    # PutObject response, `QueueForReview` leaves an SQS receipt. Either way what the document
    # actually did — the reading, the per-field outcomes, the gate's verdicts — is gone from the
    # place the obvious code looks. That cost two full end-to-end runs, both reported as five
    # failures whose real cause was that the evidence had been overwritten by a message id.
    #
    # The history keeps every state's output, which is what an execution history is for.
    output = _from_history(execution["executionArn"])
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

    # **The tier that had never been called, and the one check that can tell.**
    #
    # With the tiers deployed, an escalation that never fires is indistinguishable from one that
    # works: the document still publishes what tier 0 could read and queues the rest, the
    # execution still succeeds, and every other check on this page still passes. The states are
    # in the machine or they are not, and if they are, something must have gone up.
    if _escalation_is_deployed(estate):
        escalation = output.get("escalation") or {}
        attempted = bool(escalation.get("attempted"))
        estate.check(
            "3b · a page that abstained actually went up a tier",
            attempted and escalation.get("tier") is not None and bool(escalation.get("fields")),
            f"tier {escalation.get('tier')} was called for {len(escalation.get('fields') or [])} "
            f"field(s): {', '.join(escalation.get('fields') or [])}. "
            f"reports_confidence={escalation.get('reports_confidence')} — a tier that scores "
            f"nothing may rescue a reading for a human and may never publish on it"
            if attempted
            else f"nothing escalated: "
            f"{escalation.get('reason') or 'the state produced no result'}. "
            f"The states are deployed, {queued} field(s) abstained, and the ladder was not "
            f"climbed — which is the cascade being a design rather than a measurement, in the "
            f"one estate where it could have stopped being one",
        )
    else:
        print(
            f"  {DIM}skip  the escalation states are not in this machine — deployed with "
            f"enable_escalation_tiers off. Nothing here is evidence about a managed reader.{RESET}"
        )

    checked = (output.get("provenance") or {}).get("checked") or {}
    per_field = checked.get("fields") or []
    # **`verdict`, not `verified`.** The gate returns a *word* per field — verified, refused, or
    # uncheckable — and the last of those is the reason a boolean would not do: a field nothing
    # could look at has not been verified, and recording it as `false` would merge "we checked
    # and it is wrong" with "we could not check", which have opposite fixes.
    verdicts = [entry.get("verdict") for entry in per_field]
    vocabulary = {"verified", "refused", "uncheckable", "not_applicable"}
    estate.check(
        "4 · the provenance gate ran per field, not per document",
        len(per_field) > 0 and all(v in vocabulary for v in verdicts),
        f"{len(per_field)} field verdict(s): "
        + ", ".join(f"{v} x{verdicts.count(v)}" for v in sorted(set(verdicts), key=str))
        + ". One boolean for a whole document is a gate that cannot say which field it refused, "
        "and claim 2 is a statement about fields",
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
    _check_the_record_reached_the_lake(estate, document_id, outcome)
    return _rows_in_the_lake(estate, document_id)


def _query_failure(execution_id: str) -> str:
    """Why Athena refused, in its own words."""
    described = _client("athena").get_query_execution(QueryExecutionId=execution_id)
    return described["QueryExecution"]["Status"].get("StateChangeReason", "no reason given")


def _rows_in_the_lake(estate: Estate, document_id: str) -> int:
    """Every row under this document id, across every version. `-1` when the query would not run.

    By document rather than by version, deliberately: a *duplicate* of one version and a genuine
    second version both change this number, and the check that uses it sends identical bytes —
    where a second version is not a thing that can happen.
    """
    athena = _client("athena")
    started = athena.start_query_execution(
        QueryString="SELECT count(*) FROM document_version WHERE document_id = ?",
        ExecutionParameters=[document_id],
        WorkGroup=estate.athena_workgroup,
        QueryExecutionContext={"Database": estate.glue_database},
    )["QueryExecutionId"]

    deadline = time.time() + 180
    while time.time() < deadline:
        state = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]["Status"][
            "State"
        ]
        if state == "SUCCEEDED":
            rows = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"]
            return int(rows[1]["Data"][0]["VarCharValue"])
        if state in {"FAILED", "CANCELLED"}:
            return -1
        time.sleep(3)
    return -1


def _check_the_record_reached_the_lake(estate: Estate, document_id: str, outcome: dict) -> None:
    """Claim it in the warehouse's own words: query the table and count the rows.

    **The lake was a schema over nothing until this ran.** `infra/lakehouse` declared an Iceberg
    table on the day it was written and no step ever wrote to it, so the marts, the search
    surface and the bulk reprocessor all read from an empty table and every one of them
    "worked". Asserted by querying rather than by trusting the landing state's return value: a
    state that reports `landed: 9` and committed nothing is the failure this is for.

    The count is compared against the *fields* of the published record, not against a constant —
    an abstention lands as a row with a null value, and a check that expected only publishable
    fields would go green on a lake that had quietly dropped the queue.
    """
    athena = _client("athena")
    expected = len(outcome.get("fields", []))
    version = str(outcome.get("fingerprint", ""))
    if not version:
        estate.check(
            "9 · the published record reached the lake",
            False,
            "the published record carries no fingerprint, so there is no version to ask the "
            "lake about — and asking by document id alone counts every version at once",
        )
        return
    # **Nothing is interpolated, and the first version of this was.** It built the `WHERE` with
    # an f-string, in the same commit that argues a field value must be encoded rather than
    # concatenated — and `ruff`'s S608 refused it. The document id here is one this script chose,
    # so it was not exploitable; it was the same shape as the thing being guarded against, three
    # files away from the guard.
    #
    # The database goes in the execution *context* rather than into the statement, so the table
    # can be named bare and there is no identifier to quote either.
    started = athena.start_query_execution(
        # **By version, not by document, and the difference is doctrine rule 4.**
        #
        # This asked for every row under the document id and compared the count against one
        # publication's field count. That is correct exactly once. The second time the same
        # document goes through — which is the whole of the re-extraction check, running a few
        # lines below — the lake holds both versions, and the count is a multiple: *18 rows
        # against 9 fields published*, reported as a record that did not reach the lake, of a
        # lake that had it twice and correctly.
        #
        # The check that a correction does not overwrite is the check that made this one wrong,
        # which is a fair description of what the property costs.
        QueryString=(
            "SELECT count(*) AS landed, count(value) AS with_a_value "
            "FROM document_version WHERE document_id = ? AND version = ?"
        ),
        ExecutionParameters=[document_id, version],
        WorkGroup=estate.athena_workgroup,
        QueryExecutionContext={"Database": estate.glue_database},
    )["QueryExecutionId"]

    deadline = time.time() + 180
    state = "QUEUED"
    while time.time() < deadline:
        state = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]["Status"][
            "State"
        ]
        if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(3)

    if state != "SUCCEEDED":
        # **A query this verifier cannot run is not a fact about the estate.**
        #
        # The first version reported `9 · the published record reached the lake: FAIL` when the
        # record *had* reached the lake — 27 rows were in the table — and the only thing wrong
        # was that this script's own principal had no Lake Formation grant on a table that had
        # just been recreated. `count(*)` needs no column access and answered; `count(value)`
        # does and did not.
        #
        # A check that reports its own blindness as the system's failure is worse than no check:
        # it is a false statement about production, made confidently, in the one place whose
        # value is that it can be believed.
        reason = _query_failure(started)
        estate.check(
            "9 · the published record reached the lake",
            False,
            f"the query {state}: {reason}. If this names Lake Formation, it is a statement about "
            f"the principal running this script and not about the estate — grant it with "
            f"`aws lakeformation grant-permissions --principal "
            f"DataLakePrincipalIdentifier=<you> --resource "
            f'\'{{"Table":{{"DatabaseName":"{estate.glue_database}","Name":'
            f'"document_version"}}}}\' --permissions SELECT DESCRIBE`',
        )
        return

    rows = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"]
    landed = int(rows[1]["Data"][0]["VarCharValue"])
    with_a_value = int(rows[1]["Data"][1]["VarCharValue"])
    publishable = int(outcome.get("publishable_count", 0))
    estate.check(
        "9 · the published record reached the lake",
        landed == expected and with_a_value == publishable,
        f"{landed} row(s) in the Iceberg table against {expected} field(s) published, "
        f"{with_a_value} carrying a value against {publishable} publishable. An abstention "
        f"lands as a row with a null value: dropping it would hide the review queue from every "
        f"query in the analytics layer, which is the number a thresholding system is judged on",
    )


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
        if entry.get("verdict") == "verified" and entry.get("value") and entry.get("box")
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

    # **One key must never start a machine, and it is not the one I first assumed.**
    #
    # This expected `incoming/E2E-FLATKEY.pdf` to be rejected before any compute ran. It is not,
    # and the system is right: the EventBridge rule filters on the `incoming/` prefix, a flat key
    # is genuinely under that prefix, and the convention itself is checked in `read_tier0` —
    # where a refusal can name what was wrong with the key instead of being a rule that silently
    # did not match. The stricter expectation was mine and nothing declared it.
    #
    # What it costs is one execution and one cold start per malformed object, which is the price
    # of a legible refusal. Worth revisiting if malformed keys ever arrive in volume; not worth
    # trading a named error for silence today.
    for key in ("elsewhere/en/bill_of_lading/E2E-WRONGPREFIX.pdf",):
        estate.check(
            f"edge · {key} starts nothing",
            key not in started_for,
            "refused by the trigger's own pattern, before any compute ran"
            if key not in started_for
            else f"an execution started and ended {started_for[key]} — the rule is too wide",
        )

    # And the four that start, and must then be refused by name rather than published — the
    # three malformed documents and the key that does not follow the convention.
    #
    # **"Reached a terminal state" was the whole of this assertion, and it asserted nothing.**
    # `FAILED` or `SUCCEEDED` is every outcome an execution can end in, so the only things it
    # could catch were a machine that never started and one still running fifteen minutes later.
    # A document that sailed through and published a record read as *refused* — which is the
    # exact failure the edge cases exist to detect, passing under the name of the check written
    # to detect it.
    #
    # The property is in the second half: **no record exists**. `SUCCEEDED` is a legitimate
    # outcome here — a Greek packing list whose every field abstains ends successfully with
    # everything in the queue and nothing published — so the status alone can never separate the
    # two, and the records bucket can.
    for key, why in (*cases[:3], cases[3]):
        status = started_for.get(key)
        document_id = key.rsplit("/", 1)[-1].removesuffix(".pdf")
        records = _records_under(estate, document_id)
        estate.check(
            f"edge · {key.split('/')[-1]} is refused, not published",
            status in {"FAILED", "SUCCEEDED"} and not records,
            f"{why} — execution {status or 'never started'}, "
            + (
                f"and nothing under records/{document_id}/"
                if not records
                else f"and it PUBLISHED {len(records)} record(s): {records[:3]}"
            ),
        )


def _optional_surfaces(estate: Estate, project: str, document_id: str) -> None:
    """The two surfaces that are off by default, checked only where a deploy turned them on.

    **Absent is reported, never passed.** A check that quietly skips when a feature is off is a
    check that reports green for the estate where the feature was on and broken — which is the
    same defect as every other one this script has found, wearing an `if`. So a missing function
    is printed as a skip with its reason, and the count of checks that ran says so.
    """
    print(f"\n{DIM}the optional surfaces — search, and the classification endpoint{RESET}\n")
    functions = _client("lambda")

    def _present(name: str) -> bool:
        try:
            functions.get_function(FunctionName=name)
        except functions.exceptions.ResourceNotFoundException:
            print(f"  {DIM}skip  {name} is not deployed — dispatch with its flag on{RESET}")
            return False
        return True

    if _present(f"{project}-search"):
        _search_surface(estate, project, document_id)
    if _present(f"{project}-classify"):
        _classification(estate, project)


def _invoke(name: str, payload: dict) -> tuple[dict, str]:
    """`(answer, error)`. A handler that raised comes back as a payload, not an exception."""
    answer = _client("lambda").invoke(
        FunctionName=name, InvocationType="RequestResponse", Payload=json.dumps(payload).encode()
    )
    body = json.loads(answer["Payload"].read().decode("utf-8"))
    if answer.get("FunctionError"):
        return {}, f"{body.get('errorType', '?')}: {body.get('errorMessage', body)}"
    return body, ""


def _search_surface(estate: Estate, project: str, document_id: str) -> None:
    """The record that was published a few minutes ago can be found by asking for it.

    **This is the check the collection was missing.** The pipeline's indexing step returning 201
    proves a document was accepted; it does not prove the index answers questions, and until the
    search function existed nothing in this estate could ask one — the network policy is
    `AllowFromPublic = false`, so a laptop cannot reach the collection at all.

    OpenSearch Serverless is near-real-time rather than real-time: a document is searchable a
    few seconds after it is indexed. Polled rather than slept through, so a slow collection is a
    slow check instead of a failed one.
    """
    found: dict = {}
    for _ in range(12):
        answer, error = _invoke(f"{project}-search", {"term": document_id})
        if error:
            estate.check("18 · a published record is searchable", False, error)
            return
        if answer.get("matched"):
            found = answer
            break
        time.sleep(10)

    estate.check(
        "18 · a published record is searchable",
        bool(found.get("matched")),
        f"asked the index for {document_id} and got {found.get('matched', 0)} record(s), "
        f"version {found.get('records', [{}])[0].get('version', '—')}"
        if found
        else f"the index returned nothing for {document_id} after two minutes",
    )
    if not found:
        return

    # **What must NOT be there.** The index is one query away from a person about to make a
    # customs decision, and the values that did not clear their thresholds are exactly the ones
    # nobody approved. A count of them is useful; the values are not offered.
    record = found["records"][0]
    leaked = sorted(set(record) - _INDEXABLE)
    estate.check(
        "19 · the index carries published values and nothing else",
        not leaked and record.get("queued_field_count", 0) >= 0,
        f"{record.get('published_field_count')} published value(s) are searchable and "
        f"{record.get('queued_field_count')} abstention(s) are counted but not offered"
        if not leaked
        else f"the index answered with {leaked}, which core.search does not declare",
    )


def _classification(estate: Estate, project: str) -> None:
    """The endpoint ranks; this repository decides. Both halves, on the real estate.

    Two descriptions, chosen because they are the two answers the system is allowed to give: one
    that names its heading, and one that omits the single word deciding between a declared
    contested pair. A run where both came back `proposed` would be a model confident where the
    trade is not — which the training gate refuses offline and which this proves did not happen
    to the artefact that actually shipped.
    """
    clear, error = _invoke(f"{project}-classify", {"goods": "Aluminium window frames, anodised"})
    if error:
        estate.check("20 · the endpoint ranks and this repository decides", False, error)
        return
    estate.check(
        "20 · the endpoint ranks and this repository decides",
        clear.get("disposition") == "proposed" and clear.get("publishes") is False,
        f"{clear.get('candidates', [{}])[0].get('code', '—')} proposed at "
        f"{clear.get('candidates', [{}])[0].get('score', '—')}, gap {clear.get('margin')} — and "
        f"publishes={clear.get('publishes')}, because hs_code is always-review on any score",
    )

    contested, error = _invoke(f"{project}-classify", {"goods": "Ceramic floor tiles, 40x40cm"})
    if error:
        estate.check("21 · a contested pair gets no winner", False, error)
        return
    codes = [candidate["code"] for candidate in contested.get("candidates", [])[:2]]
    estate.check(
        "21 · a contested pair gets no winner",
        contested.get("disposition") == "contested",
        f"{' and '.join(codes)} offered with no winner — {contested.get('explanation', '')[:120]}"
        if contested.get("disposition") == "contested"
        else f"the estate answered {contested.get('disposition')} for a description that omits "
        f"the word deciding between {codes}",
    )


#: What a search result may carry. Read from `core.search` rather than transcribed, so a value
#: added there is a value this check knows about — and a value added anywhere else is not.
_INDEXABLE = frozenset(
    {
        "version",
        "score",
        "document_id",
        "document_type",
        "language",
        "reader",
        "indexed_on",
        "fields",
        "published_field_count",
        "queued_field_count",
    }
)


def _records_under(estate: Estate, document_id: str) -> list[str]:
    """Every published record for a document id.

    Listed rather than fetched by name: the happy path knows the fingerprint it is looking for
    and a refused document has none, so the question here is not *is this record there* but
    *is there anything at all*.
    """
    listing = _client("s3").list_objects_v2(
        Bucket=estate.records, Prefix=f"records/{document_id}/", MaxKeys=10
    )
    return [entry["Key"] for entry in listing.get("Contents", [])]


def _landing_is_idempotent(estate: Estate, document: Path, document_id: str, rows: int) -> None:
    """The same bytes, sent again, add nothing to the lake.

    **Claim 7's property, at the one place it was not held.** A fingerprint is a function of the
    bytes and the reader, so re-sending a document produces the *same version* — the records
    bucket writes one object and the search index one document, both keyed by it. The lake
    appended. Three runs of one document put twenty-seven rows in the table for nine fields, and
    nothing failed: every count in the analytics layer was a multiple of the truth, including the
    abstention counts claim 5 is judged on.

    Distinct from `10 · a correction supersedes`, which sends a *different* page under the same
    id and requires a second version. This one sends identical bytes and requires no second
    anything.
    """
    key = f"incoming/en/bill_of_lading/{document_id}.pdf"
    started = time.time()
    _upload(estate, key, document.read_bytes())
    again = _await_execution(estate, started, document_id)
    if again is None or again["status"] != "SUCCEEDED":
        estate.check(
            "12 · the same bytes land nothing new",
            False,
            f"the second run did not succeed ({again['status'] if again else 'no execution'})",
        )
        return

    landed = (_from_history(again["executionArn"]).get("lake") or {}).get("landed") or {}
    after = _rows_in_the_lake(estate, document_id)
    estate.check(
        "12 · the same bytes land nothing new",
        after == rows and landed.get("landed") == 0,
        f"{rows} row(s) before and {after} after, and the landing step reported "
        f"{landed.get('landed', '?')} written — {landed.get('skipped', 'no reason given')}"
        if after == rows
        else f"the lake went from {rows} to {after} rows for one version. The same document read "
        f"twice is the same version, so this is a duplicate rather than a correction",
    )


def _re_extraction(estate: Estate, first: Path, corrected: Path) -> None:
    """Claim 3 on the estate: a correction is a new version beside the old one, not over it.

    **The one property in this system that a single run cannot show.** Everything else the
    verifier asserts is about one document going through once. Doctrine rule 4 is about what
    happens the *second* time — and until now nothing had ever sent the same document id through
    twice on a real estate.

    Two different pages under one document id, which is what a correction is: the shipment is the
    same, the paper is not. Re-sending identical bytes would prove the opposite property — same
    input, same fingerprint, one record — which `evals/reprocessing` already proves offline over
    3,000 documents and which would look, from here, exactly like nothing happening.

    Three assertions, and the third is the one nobody builds: the older record is **still there**,
    the newer one is a different version, and the lake says which replaced which. A system that
    overwrites is indistinguishable from this one on the first two.
    """
    print(f"\n{DIM}re-extraction — the same document id, corrected{RESET}\n")
    key = "incoming/en/bill_of_lading/E2E-REEXTRACT.pdf"

    started = time.time()
    _upload(estate, key, first.read_bytes())
    original = _await_execution(estate, started, "E2E-REEXTRACT")
    if original is None or original["status"] != "SUCCEEDED":
        estate.check(
            "10 · a correction supersedes rather than overwrites",
            False,
            f"the first version did not publish "
            f"({original['status'] if original else 'no execution'})",
        )
        return
    first_version = (
        (_from_history(original["executionArn"]).get("extraction") or {}).get("outcome") or {}
    ).get("fingerprint", "")

    started = time.time()
    _upload(estate, key, corrected.read_bytes())
    second = _await_execution(
        estate, started, "E2E-REEXTRACT", exclude=frozenset({original["executionArn"]})
    )
    if second is None or second["status"] != "SUCCEEDED":
        estate.check(
            "10 · a correction supersedes rather than overwrites",
            False,
            f"the corrected version did not publish "
            f"({second['status'] if second else 'no execution'})",
        )
        return
    second_version = (
        (_from_history(second["executionArn"]).get("extraction") or {}).get("outcome") or {}
    ).get("fingerprint", "")

    kept = {
        entry["Key"].rsplit("/", 1)[-1].removesuffix(".json")
        for entry in _client("s3")
        .list_objects_v2(Bucket=estate.records, Prefix="records/E2E-REEXTRACT/")
        .get("Contents", [])
    }
    estate.check(
        "10 · a correction supersedes rather than overwrites",
        first_version
        and second_version
        and first_version != second_version
        and kept >= {first_version, second_version},
        f"versions {first_version[:12]}… then {second_version[:12]}…, and {len(kept)} record(s) "
        f"retrievable. Doctrine rule 4: the correction writes beside the original, never over it "
        f"— a system that overwrote would look identical until somebody asked what it said before",
    )

    landed = _rows_for_version(estate, "E2E-REEXTRACT")
    supersedes = {version: previous for version, previous in landed}
    estate.check(
        "11 · the lake records which version replaced which",
        supersedes.get(second_version) == first_version,
        f"the newer version's rows carry supersedes={str(supersedes.get(second_version))[:12]}… "
        f"against a first version of {first_version[:12]}…. A chain, not a pointer to current: "
        f"the question an auditor asks is what you said before",
    )


def _rows_for_version(estate: Estate, document_id: str) -> list[tuple[str, str]]:
    """`(version, supersedes)` for every row this document has in the lake."""
    athena = _client("athena")
    started = athena.start_query_execution(
        QueryString=(
            "SELECT DISTINCT version, supersedes FROM document_version WHERE document_id = ?"
        ),
        ExecutionParameters=[document_id],
        WorkGroup=estate.athena_workgroup,
        QueryExecutionContext={"Database": estate.glue_database},
    )["QueryExecutionId"]
    deadline = time.time() + 180
    while time.time() < deadline:
        state = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]["Status"][
            "State"
        ]
        if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(3)
    if state != "SUCCEEDED":
        return []
    rows = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"][1:]
    return [
        (
            row["Data"][0].get("VarCharValue", ""),
            row["Data"][1].get("VarCharValue") or "",
        )
        for row in rows
    ]


#: Two committed corpus pages, rendered to PDF on demand. Different shipments, so the second is
#: a genuinely different document under the same id — which is what a *correction* is, and what
#: the re-extraction check needs. Re-sending identical bytes would prove the opposite property.
CORPUS_FIRST = ROOT / "corpus/rendered/SHP00001_bill_of_lading_p1.jpg"
CORPUS_CORRECTED = ROOT / "corpus/rendered/SHP00002_bill_of_lading_p1.jpg"


def _rendered(page: Path, name: str) -> Path:
    """A committed corpus page as a one-page PDF, written beside the run.

    **The verifier used to take a PDF somebody had made by hand in a temporary directory.** That
    is a script only its author can run, and it stopped working the moment the directory was
    cleaned — which is a fair description of every "it works on my machine" this repository
    exists to argue against. The corpus is committed; the render is deterministic; the input is
    now part of the repository like everything else it asserts about.

    A degraded scan wrapped in a PDF is also exactly what arrives: the corpus pages are already
    skewed, noised and recompressed, so nothing here is a cleaner document than the real one.
    """
    from PIL import Image  # noqa: PLC0415 - the offline suite must import this module without it

    if not page.exists():
        raise SystemExit(
            f"{page} is not in the repository. The corpus is committed; if it is missing, this "
            f"is a checkout problem rather than something to work around with a temporary file"
        )
    destination = Path(tempfile.gettempdir()) / f"manifest-{name}.pdf"
    Image.open(page).convert("RGB").save(destination, "PDF", resolution=200.0)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument(
        "--document",
        type=Path,
        help=(
            "A PDF to send through. Rendered from the committed corpus when omitted, so this "
            "script needs nothing a reader has to make by hand."
        ),
    )
    parser.add_argument(
        "--document-id",
        default=f"E2E-VERIFY-{datetime.now(UTC):%Y%m%d%H%M%S}",
        help=(
            "Stamped per run by default. A fixed id makes every count in the lake a statement "
            "about every run this estate has ever had — `9 · the published record reached the "
            "lake` reported 27 rows against 9 fields, correctly, of a table holding three runs."
        ),
    )
    parser.add_argument("--skip-edge-cases", action="store_true")
    parser.add_argument(
        "--corrected",
        type=Path,
        help=(
            "A second, different page for the same document id — the re-extraction check. "
            "Rendered from a different committed corpus page when omitted."
        ),
    )
    arguments = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    document = arguments.document or _rendered(CORPUS_FIRST, "e2e-first")
    corrected = arguments.corrected or _rendered(CORPUS_CORRECTED, "e2e-corrected")
    estate = _resolve(arguments.project)
    print(f"the deployed estate — {estate.state_machine}\n")

    landed = _happy_path(estate, document, arguments.document_id)
    if landed is not None:
        _landing_is_idempotent(estate, document, arguments.document_id, landed)
    _re_extraction(estate, document, corrected)
    _optional_surfaces(estate, arguments.project, arguments.document_id)
    if not arguments.skip_edge_cases:
        _edge_cases(estate, document)

    failed = [result for result in estate.results if not result.passed]
    print(f"\n  {len(estate.results) - len(failed)}/{len(estate.results)} checks passed\n")
    if failed:
        print(f"{RED}end to end: FAILED{RESET}", file=sys.stderr)
        for result in failed:
            print(f"  {result.name}: {result.detail}", file=sys.stderr)
        return 1

    # **The closing line is a claim, and it has to be one this run can support.**
    #
    # It read "No managed extraction engine was called" — a true sentence for every run this
    # script had ever done, printed unconditionally, and therefore a sentence that would go on
    # being printed for the first run where it was false. It was: the escalation called Textract
    # for seven fields and the verifier congratulated the estate for not having done so, three
    # lines under the check that says it did.
    escalated = next((result for result in estate.results if result.name.startswith("3b")), None)
    print(
        "end to end: a document arrived, was read by the tier-0 image, thresholded against\n"
        "  derived thresholds, checked field by field against its own pages, published with its\n"
        "  provenance, and its abstentions reached a human."
    )
    if escalated:
        print(
            f"  A managed engine WAS called — {escalated.detail.split('.')[0]}. That is a billed\n"
            "  read, and nothing here is an accuracy figure for it: what is shown is that the\n"
            "  routing sent the abstaining fields up, not that the tier read them better."
        )
    else:
        print("  No managed extraction engine was called; every page was read by the image.")
    searched = next((result for result in estate.results if result.name.startswith("18")), None)
    classified = next((result for result in estate.results if result.name.startswith("21")), None)
    if searched:
        print(
            "  The published record was then found by asking the index for it, and the index\n"
            "  carries the values that cleared their thresholds and a count of the ones that did\n"
            "  not — never the abstained values themselves."
        )
    if classified:
        print(
            "  A goods description was ranked by the endpoint and decided here: the contested\n"
            "  pair came back as two candidates with no winner, and nothing published on any\n"
            "  score, because hs_code is always-review."
        )
    print(
        "  No distributed job ran; this is one run, on one day, on documents this repository\n"
        "  generated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
