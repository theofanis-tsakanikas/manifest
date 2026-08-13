"""Score a goods description against a fitted artefact. Pure Python, no model library.

**Why the artefact is JSON and not a pickle, which is three arguments and not one.**

*It has to load.* SageMaker's scikit-learn container serves version 1.2.1. This repository runs
Python 3.12, where 1.2.1 has no wheel and does not build. Pinning the trainer to the container's
version is the textbook answer and it is unavailable here; the version pair is a coincidence this
project would then have to keep true for ever, in a file nobody reads, and the failure mode is an
endpoint that returns 500 on every request after a successful deploy.

*It has to be readable.* `CLAUDE.md` draws one line down the middle of this system: models
propose, deterministic code decides. A pickle is the one artefact in the estate where that line
cannot be inspected — `joblib.load` returns an object whose behaviour is whatever was serialised.
As JSON, the fitted model is a vocabulary, an array of inverse document frequencies and twelve
rows of coefficients. Somebody can read it. A diff shows what changed between two fits.

*It must not be code.* Unpickling executes what the stream tells it to. The artefact lives in S3
and is loaded by a container inside the VPC; making it data rather than instructions removes that
entirely, and it is the same reasoning the extraction prompts use on document text.

**What this costs, stated rather than hidden.** Everything below re-implements what
`TfidfVectorizer` and `LogisticRegression` do at predict time, and a re-implementation that
disagrees with the original is worse than a pickle. So `scripts/train_classifier.py` fits with
scikit-learn, exports here, and then **re-scores every description in the training set and every
contested one through this module**, refusing to write an artefact unless the two agree to 1e-9.
The check is the same shape as claim 2's: a second path over the same input, and the artefact
only exists if the paths agree.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: scikit-learn's default word pattern, transcribed. Two or more word characters, so single
#: letters and punctuation never become features — `5L` survives as a token and `,` does not.
TOKEN_PATTERN = re.compile(r"(?u)\b\w\w+\b")

#: The artefact shape this module can read. Bumped when the exported keys change, and checked on
#: load, because an endpoint quietly scoring against a layout it half-understands is the failure
#: this whole file exists to avoid.
VERSION = 1


def terms(text: str, ngram_max: int) -> list[str]:
    """Lowercased word n-grams, in scikit-learn's order and joining.

    Unigrams first, then bigrams, each joined by a single space — the same construction
    `TfidfVectorizer` uses, so the vocabulary exported from a fitted one is keyed the same way.
    """
    tokens = TOKEN_PATTERN.findall(text.lower())
    found = list(tokens)
    for size in range(2, ngram_max + 1):
        found.extend(
            " ".join(tokens[start : start + size]) for start in range(len(tokens) - size + 1)
        )
    return found


def _features(artefact: dict[str, Any], goods: str) -> dict[int, float]:
    """The sparse tf-idf row for one description, l2-normalised.

    Terms outside the fitted vocabulary are dropped rather than counted, which is what
    `TfidfVectorizer.transform` does — a description made entirely of unseen words produces the
    zero vector, every class scores its intercept, and the gap between the top two is whatever
    the intercepts say. That is the correct behaviour and it is worth naming: it means an
    unfamiliar description does not get a confident answer by accident.
    """
    vocabulary: dict[str, int] = artefact["vocabulary"]
    idf: list[float] = artefact["idf"]

    counts: dict[int, int] = {}
    for term in terms(goods, artefact["ngram_max"]):
        index = vocabulary.get(term)
        if index is not None:
            counts[index] = counts.get(index, 0) + 1

    row = {
        index: (1.0 + math.log(count) if artefact["sublinear_tf"] else float(count)) * idf[index]
        for index, count in counts.items()
    }
    norm = math.sqrt(sum(value * value for value in row.values()))
    if norm == 0:
        return row
    return {index: value / norm for index, value in row.items()}


def prior(artefact: dict[str, Any]) -> float:
    """The highest score this model gives a description it cannot read at all.

    **The floor, derived rather than written, and this is doctrine rule 3 doing real work.**
    `contracts/classification/headings.yaml` declares `minimum_score: 0.35` and that number is
    correct for the scorer it was written for — a similarity ratio between two strings, which
    lives between roughly 0.3 and 0.6. A softmax over twelve classes lives on a different scale
    entirely: uniform is 0.083 and a good match peaks near 0.30. Applying 0.35 to it would refuse
    every proposal the endpoint could ever make, and applying a smaller number chosen to stop
    that happening would be a threshold supported by nothing.

    So it is computed. A description made entirely of terms outside the vocabulary produces the
    zero vector: no evidence at all, and the scores are the intercepts alone. Whatever the model
    hands out on no information is its prior, and a real description scoring at or below it has
    told the model nothing. That is a floor with a meaning, and it moves when the model is
    refitted, which is the property a written constant does not have.
    """
    return scores(artefact, "")[0]["score"]


def scores(artefact: dict[str, Any], goods: str) -> list[dict[str, Any]]:
    """Every heading with its probability, highest first.

    Softmax over the linear scores, which is what multinomial logistic regression does with more
    than two classes — not a per-class sigmoid. The difference matters here and nowhere else in
    this system: softmax makes the probabilities sum to one, so two headings that fit equally
    well split the mass and the *gap* between them collapses. The abstention band in
    `contracts/classification/headings.yaml` is declared on that gap, for exactly this reason.
    """
    if artefact.get("version") != VERSION:
        raise ValueError(
            f"artefact version {artefact.get('version')!r} is not {VERSION}; the exported layout "
            f"changed and this scorer would be reading fields that no longer mean what it thinks"
        )

    row = _features(artefact, goods)
    linear = [
        intercept + sum(weight[index] * value for index, value in row.items())
        for weight, intercept in zip(artefact["coefficients"], artefact["intercepts"], strict=True)
    ]

    # Shifted by the maximum before exponentiating. Standard, and not a micro-optimisation: a
    # linear score of a few hundred overflows `math.exp` and the endpoint answers with an
    # exception instead of a ranking.
    highest = max(linear)
    exponentiated = [math.exp(value - highest) for value in linear]
    total = sum(exponentiated)

    return sorted(
        (
            {"code": code, "score": value / total}
            for code, value in zip(artefact["classes"], exponentiated, strict=True)
        ),
        key=lambda candidate: candidate["score"],
        reverse=True,
    )
