"""What the endpoint runs. It ranks; it does not decide.

**The boundary this file sits on is the project's central one.** `CLAUDE.md`: a model may
*propose* a tariff classification, and deterministic code owns whether anything is published.
So this returns candidates and their scores and stops. The abstention band, the minimum score
and the contested-pair rule live in `contracts/classification/headings.yaml` and are applied by
`classification/hs.py`, where a test can reach them and `gate-proof` can break them.

A version of this that returned "the answer" would move that decision inside a serialised model
in an S3 object — the one place in this system where no check can see it, and where changing it
is a redeploy rather than a diff.

**It carries no confidence and the scores are not one.** `predict_proba` gives a distribution
over twelve headings that were fitted on 66 hand-written descriptions. It is a ranking signal,
which is what the gap between the top two is used for; it is not a measured frequency over a
labelled distribution and it never enters claim 1's derivation. `extraction/aws/llm.py` refuses a
self-reported score for the same reason and at more length.

SageMaker's scikit-learn container calls these four functions by name.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:  # pragma: no cover - one branch runs offline, the other inside the serving container
    from manifest.classification.artefact import scores
except ImportError:  # the container puts `code/` on the path and nothing else
    from artefact import scores  # type: ignore[no-redef]

#: What the artefact is called inside `model.tar.gz`. One name in two files, and the training
#: script is the one that writes it.
ARTEFACT_NAME = "classifier.json"

#: How many candidates come back. Three, because the decision downstream is about the gap between
#: the top two and a reviewer looking at a contested pair is served by seeing the third.
CANDIDATES = 3


def model_fn(model_dir: str) -> Any:
    """Load the artefact the training script wrote. It is JSON, and that is deliberate.

    Not `joblib.load`. The container serves scikit-learn 1.2.1 and this repository runs 3.12,
    where that version does not build — but the version mismatch is only the reason it came up.
    `classification/artefact.py` gives the other two: a pickle is the one thing in this estate
    whose behaviour cannot be read, and unpickling an S3 object is executing it. The scorer here
    is the same module the training script verified against scikit-learn to 1e-9 before this
    file was ever packaged.
    """
    with open(os.path.join(model_dir, ARTEFACT_NAME), encoding="utf-8") as handle:
        return json.load(handle)


def input_fn(body: str, content_type: str = "application/json") -> str:
    """The goods description, and nothing else.

    **The text is a counterparty's**, printed on an invoice they wrote, and everything else in
    this system treats it as data rather than instruction. Here that is structural: it is a
    string handed to a vectoriser, so there is nothing for an instruction to be interpreted by.
    The refusal below is about shape, not safety.
    """
    if content_type != "application/json":
        raise ValueError(f"content type {content_type} is not supported; send application/json")
    payload = json.loads(body)
    goods = payload.get("goods") if isinstance(payload, dict) else None
    if not isinstance(goods, str) or not goods.strip():
        raise ValueError("send {'goods': '<description>'} — an empty description ranks nothing")
    return goods


def predict_fn(goods: str, model: Any) -> dict[str, Any]:
    """The ranked headings and the floor this model derived. No winner is chosen here."""
    return {
        "candidates": scores(model, goods)[:CANDIDATES],
        # Carried out of the artefact rather than recomputed: it was derived at fit time from
        # this model's own behaviour on a description it cannot read, and `artefact.prior`
        # explains why a written constant would be wrong here.
        "minimum_score": model["minimum_score"],
    }


def output_fn(prediction: dict[str, Any], accept: str = "application/json") -> str:
    """Candidates and the gap, because the gap is what the contract's band is declared on.

    Computed here rather than downstream only because both numbers are already in hand; the
    *decision* it feeds is still made against `headings.yaml`, by code a test can reach.
    """
    candidates = prediction["candidates"]
    gap = candidates[0]["score"] - candidates[1]["score"] if len(candidates) > 1 else 1.0
    return json.dumps(
        {
            "candidates": candidates,
            "minimum_score": prediction["minimum_score"],
            "gap": round(gap, 6),
            # Stated in the payload so that a consumer reading only this cannot mistake a ranking
            # for a decision. `hs.py` applies the band; this says out loud that it has not.
            "decided": False,
        }
    )
