"""The training set is a contract, so it is checked like one.

**Nothing here fits anything.** These are the properties that make the fitted model mean what the
gate in `scripts/train_classifier.py` assumes it means — and every one of them is a way the file
could be edited into looking fine while the gate quietly stopped testing anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONTRACTS = Path("contracts/classification")
TRAINING = yaml.safe_load((CONTRACTS / "training.yaml").read_text(encoding="utf-8"))
HEADINGS = yaml.safe_load((CONTRACTS / "headings.yaml").read_text(encoding="utf-8"))

_DECLARED = {heading["code"] for heading in HEADINGS["headings"]}
_CONTESTED = {
    heading["code"]: set(heading.get("contested_with", ())) for heading in HEADINGS["headings"]
}


def test_every_trained_heading_is_a_heading_the_system_may_propose() -> None:
    """A model fitted on a code the contract does not declare answers with one the caller refuses.

    `handlers/classify.py` raises on an undeclared code rather than dropping it, so this would be
    an endpoint that fails at request time on a fault introduced at fit time.
    """
    assert set(TRAINING["descriptions"]) <= _DECLARED


def test_every_declared_heading_has_examples() -> None:
    """A heading with no examples can never be proposed, which makes it decoration in a contract."""
    assert set(TRAINING["descriptions"]) >= _DECLARED


@pytest.mark.parametrize("code", sorted(TRAINING["descriptions"]))
def test_no_heading_is_represented_by_one_or_two_lines(code: str) -> None:
    """Three is not a good number of examples. It is the number below which the fit is arbitrary.

    Stated as a floor rather than a target: the held-out figure this training set produces is
    already reported with its N and appears on no scoreboard.
    """
    assert len(TRAINING["descriptions"][code]) >= 3


@pytest.mark.parametrize(
    "case", TRAINING.get("ambiguous", ()), ids=lambda case: case["description"]
)
def test_every_contested_description_names_a_pair_the_contract_declares_contested(case) -> None:
    """The gate would otherwise pass on a pair nothing says is genuinely argued.

    An `ambiguous` entry naming two headings that are *not* declared contested is a description
    the model is being asked to abstain on for no stated reason — which is a band applied by
    preference, and doctrine rule 3 is about exactly that.
    """
    first, second = case["between"]
    assert second in _CONTESTED.get(first, set())
    assert first in _CONTESTED.get(second, set())


@pytest.mark.parametrize(
    "case", TRAINING.get("ambiguous", ()), ids=lambda case: case["description"]
)
def test_no_contested_description_is_also_a_training_example(case) -> None:
    """A description in both places is a gate testing whether a model memorised its own input.

    It would pass whenever the fit was good, which is the opposite of what the band is for.
    """
    every = {text for texts in TRAINING["descriptions"].values() for text in texts}
    assert case["description"] not in every


def test_every_contested_pair_has_a_contested_description() -> None:
    """A declared contest nothing tests is a rule that has never been shown to fire.

    The contract declares three pairs. If one of them had no `ambiguous` entry, the gate would
    still go green while the model separated it freely — the failure would be invisible and it
    would be in the part of the system that exists to be careful.
    """
    tested = {frozenset(case["between"]) for case in TRAINING.get("ambiguous", ())}
    declared = {frozenset((code, other)) for code, others in _CONTESTED.items() for other in others}
    assert declared <= tested


def test_the_training_set_states_where_it_came_from() -> None:
    """Every figure it produces is a statement about this file. That requires knowing what it is."""
    assert "by hand" in TRAINING["provenance"]
