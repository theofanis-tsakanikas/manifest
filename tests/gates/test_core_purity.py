"""The gate that keeps every other claim checkable on a laptop.

Two halves. The first asserts the real core is clean — that is the property, and it is
asserted here as well as in `scripts/check_core_is_pure.py` so that `make test` alone would
catch a regression. The second asserts the gate *bites*, on synthetic modules written to break
it, because a scanner that finds nothing is indistinguishable from a scanner that looks for
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manifest.gates.core_purity import report, scan

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src" / "manifest" / "core"


@pytest.mark.gate
def test_the_real_core_is_clean() -> None:
    findings = scan(CORE, ROOT)
    assert findings == [], report(findings)


def _scan_source(tmp_path: Path, source: str, name: str = "suspect.py") -> list[str]:
    """Write one module into a stand-in core and return the rules it trips.

    The layout mirrors the real one — `src/<package>/core/` — because the gate resolves
    relative imports by counting path components, and a flat temporary directory would let
    that half of it pass without being exercised.
    """
    core = tmp_path / "src" / "manifest" / "core"
    core.mkdir(parents=True)
    (core / name).write_text(source, encoding="utf-8")
    return [finding.rule for finding in scan(core, tmp_path)]


@pytest.mark.gate
def test_a_cloud_sdk_import_is_refused(tmp_path: Path) -> None:
    assert "import" in _scan_source(tmp_path, "import boto3\n")


@pytest.mark.gate
def test_a_third_party_import_is_refused(tmp_path: Path) -> None:
    """Not a denylist. Anything outside the standard library, including our own dependencies.

    A denylist of known engines would pass the first wheel nobody thought of, and the whole
    value of the rule is that it holds for the engine that has not been written yet.
    """
    assert "import" in _scan_source(tmp_path, "import numpy\n")


@pytest.mark.gate
def test_reaching_back_into_the_package_is_refused(tmp_path: Path) -> None:
    assert "import" in _scan_source(tmp_path, "from manifest.extraction import read\n")


@pytest.mark.gate
def test_a_relative_import_climbing_out_of_the_core_is_refused(tmp_path: Path) -> None:
    assert "import" in _scan_source(tmp_path, "from ..extraction import read\n")


@pytest.mark.gate
def test_a_relative_import_inside_the_core_is_allowed(tmp_path: Path) -> None:
    assert _scan_source(tmp_path, "from .geometry import Box\n") == []


@pytest.mark.gate
def test_a_dynamic_import_is_refused(tmp_path: Path) -> None:
    """The rule with one indirection in front of it."""
    assert "import" in _scan_source(tmp_path, "import importlib\n")


@pytest.mark.gate
@pytest.mark.parametrize(
    "source",
    [
        "import datetime\n\n\ndef stamp():\n    return datetime.datetime.now()\n",
        "import os\n\n\ndef region():\n    return os.getenv('AWS_REGION')\n",
        "import os\n\n\ndef region():\n    return os.environ['REGION']\n",
        "import uuid\n\n\ndef ident():\n    return uuid.uuid4()\n",
        "def load(path):\n    return open(path).read()\n",
    ],
    ids=["clock", "environment-call", "environment-read", "randomness", "filesystem"],
)
def test_ambient_state_is_refused(tmp_path: Path, source: str) -> None:
    """Each of these makes a re-extraction differ from the extraction it reproduces.

    None of them raises, none of them logs, and none of them fails a test written by the person
    who added it. That is why the check reads the syntax tree instead of trusting review.
    """
    assert "ambient state" in _scan_source(tmp_path, source)


@pytest.mark.gate
def test_a_method_of_our_own_called_now_is_refused_too(tmp_path: Path) -> None:
    """Deliberate, and the reason the match is on the bare name.

    `ExtractedAt.now()` is the most natural API in the world. Matching only `datetime.now`
    would let the rule be defeated by a helper somebody wrote to be tidy.
    """
    source = "class ExtractedAt:\n    @staticmethod\n    def now():\n        return 0\n\n\nX = ExtractedAt.now()\n"  # noqa: E501
    assert "ambient state" in _scan_source(tmp_path, source)


@pytest.mark.gate
@pytest.mark.parametrize(
    "source",
    [
        "# escalate to textract when confidence is low\nVALUE = 1\n",
        'ENGINE = "tesseract"\n',
        "ROUTES = {'bedrock': 2}\n",
        "def normalise(block):\n    '''Maps a Comprehend entity to ours.'''\n    return block\n",
    ],
    ids=["comment", "string", "dict-key", "docstring"],
)
def test_an_engine_named_anywhere_is_refused(tmp_path: Path, source: str) -> None:
    """A text scan rather than an AST scan, on purpose.

    None of these four is an import, and each is a way the name actually gets in. The moment
    the core can tell which engine produced a value, the normalised representation has stopped
    being the contract with the cloud and become a suggestion — and the claim that a local
    engine and a managed service are interchangeable behind it is no longer true.
    """
    assert "engine name" in _scan_source(tmp_path, source)


@pytest.mark.gate
def test_the_engine_scan_matches_whole_words_only(tmp_path: Path) -> None:
    """`abbreviation` contains no engine, and a gate with false positives gets switched off."""
    assert _scan_source(tmp_path, "ABBREVIATIONS = ('BV', 'GmbH')\nSURYAN = 1\n") == []


@pytest.mark.gate
def test_a_module_the_gate_cannot_parse_is_a_finding(tmp_path: Path) -> None:
    """Not a skip. A module the gate cannot read is a module the gate is not checking, and
    silently skipping it is how a scanner reports green over the one file that broke."""
    assert "unparseable" in _scan_source(tmp_path, "def broken(:\n")


@pytest.mark.gate
def test_every_violation_is_reported_not_only_the_first(tmp_path: Path) -> None:
    """A caller who learns the gate one refusal at a time gives up before the end."""
    rules = _scan_source(tmp_path, "import numpy\nimport socket\nimport importlib\n")
    assert len(rules) == 3


@pytest.mark.gate
def test_one_line_can_break_two_rules_and_reports_both(tmp_path: Path) -> None:
    """`import boto3` is a non-standard-library import *and* a named engine.

    Reporting it once would mean a reader who deleted the import believed they had satisfied
    the gate, when what they had done was satisfy one of the two rules it trips.
    """
    assert sorted(_scan_source(tmp_path, "import boto3\n")) == ["engine name", "import"]


def test_the_report_explains_what_was_given_up(tmp_path: Path) -> None:
    core = tmp_path / "src" / "manifest" / "core"
    core.mkdir(parents=True)
    (core / "suspect.py").write_text("import boto3\n", encoding="utf-8")
    text = report(scan(core, tmp_path))
    assert "boto3" in text
    assert "adapter" in text
