"""The entry point works with only `code/` on the path — the way the container runs it.

**The failure this catches is silent and complete.** SageMaker's scikit-learn container puts
`SAGEMAKER_SUBMIT_DIRECTORY` on `sys.path` and nothing else: there is no `manifest` package in
that image. `inference.py` imports the scorer through the package and falls back to the flat
name, and the fallback is the branch that runs in the estate — so it is the branch no ordinary
test exercises, in the file where a mistake means every request returns 500 after a deploy that
reported success.

Importing it here under a name that shadows the package is the cheapest honest simulation: it
runs offline, it needs no container, and it fails for the same reason the endpoint would.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

SOURCE = Path("src/manifest/classification")

#: The smallest artefact `scores` will accept, so this file tests the layout and not the model.
ARTEFACT = {
    "version": 1,
    "ngram_max": 1,
    "sublinear_tf": True,
    "vocabulary": {"glazed": 0, "tiles": 1},
    "idf": [2.0, 1.0],
    "classes": ["690722", "690721"],
    "coefficients": [[3.0, 0.0], [-3.0, 0.0]],
    "intercepts": [0.0, 0.0],
    "minimum_score": 0.5,
}


@pytest.fixture
def container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`model.tar.gz` unpacked, with the `manifest` package unreachable."""
    code = tmp_path / "code"
    code.mkdir()
    for module in ("inference.py", "artefact.py"):
        shutil.copy(SOURCE / module, code / module)
    (tmp_path / "classifier.json").write_text(json.dumps(ARTEFACT), encoding="utf-8")

    # The package import must fail the way it fails in the image. Removing it from `sys.modules`
    # is not enough — it is installed here — so the name is poisoned for the duration.
    monkeypatch.setitem(sys.modules, "manifest.classification.artefact", None)
    monkeypatch.syspath_prepend(str(code))
    for name in ("inference", "artefact"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    specification = importlib.util.spec_from_file_location("inference", code / "inference.py")
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, "inference", module)
    specification.loader.exec_module(module)
    return module, tmp_path


def test_the_four_entry_points_run_end_to_end_without_the_package(container) -> None:
    inference, model_dir = container

    model = inference.model_fn(str(model_dir))
    goods = inference.input_fn(json.dumps({"goods": "Glazed tiles"}))
    answer = json.loads(inference.output_fn(inference.predict_fn(goods, model)))

    assert answer["candidates"][0]["code"] == "690722"
    assert answer["decided"] is False
    assert answer["minimum_score"] == ARTEFACT["minimum_score"]


def test_the_payload_carries_the_gap_the_band_is_declared_on(container) -> None:
    inference, model_dir = container
    model = inference.model_fn(str(model_dir))
    answer = json.loads(
        inference.output_fn(inference.predict_fn(inference.input_fn('{"goods": "tiles"}'), model))
    )

    assert answer["gap"] == pytest.approx(0.0, abs=1e-9)


def test_a_content_type_the_endpoint_does_not_serve_is_refused(container) -> None:
    inference, _ = container

    with pytest.raises(ValueError, match="not supported"):
        inference.input_fn("<goods/>", content_type="application/xml")


def test_an_empty_description_is_refused_at_the_container(container) -> None:
    """The caller refuses it too. Both, because either can be the one that is called."""
    inference, _ = container

    with pytest.raises(ValueError, match="ranks nothing"):
        inference.input_fn(json.dumps({"goods": ""}))
