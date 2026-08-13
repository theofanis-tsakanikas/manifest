"""Ask the endpoint to rank tariff headings, and decide here.

**The endpoint was in the estate and nothing called it.** `infra/extraction/classification.tf`
has declared a SageMaker serverless endpoint, its role, its VPC attachment and its data capture
since it was written; the flag that stands it up was refused at plan time with an accurate
message — *this repository has never produced an artefact*. It has now, so this is the caller.

**What is new here is one boundary, held.** The model ranks. `classification.hs.decide` applies
the minimum score, the abstention band and the declared contested pairs against
`contracts/classification/`. Those two sentences are the whole design, and the reason the
handler is this short is that everything worth arguing about lives in a pure function a test can
reach and `gate-proof` can break.

**Nothing published, on any score.** `hs_code` is declared always-review in
`contracts/documents/`, so a `PROPOSED` disposition is not a classification — it is a proposal
put in front of a person, and `Proposal.publishes` is `False` unconditionally. This handler
returns; it does not write a record.

**The description is a counterparty's text.** It came off an invoice somebody else wrote, and it
reaches a vectoriser as a string. There is no prompt here for an instruction to be interpreted
by — which is the structural version of the control `extraction/aws/llm.py` argues for at
length, and the reason the classification path was never the exposed one.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from manifest.classification.hs import Candidate, Heading, decide
from manifest.contracts.loader import load

#: The endpoint answers from a fitted model with no I/O of its own. Longer than this is a cold
#: start that has gone wrong rather than one still in progress, and a proposal nobody is waiting
#: on is worth less than an error somebody reads.
TIMEOUT_SECONDS = 30


class HandlerError(RuntimeError):
    """Refusal."""


def _client(name: str) -> Any:
    """Named rather than hard-coded, because a check reads this call.

    `scripts/check_deploy_path.py` greps the handlers for `_client("service")` and requires an
    interface endpoint for each one — the control that exists because a missing endpoint does not
    fail, it hangs for 180 seconds with no log line. A client constructed any other way is
    invisible to it, and this handler is exactly the case it was written for: nothing in the VPC
    has ever called SageMaker before.
    """
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name,
        config=Config(
            connect_timeout=5,
            read_timeout=TIMEOUT_SECONDS,
            retries={"max_attempts": 2},
        ),
    )


def _ranked(payload: dict[str, Any], headings: tuple[Heading, ...]) -> tuple[Candidate, ...]:
    """The endpoint's candidates, as this repository's type, or a refusal.

    **A code the contract does not declare is refused rather than dropped.** Silently ignoring it
    would let an artefact fitted on a different set of headings answer for this one and look
    like a narrower ranking — the failure would be a proposal that quietly stopped offering a
    heading, which is invisible. Refusing names the mismatch between the artefact and the
    contract, which is the thing that actually went wrong.
    """
    described = {heading.code: heading.description for heading in headings}
    candidates: list[Candidate] = []
    for candidate in payload.get("candidates", ()):
        code = str(candidate.get("code", ""))
        if code not in described:
            raise HandlerError(
                f"the endpoint proposed heading {code!r}, which "
                f"contracts/classification/headings.yaml does not declare. The artefact was "
                f"fitted against a different set of headings than the one this system decides "
                f"against"
            )
        candidates.append(
            Candidate(
                code=code,
                description=described[code],
                # Through `str`: `Decimal(0.97)` is 0.9699999999999999733546474089962430298328399
                # and the band is compared at four places. Every other score in this system
                # crosses into `Decimal` the same way.
                score=Decimal(str(candidate.get("score", 0))),
            )
        )
    if not candidates:
        raise HandlerError("the endpoint returned no candidates; there is nothing to decide on")
    return tuple(candidates)


def _floor(payload: dict[str, Any], headings: int) -> Decimal:
    """The model's own floor, checked before it is trusted.

    **Why the endpoint supplies this and the contract does not.** `headings.yaml` declares
    `minimum_score: 0.35`, derived for a similarity ratio between two strings. The endpoint
    returns a softmax over twelve classes, where uniform is 0.083 and a strong match peaks near
    0.30 — so the contract's number would refuse every proposal the endpoint can make, and a
    smaller one written to stop that would be a threshold supported by nothing. `artefact.prior`
    derives it instead, from what the model scores on a description it cannot read.

    **And it is checked, because a number a model supplies is a number a model could lower.**
    Below the uniform prior the floor is not a floor: every candidate clears it by construction,
    and the endpoint would have raised its own dispositions from `no_proposal` to `proposed` by
    reporting a smaller number. Doctrine rule 5 — nothing approves itself — with arithmetic
    behind it rather than a comment.
    """
    reported = payload.get("minimum_score")
    if not isinstance(reported, int | float):
        raise HandlerError(
            "the endpoint reported no minimum_score. The floor is derived from the fitted model "
            "and travels with it; the contract's is on a different scale and cannot stand in"
        )
    uniform = Decimal(1) / Decimal(headings)
    floor = Decimal(str(reported))
    if floor < uniform:
        raise HandlerError(
            f"the endpoint reported a floor of {floor}, below the uniform prior over "
            f"{headings} headings ({uniform:.4f}). Every candidate clears that by construction, "
            f"so it is not a floor — it is the model raising its own proposals"
        )
    return floor


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """`{"goods": "..."} -> the proposal, with its disposition and its reason."""
    goods = event.get("goods")
    if not isinstance(goods, str) or not goods.strip():
        raise HandlerError("give {'goods': '<description>'}; an empty description ranks nothing")

    endpoint = os.environ.get("CLASSIFIER_ENDPOINT")
    if not endpoint:
        raise HandlerError("CLASSIFIER_ENDPOINT is unset; there is no endpoint to ask")

    response = _client("sagemaker-runtime").invoke_endpoint(
        EndpointName=endpoint,
        ContentType="application/json",
        Accept="application/json",
        Body=json.dumps({"goods": goods}).encode("utf-8"),
    )
    payload = json.loads(response["Body"].read().decode("utf-8"))

    # Read out loud rather than assumed. The endpoint states that it has not decided, and a
    # payload that claimed otherwise would be an artefact that had taken the decision inside a
    # serialised model in an S3 object — past every check in this repository.
    if payload.get("decided"):
        raise HandlerError(
            "the endpoint reported that it decided. It ranks; the band, the minimum score and "
            "the contested pairs are applied here, against contracts/classification/, by code a "
            "test can reach"
        )

    contract = load(Path(os.environ.get("CONTRACTS_DIR", "/var/task/contracts"))).classification
    headings = tuple(
        Heading(
            code=heading.code,
            description=heading.description,
            contested_with=tuple(heading.contested_with),
        )
        for heading in contract.headings
    )

    proposal = decide(
        goods,
        _ranked(payload, headings),
        headings,
        minimum_score=_floor(payload, len(headings)),
        # **The band comes from the contract and never from the endpoint.** The floor is a
        # property of the fitted model — a different scorer puts its scores on a different scale
        # — but the gap the system is willing to call decided is a policy this repository owns,
        # and a model that could widen it would be approving its own proposals.
        margin=contract.margin,
    )

    answer = {
        "goods": proposal.goods,
        "disposition": str(proposal.disposition),
        "candidates": [
            {
                "code": candidate.code,
                "description": candidate.description,
                "score": str(candidate.score),
            }
            for candidate in proposal.candidates
        ],
        "margin": str(proposal.margin),
        "explanation": proposal.explanation,
        # Stated in the payload rather than left to be inferred. `hs_code` is always-review, so
        # this is `false` on every score the endpoint can return, and a consumer reading only
        # this response cannot mistake a proposal for a classification.
        "publishes": proposal.publishes,
    }
    _record(answer)
    return answer


def _record(proposal: dict[str, Any]) -> None:
    """Write the proposal where claim 5 can find it, or say why it could not.

    **This is the denominator, and it exists because the obvious place for it does not.**
    `infra/extraction/classification.tf` declared a SageMaker data capture until the API refused
    it: serverless inference supports none. What the capture was for does not go away with it —
    doctrine rule 2 measures a reviewer's agreement rate with the model, and a rate needs both
    sides of the comparison written down.

    What is recorded is the **proposal**, after the floor, the band and the contested pairs — the
    thing a reviewer is actually shown — rather than the endpoint's raw ranking. The capture
    would have held the other one.

    **Nothing reads these yet.** An agreement rate needs the reviewer's answer too, and that side
    is not wired to the estate — `evals/review` scores a generated queue offline. Saying this out
    loud rather than letting a writer imply a metric: what exists is the half that cannot be
    added retroactively, because a proposal nobody kept is a proposal nobody can compare against.

    **A failure here does not fail the classification.** The proposal is correct and the caller
    is waiting for it; losing the evidence write is a gap in a metric, and turning that into a
    refusal would make the measurement more important than the thing being measured. It is
    logged, loudly, because a denominator quietly missing entries is claim 5 measuring a set it
    does not describe.
    """
    bucket = os.environ.get("EVIDENCE_BUCKET")
    if not bucket:
        print("EVIDENCE_BUCKET is unset; this proposal is not recorded and claim 5 cannot count it")
        return

    from datetime import UTC, datetime  # noqa: PLC0415 - the clock is the adapter's, not core's

    stamped = datetime.now(UTC)
    # Keyed by day and then by content, so a repeated question does not write a second object and
    # the count is of *proposals*, not of invocations. The digest is of the goods description
    # and the disposition together: the same description decided differently is a different fact
    # and must not overwrite the earlier one.
    digest = sha256(
        f"{proposal['goods']}|{proposal['disposition']}|{proposal['margin']}".encode()
    ).hexdigest()[:32]
    try:
        _client("s3").put_object(
            Bucket=bucket,
            Key=f"classification-proposals/{stamped:%Y/%m/%d}/{digest}.json",
            Body=json.dumps({**proposal, "proposed_at": stamped.isoformat()}).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=os.environ["DATA_KEY_ARN"],
        )
    # Broad on purpose: the classification stands whatever went wrong here.
    except Exception as error:
        print(f"the proposal was not recorded: {error}. Claim 5's denominator is short by one")
