#!/usr/bin/env python3
"""Fit the tariff classifier, and refuse it if it is confident where professionals are not.

**What this is not.** It is not a claim about classifying tariff headings. It is fitted on 66
hand-written descriptions over twelve headings, chosen because this repository's corpus falls
under them, against a nomenclature with five thousand. Any figure it produces is a statement
about `contracts/classification/training.yaml`, carries its N, and appears on no scoreboard —
`PLAN.md` and `docs/DECISIONS.md` both set that rule before this file existed and it is not
weakened here.

**Why fit anything at all, then.** Because the endpoint was declared, advertised in the README's
subtitle, and had nothing to serve — and because the property worth demonstrating is not accuracy
but *abstention*. Three of the twelve headings differ from their pair by one word: glazed against
unglazed, virgin against not, printed against not. A model fitted only on clear examples will
choose between them anyway, and will be most confident exactly where the trade is least certain.

So the gate is not "is it accurate". The gate is:

**Every ambiguous description must land inside the abstention band.** `headings.yaml` declares
that band on the *gap between the top two* rather than on the top score, for a reason written
there: a description matching two headings closely scores higher at the top than one matching a
single heading loosely. A model that fails this is refused and no artefact is written, because an
artefact that publishes a winner on a contested pair is worse than no endpoint.

**The held-out figure is reported and is not the gate.** With a training set this size the split
is small enough that its accuracy is a number about thirteen examples, and it says so.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

from manifest.classification.artefact import VERSION, prior, scores

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ROOT / "contracts" / "classification" / "training.yaml"
HEADINGS = ROOT / "contracts" / "classification" / "headings.yaml"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Held out for the reported figure. A quarter, so the number has enough examples behind it to be
#: worth printing and few enough that printing it without an N would be dishonest.
HELD_OUT = 0.25

#: The exported layout's version, and `artefact.py` refuses anything else. One number in two
#: files is one number that drifts, so the scorer's constant is the one imported here.
ARTEFACT_VERSION = VERSION

#: What the artefact is called inside `model.tar.gz`, and what `inference.py` opens.
ARTEFACT_NAME = "classifier.json"

#: How far the two scoring paths may differ. Float arithmetic in a different order gives a
#: different last bit; anything larger than this is a re-implementation that is actually wrong.
TOLERANCE = 1e-9

#: Fixed, so two runs of this script on one training set produce one model. A classifier whose
#: artefact changed per run would make the endpoint's behaviour depend on when it was built,
#: which is claim 3's property given away in the one place nobody would look for it.
SEED = 20260813


def _load() -> tuple[list[str], list[str], list[dict]]:
    training = yaml.safe_load(TRAINING.read_text(encoding="utf-8"))
    texts: list[str] = []
    labels: list[str] = []
    for code, descriptions in training["descriptions"].items():
        for description in descriptions:
            texts.append(description)
            labels.append(str(code))
    return texts, labels, training.get("ambiguous", [])


def _margin() -> float:
    """The abstention band, read from the contract rather than chosen here.

    Two numbers that must agree is one number that will drift, and this is the one where drift
    would be silent: a model fitted against a looser band than the one the gate applies would
    pass here and abstain in production, or worse, the other way round.
    """
    return float(yaml.safe_load(HEADINGS.read_text(encoding="utf-8"))["margin"])


def _fit(texts: list[str], labels: list[str]):
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.pipeline import make_pipeline  # noqa: PLC0415

    # **Words, not character n-grams, and the first version had it the other way round.**
    #
    # Character 3-to-5-grams looked right — the distinguishing token is sometimes a prefix,
    # *un*glazed against glazed — and they were badly wrong: with seven examples a class, the
    # model latched onto incidental character sequences and separated every contested pair by
    # 0.11 to 0.42 against a declared band of 0.08. It was confident precisely where the trade is
    # not, which is the failure this whole gate exists to catch.
    #
    # A word vectoriser makes the distinguishing feature the thing it actually is: the presence
    # of a word. A description that contains neither word of a pair then has nothing to push it
    # either way, which is the property being asked for rather than a tuning knob.
    model = make_pipeline(
        TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True, min_df=1),
        LogisticRegression(max_iter=2000, C=1.0, random_state=SEED),
    )
    model.fit(texts, labels)
    return model


def _held_out(texts: list[str], labels: list[str]) -> tuple[float, int]:
    from sklearn.model_selection import train_test_split  # noqa: PLC0415

    train_x, test_x, train_y, test_y = train_test_split(
        texts, labels, test_size=HELD_OUT, random_state=SEED, stratify=None
    )
    fitted = _fit(train_x, train_y)
    correct = sum(
        1 for text, label in zip(test_x, test_y, strict=True) if fitted.predict([text])[0] == label
    )
    return correct / len(test_y), len(test_y)


def _abstains(exported: dict, ambiguous: list[dict], margin: float) -> list[str]:
    """Every ambiguous description whose top two are further apart than the declared band.

    Scored through the **exported artefact**, not through the fitted object, because the exported
    artefact is what the endpoint runs. A gate applied to something other than the thing that
    ships is a gate that can pass while the deployed model fails it.
    """
    failures: list[str] = []
    for case in ambiguous:
        ranked = scores(exported, case["description"])
        gap = ranked[0]["score"] - ranked[1]["score"]
        if gap >= margin:
            failures.append(
                f"{case['description']!r} separates {case['between']} by {gap:.4f}, and the "
                f"declared band is {margin}. Nothing in that description resolves the pair, so a "
                f"model that separates them is confident where the trade is not"
            )
    return failures


def _export(model) -> dict:
    """The fitted pipeline as readable data. See `classification/artefact.py` for why.

    Everything scikit-learn needs at predict time and nothing it needs at fit time: the
    vocabulary, the inverse document frequencies, the coefficients and the intercepts. The result
    is a file somebody can open, diff between two fits, and read to see which words moved a
    heading — which a pickle is not.
    """
    vectoriser = model.named_steps["tfidfvectorizer"]
    regression = model.named_steps["logisticregression"]
    exported = {
        "version": ARTEFACT_VERSION,
        "fitted_from": "contracts/classification/training.yaml",
        "ngram_max": vectoriser.ngram_range[1],
        "sublinear_tf": bool(vectoriser.sublinear_tf),
        "vocabulary": {term: int(index) for term, index in vectoriser.vocabulary_.items()},
        "idf": [float(value) for value in vectoriser.idf_],
        "classes": [str(code) for code in regression.classes_],
        "coefficients": [[float(weight) for weight in row] for row in regression.coef_],
        "intercepts": [float(value) for value in regression.intercept_],
    }
    return exported


def _with_floor(exported: dict) -> dict:
    """Attach the derived floor, once the artefact is otherwise complete.

    Two steps rather than one because `prior()` scores through the artefact, so the artefact has
    to exist first. Recorded in the file so the number travels with the model that produced it —
    a floor kept anywhere else is a floor that stays behind when the model is refitted.
    """
    return {**exported, "minimum_score": prior(exported)}


def _paths_agree(model, exported: dict, texts: list[str]) -> list[str]:
    """The exported artefact scores every description the way scikit-learn does, or it is refused.

    **This is the price of not shipping a pickle, paid rather than waved at.** `artefact.py`
    re-implements tf-idf and the softmax by hand; a re-implementation that disagrees with the
    original is strictly worse than the pickle it replaced, because it would be wrong in a way
    nobody could see. So both paths score the same descriptions and must agree to `TOLERANCE`.

    It is claim 2's construction, applied to a model instead of a bounding box: a second path
    over the same input, and the artefact exists only if the paths agree.
    """
    problems: list[str] = []
    for text in texts:
        reference = dict(
            zip(
                (str(code) for code in model.classes_),
                (float(value) for value in model.predict_proba([text])[0]),
                strict=True,
            )
        )
        for candidate in scores(exported, text):
            difference = abs(candidate["score"] - reference[candidate["code"]])
            if difference > TOLERANCE:
                problems.append(
                    f"{text!r} scores {candidate['code']} at {candidate['score']:.9f} through "
                    f"the exported artefact and {reference[candidate['code']]:.9f} through "
                    f"scikit-learn — {difference:.2e} apart, and the tolerance is {TOLERANCE:.0e}"
                )
    return problems


def _package(exported: dict, destination: Path) -> Path:
    """`model.tar.gz` in the shape SageMaker's scikit-learn container expects."""
    staging = Path(tempfile.mkdtemp())
    (staging / ARTEFACT_NAME).write_text(json.dumps(exported, indent=1), encoding="utf-8")

    # `code/inference.py`, not `inference.py` at the root. The scikit-learn serving container
    # looks for the entry point under `SAGEMAKER_SUBMIT_DIRECTORY`, which
    # `infra/extraction/classification.tf` sets to `/opt/ml/model/code` — the documented layout.
    # At the root the archive still loads, the container falls back to its default handler, and
    # the endpoint answers with a bare prediction: a code and nothing else. That failure is
    # quiet, it looks like a working endpoint, and it is exactly the decision-inside-the-model
    # failure `inference.py` exists to prevent.
    code = staging / "code"
    code.mkdir()
    # Both files, because the container has no `manifest` package on its path. `inference.py`
    # imports `artefact` and falls back to the flat name when the package import fails, which is
    # the branch that runs in the estate.
    for module in ("inference.py", "artefact.py"):
        shutil.copy(ROOT / "src" / "manifest" / "classification" / module, code / module)

    archive = destination / "model.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for member in sorted(staging.iterdir()):
            tar.add(member, arcname=member.name)
    shutil.rmtree(staging, ignore_errors=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build")
    parser.add_argument("--upload-to", help="s3://bucket/key to put the artefact at.")
    arguments = parser.parse_args()

    texts, labels, ambiguous = _load()
    margin = _margin()
    print(
        f"{DIM}{len(texts)} descriptions, {len(set(labels))} headings, "
        f"{len(ambiguous)} ambiguous{RESET}"
    )

    accuracy, n = _held_out(texts, labels)
    print(
        f"  held out: {accuracy:.1%} of {n} examples. **A statement about "
        f"contracts/classification/training.yaml**, not about classifying tariff headings — "
        f"twelve headings against a nomenclature with five thousand, and this figure appears on "
        f"no scoreboard."
    )

    model = _fit(texts, labels)
    exported = _with_floor(_export(model))
    print(
        f"  {DIM}floor: {exported['minimum_score']:.4f} — the top score this model gives a "
        f"description it cannot read, derived rather than written (doctrine rule 3){RESET}"
    )

    contested = [case["description"] for case in ambiguous]
    disagreements = _paths_agree(model, exported, texts + contested)
    for disagreement in disagreements:
        print(f"  {RED}FAIL{RESET}  {disagreement}", file=sys.stderr)
    if disagreements:
        print(
            f"\n{len(disagreements)} score(s) differ between scikit-learn and the exported "
            f"artefact. No artefact written: a hand-written scorer that disagrees with the model "
            f"it was exported from is worse than the pickle it replaced, because it is wrong "
            f"where nobody is looking.",
            file=sys.stderr,
        )
        return 1
    print(
        f"  {GREEN}ok{RESET}    both paths agree on all {len(texts) + len(contested)} "
        f"descriptions, to {TOLERANCE:.0e}"
    )

    failures = _abstains(exported, ambiguous, margin)
    for failure in failures:
        print(f"  {RED}FAIL{RESET}  {failure}", file=sys.stderr)
    if failures:
        print(
            f"\n{len(failures)} contested description(s) were separated. No artefact written: an "
            f"endpoint that picks a winner on a pair professionals argue about is worse than no "
            f"endpoint, and this is the property the classifier exists to demonstrate.",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}ok{RESET}    all {len(ambiguous)} contested descriptions fall inside the band")

    arguments.out.mkdir(parents=True, exist_ok=True)
    archive = _package(exported, arguments.out)
    print(f"  {GREEN}ok{RESET}    {archive} ({archive.stat().st_size:,} bytes)")

    if arguments.upload_to:
        subprocess.run(  # noqa: S603
            ["aws", "s3", "cp", str(archive), arguments.upload_to],  # noqa: S607
            check=True,
        )
        print(f"  {GREEN}ok{RESET}    uploaded to {arguments.upload_to}")

    print(json.dumps({"held_out_accuracy": round(accuracy, 4), "held_out_n": n}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
