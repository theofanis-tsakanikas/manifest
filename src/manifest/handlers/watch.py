"""Does what is arriving still resemble what the thresholds were derived from?

**The envelope, applied to traffic.** `corpus/envelope.yaml` declares the confidence distribution
and abstention band this system's numbers are only meaningful inside, and a test turns the build
red when the *generator* leaves it. Nothing was checking the *documents*, and `core/drift.py` was
written for exactly that and never called by anything that runs.

The failure it exists for does not announce itself. A new scanning supplier ships 150 dpi instead
of 300; the reader keeps returning text; confidences drift down; the abstention rate doubles.
Every threshold is still met, every gate still passes, every dashboard is still green — and the
queue fills with work nobody planned for, which doctrine rule 1 calls a failure of the system
rather than of the reviewers.

**It reports a finding. It never adjusts anything.** Nothing here moves a threshold and nothing
here widens a band. An envelope that stretched to accommodate the traffic would be a control
agreeing with whatever happened, which is no control at all — so the output is an alert and a
return value, and the only way to change a band is to edit the declaration and say why.

**It fires in both directions**, and the second is the one nobody watches. A reader that has
become suddenly *confident* has usually stopped seeing something — a template change that turns a
stamped field into whitespace reads as a clean page. `core.drift` treats a median above the upper
bound as drift for that reason, and this handler passes it through unsoftened.

**The bands arrive as configuration, from the declaration.** Terraform reads `corpus/envelope.yaml`
and passes the four numbers in; this handler refuses to run without them. A default here would be
a band chosen by whoever wrote the handler, in a place nobody reviews — which is the move the
envelope exists to prevent.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from manifest.core.drift import Band, Window, assess

#: The prefix the pipeline publishes records under. A window is built from records rather than
#: from readings because a threshold is applied to a *field*, and the field's confidence is what
#: the envelope declares a band for.
PREFIX = "records"


class HandlerError(RuntimeError):
    """Refusal."""


def _client(name: str) -> Any:
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name, config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 3})
    )


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(
            f"{name} is not set. The bands and the window are declarations, and a handler that "
            f"defaulted one would be choosing an operating envelope in a place nobody reviews"
        )
    return value


def _band(name: str) -> Band:
    return Band(
        name=name,
        lower=float(_env(f"{name.upper()}_MIN")),
        upper=float(_env(f"{name.upper()}_MAX")),
    )


def _window(bucket: str, hours: int) -> Window:
    """Every record published in the last `hours`, summarised.

    Only the *latest* version of each document contributes. A document a reviewer corrected has
    two records; counting both would weight reviewed documents twice and make the abstention rate
    a statement about how much review happened rather than about what arrived.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    latest: dict[str, tuple[datetime, str]] = {}
    pages = _client("s3").get_paginator("list_objects_v2")
    for page in pages.paginate(Bucket=bucket, Prefix=f"{PREFIX}/"):
        for entry in page.get("Contents", ()):
            if not entry["Key"].endswith(".json") or entry["LastModified"] < since:
                continue
            document = entry["Key"].split("/")[1]
            if document not in latest or entry["LastModified"] > latest[document][0]:
                latest[document] = (entry["LastModified"], entry["Key"])

    confidences: list[float] = []
    unscored = abstained = total = 0
    for _, key in latest.values():
        record = json.loads(_client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
        fields = record.get("fields", [])
        total += len(fields)
        scored = False
        for field in fields:
            if not field.get("publishable"):
                abstained += 1
            confidence = field.get("confidence")
            if confidence is not None:
                confidences.append(float(confidence))
                scored = True
        # **A reader that reports no score cannot drift**, and averaging it in as a zero would
        # manufacture drift out of a routing change — a day with more Greek documents would look
        # like a day with a worse reader.
        if not scored:
            unscored += 1

    return Window(
        documents=len(latest),
        confidences=tuple(confidences),
        unscored_documents=unscored,
        abstained_fields=abstained,
        total_fields=total,
    )


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """`{hours?}` — assess the arriving window against the declared envelope."""
    hours = int(event.get("hours") or _env("WINDOW_HOURS"))
    window = _window(_env("RECORDS_BUCKET"), hours)

    findings = assess(
        window=window,
        confidence_band=_band("median_confidence"),
        abstention_band=_band("abstention_rate"),
        # No default, by the same argument the core makes: how many documents make a window is a
        # property of the traffic, and a broker handling four hundred a day and one handling four
        # need different answers.
        minimum_documents=int(_env("MINIMUM_DOCUMENTS")),
    )

    drifted = [finding for finding in findings if finding.drifted]
    if drifted:
        _alert(drifted, window, hours)

    return {
        "hours": hours,
        "documents": window.documents,
        "unscored_documents": window.unscored_documents,
        "median_confidence": window.median_confidence,
        "abstention_rate": window.abstention_rate,
        "drifted": len(drifted),
        "findings": [
            {
                "measure": finding.measure,
                "verdict": str(finding.verdict),
                "observed": finding.observed,
                "band": [finding.band.lower, finding.band.upper],
                "reason": finding.reason,
            }
            for finding in findings
        ],
    }


def _alert(drifted: list[Any], window: Window, hours: int) -> None:
    """One message per assessment that found drift, to the topic an operator reads.

    A finding, addressed to a person. Nothing downstream of this consumes it automatically, and
    nothing should: the answer to drift is a decision — re-record, escalate more, or talk to the
    supplier — and a system that picked one would be adjusting its own envelope.
    """
    lines = "\n".join(
        f"  {finding.measure}: {finding.observed:.4f}, declared band "
        f"[{finding.band.lower}, {finding.band.upper}]\n    {finding.reason}"
        for finding in drifted
    )
    _client("sns").publish(
        TopicArn=_env("ALERTS_TOPIC_ARN"),
        Subject=f"manifest: the arriving traffic left its declared envelope ({len(drifted)})",
        Message=(
            f"{window.documents} document(s) in the last {hours}h, "
            f"{window.unscored_documents} of them read by a tier that reports no score.\n\n"
            f"{lines}\n\n"
            f"This is a finding, not an adjustment. Every threshold in this system was derived "
            f"from a distribution recorded at one moment, and a threshold whose supporting "
            f"distribution has moved does not become wrong loudly — it becomes unsupported, "
            f"and goes on publishing.\n\n"
            f"The answer is a decision: re-record against the new traffic, escalate more of it, "
            f"or ask the supplier what changed. Widening the band in corpus/envelope.yaml to "
            f"make this stop is the one answer that is not available."
        ),
    )
