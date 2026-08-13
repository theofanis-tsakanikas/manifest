"""Compare what several documents say about one shipment, and send the disagreements to a human.

**Claim 4, on the estate.** `evals/reconciliation/` has proved this offline over a corpus with
planted mismatches since the rules were written; nothing in the running system ever compared two
documents. The claim was a property of a pure function and an untested property of the estate.

**It decides nothing.** `core.reconciliation.reconcile` applies one rule to one pair of values and
returns a `Finding`; the rules, their tolerances, their units and their severities are data in
`contracts/reconciliation/shipment.yaml`. This handler reads records, builds the pairs, and calls
that function once per rule.

**Only published values are compared.** A field that abstained has no value this system stands
behind, and reconciling against it would manufacture a disagreement out of a reading nobody
approved — a finding that costs a reviewer twenty seconds and is about the extractor rather than
about the shipment. Absent is passed through as absent; `reconcile` has a documented answer for
that and it is not `DISAGREE`.

**A disagreement goes to the queue, not to a log.** `core.review.Reason.DISAGREEMENT` has existed
since the queue was written and nothing ever produced one. The operator is *paid* to catch these,
which makes them review items rather than telemetry — and it means claim 4's output lands in
claim 5's capacity model, where a rule that fires constantly shows up as a queue nobody can
serve.

**The caller names the documents.** No shipment convention is invented here: nothing in the
pipeline knows which documents belong together, `analytics/schema.sql` lists `shipment_id` among
the columns nothing produces, and a handler that parsed one out of a document id would be making
that up. What it is given, it compares; what it returns names both sides.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from manifest.contracts.loader import load, to_tolerance
from manifest.core.reconciliation import NUMERIC_TYPES, Comparison, Severity, Side, reconcile


class HandlerError(RuntimeError):
    """Refusal."""


def _client(name: str) -> Any:
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name, config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})
    )


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value


def _published_values(bucket: str, documents: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """`(document type, field) -> the value that document published`, and nothing else.

    Keyed by *type* rather than by document id, because that is what a rule names: the contract
    says "the bill of lading's gross weight against the packing list's", and which file carried
    it is the finding's business rather than the rule's.
    """
    values: dict[tuple[str, str], str] = {}
    for named in documents:
        document_id, version = str(named.get("document_id", "")), str(named.get("version", ""))
        if not document_id or not version:
            raise HandlerError(
                "each document needs a document_id and a version. A reconciliation against "
                "'whatever is current' would compare a shipment against a moving target"
            )
        key = f"records/{document_id}/{version}.json"
        try:
            record = json.loads(_client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
        # Broad on purpose: a missing key, a denied read and a malformed body are one refusal.
        except Exception as error:
            raise HandlerError(f"no published record at {key}") from error

        document_type = str(record.get("document_type") or "")
        if not document_type:
            raise HandlerError(f"the record at {key} names no document type; no rule can match it")
        for entry in record.get("fields", []):
            if entry.get("publishable") and entry.get("value"):
                values[(document_type, str(entry["field"]))] = str(entry["value"])
    return values


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """`{shipment, documents: [{document_id, version}, ...]}` -> every rule's finding."""
    shipment = str(event.get("shipment") or "")
    documents = event.get("documents")
    if not shipment or not isinstance(documents, list) or not documents:
        raise HandlerError(
            "give {'shipment': '<id>', 'documents': [{'document_id', 'version'}, ...]}. "
            "Reconciling one document against itself is not a comparison"
        )

    contracts = load(Path(os.environ.get("CONTRACTS_DIR", "/var/task/contracts")))
    values = _published_values(_env("RECORDS_BUCKET"), documents)

    findings = []
    for rule in contracts.reconciliation.rules:
        left_contract = contracts.document(rule.left.document).field(rule.left.field)
        finding = reconcile(
            shipment,
            Comparison(
                rule_id=rule.id,
                severity=Severity(rule.severity),
                tolerance=to_tolerance(rule.tolerance),
                comparison=tuple(left_contract.comparison),
                numeric=left_contract.type.value in NUMERIC_TYPES,
            ),
            Side(
                document=rule.left.document,
                field=rule.left.field,
                value=values.get((rule.left.document, rule.left.field)),
                unit=left_contract.unit,
            ),
            Side(
                document=rule.right.document,
                field=rule.right.field,
                value=values.get((rule.right.document, rule.right.field)),
                unit=contracts.document(rule.right.document).field(rule.right.field).unit,
            ),
        )
        findings.append(finding)

    disagreements = [finding for finding in findings if finding.is_disagreement]
    for finding in disagreements:
        _queue(finding)

    return {
        "shipment": shipment,
        "rules_applied": len(findings),
        "disagreements": len(disagreements),
        "findings": [
            {
                "rule": finding.rule,
                "outcome": str(finding.outcome),
                "severity": str(finding.severity),
                "left": {"document": finding.left.document, "value": finding.left.value},
                "right": {"document": finding.right.document, "value": finding.right.value},
                "explanation": finding.explanation,
            }
            for finding in findings
        ],
        # Stated rather than inferred from the count: a run where every rule found both sides
        # absent produces no disagreement and has compared nothing, and those are different.
        "compared": sum(1 for finding in findings if finding.left.value and finding.right.value),
    }


def _queue(finding: Any) -> None:
    """One review item per disagreement, with the reason the queue already has a name for.

    `Reason.DISAGREEMENT` has existed since `core.review` was written and nothing had ever
    produced one. Sending these to a log instead would keep claim 4's output out of claim 5's
    capacity model — and a rule that fires constantly would then look free.
    """
    _client("sqs").send_message(
        QueueUrl=_env("REVIEW_QUEUE_URL"),
        MessageBody=json.dumps(
            {
                "shipment": finding.shipment,
                "rule": finding.rule,
                "reason": "disagreement",
                "severity": str(finding.severity),
                "left": {
                    "document": finding.left.document,
                    "field": finding.left.field,
                    "value": finding.left.value,
                },
                "right": {
                    "document": finding.right.document,
                    "field": finding.right.field,
                    "value": finding.right.value,
                },
                "explanation": finding.explanation,
            }
        ),
    )
