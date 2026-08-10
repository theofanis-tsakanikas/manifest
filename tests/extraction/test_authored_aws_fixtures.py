"""The managed adapter maps the documented shape — from AUTHORED fixtures, never captured.

Read `tests/extraction/fixtures/AUTHORED.md` first. No call has ever been made to an AWS
service from this repository, so these fixtures were written from the documented schema. That
makes them evidence about the adapter and about the documentation being read correctly, and it
does not make them evidence about the service. The test names say so, because a reader
skimming a passing suite should not be able to come away with the larger claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manifest.core.geometry import PageSize
from manifest.extraction.aws.textract import ResponseError, to_document

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = {1: PageSize(width=2480, height=3508)}


def _response(name: str = "authored_ocr_response.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _document(response: dict | None = None):
    return to_document(
        source_id="SHP00001/bill_of_lading",
        source_digest="abc123",
        response=response if response is not None else _response(),
        page_sizes=PAGES,
        language="en",
        service_version="2026-08-09",
    )


def test_the_authored_response_maps_to_the_normalised_representation() -> None:
    reading = _document()
    assert [line.text for line in reading.page(1).lines] == ["GROSS WEIGHT", "27000 KGS"]


def test_confidence_is_divided_by_the_documented_scale_and_not_rescaled() -> None:
    """0–100 becomes a fraction. Arithmetic, not calibration — ADR-0004 forbids mapping two
    readers' scores into a common range, because their 0.8 are different events."""
    words = {word.text: word for word in reading.words} if (reading := _document()) else {}
    assert words["27000"].confidence == pytest.approx(0.712)


def test_geometry_is_used_as_documented_without_conversion() -> None:
    """`BoundingBox.Left` is documented as a ratio of page width with a top-left origin, which
    is what `core.geometry.Box` already is. The adapter renames; the tier-0 adapter divides."""
    word = next(word for word in _document().words if word.text == "27000")
    assert word.box.left == pytest.approx(0.08)
    assert word.box.top == pytest.approx(0.22)


def test_a_line_is_rebuilt_from_its_word_children_not_from_its_own_text() -> None:
    """Reading the LINE's own `Text` is easier and loses the per-word confidence claim 1 is
    derived from. The line's confidence here is its weakest word's, not the line block's 71.2."""
    line = _document().page(1).lines[1]
    assert [word.text for word in line.words] == ["27000", "KGS"]
    assert line.confidence == pytest.approx(0.712)


def test_block_types_this_system_does_not_use_are_ignored_rather_than_refused() -> None:
    """The fixture carries KEY_VALUE_SET blocks. A future need for them is a change to the
    representation, not a discovery about the adapter."""
    assert len(_document().page(1).lines) == 2


def test_a_response_that_is_not_the_documented_shape_is_refused() -> None:
    with pytest.raises(ResponseError, match="documented shape"):
        _document({"Nothing": []})


def test_a_word_with_no_geometry_is_refused_rather_than_dropped() -> None:
    """An adapter that quietly drops what it did not understand produces a reading short by an
    unknown amount, and nothing downstream can tell that from a page with less text on it."""
    response = _response()
    for block in response["Blocks"]:
        if block["Id"] == "word-3":
            del block["Geometry"]
    with pytest.raises(ResponseError, match="BoundingBox"):
        _document(response)


def test_a_page_with_no_declared_size_is_refused() -> None:
    """The response gives fractions and never states the raster's pixel dimensions. A default
    here would bake a made-up page size into every provenance record."""
    response = _response()
    response["Blocks"][2]["Page"] = 4
    with pytest.raises(ResponseError, match="no size was given"):
        _document(response)


def test_every_fixture_in_this_directory_declares_how_it_was_obtained() -> None:
    """The claim this directory is allowed to make, asserted over **every** file in it.

    Written as a sweep rather than as an assertion about one known fixture, because the failure
    it guards against is a *new* fixture arriving without the label — which is exactly what a
    file-by-file version would miss. If somebody does capture a real response one day, this is
    where the label has to change, and decision 14 permits that in one direction only: the note
    must match how the file was actually obtained.
    """
    fixtures = sorted(FIXTURES.glob("*.json"))
    assert len(fixtures) >= 3, "the three documented shapes each have a fixture"
    for fixture in fixtures:
        note = json.loads(fixture.read_text(encoding="utf-8")).get("_note", "")
        assert "AUTHORED" in note, f"{fixture.name} does not say how it was obtained"
        assert "read 20" in note, (
            f"{fixture.name} does not say *when* its source documentation was read. "
            f"Documentation moves; a fixture with no date cannot be checked against a later "
            f"version of the schema it claims to follow"
        )
    assert "no response has ever been captured" in (
        (FIXTURES / "AUTHORED.md").read_text(encoding="utf-8").lower()
    )
