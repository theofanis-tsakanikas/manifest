"""A published record becomes rows, and an abstention becomes a row with no value.

The two properties worth guarding are opposites of each other, which is why they are here
together: an unpublished reading must not reach the lake as a value, and the *row* must reach it
anyway. Drop the first and the warehouse holds readings no threshold approved; drop the second
and the queue is invisible to every query in the analytics layer, which is the one number a
thresholding system is judged on.
"""

from __future__ import annotations

import pytest

from manifest.core.lake import rows_for

WHEN = "2026-08-12T04:00:00Z"


def _record(**overrides: object) -> dict:
    record = {
        "document_id": "SHP00001",
        "fingerprint": "f" * 64,
        "reader": "tesseract-5.5.0",
        "fields": [
            {
                "field": "gross_weight",
                "value": "8959 KGS",
                "confidence": 0.957,
                "threshold": 0.9,
                "page": 1,
                "box": [0.1, 0.2, 0.3, 0.04],
                "publishable": True,
                "verdict": "verified",
            },
            {
                "field": "consignee",
                "value": "Northbridge Forwarding B.V.",
                "confidence": 0.41,
                "threshold": 0.9,
                "page": 1,
                "box": [0.1, 0.5, 0.3, 0.04],
                "publishable": False,
                "queued_because": "below_threshold",
                "verdict": "not_applicable",
            },
        ],
    }
    record.update(overrides)
    return record


def test_a_published_field_carries_its_value_and_the_threshold_it_met() -> None:
    published = rows_for(_record(), extracted_on=WHEN)[0]
    assert published.value == "8959 KGS"
    assert published.confidence == pytest.approx(0.957)
    assert published.threshold == pytest.approx(0.9), (
        "the threshold is stored, not looked up later. One that moved must not re-judge a "
        "record published under the old one"
    )
    assert published.provenance_verified is True


def test_an_abstention_is_a_row_with_no_value() -> None:
    queued = rows_for(_record(), extracted_on=WHEN)[1]
    assert queued.field == "consignee"
    assert queued.value is None, (
        "the reading did not clear its threshold, so it was never published. Landing it would "
        "put an unapproved value where downstream reads the customs record"
    )
    assert queued.confidence == pytest.approx(0.41), "what it read is still a fact worth keeping"
    assert queued.threshold == pytest.approx(0.9), "and so is the standard it failed to meet"
    assert queued.provenance_verified is False


def test_the_abstention_still_produces_a_row() -> None:
    """Delete this behaviour and the review queue disappears from every mart."""
    assert len(rows_for(_record(), extracted_on=WHEN)) == 2


def test_only_a_verified_verdict_counts_as_verified() -> None:
    """`refused`, `uncheckable` and `not_applicable` are three things, and none is a pass."""
    for verdict in ("refused", "uncheckable", "not_applicable", None):
        record = _record()
        record["fields"][0]["verdict"] = verdict
        assert rows_for(record, extracted_on=WHEN)[0].provenance_verified is False


def test_a_zero_confidence_survives_and_an_absent_one_does_not_become_zero() -> None:
    """Doctrine rule 3 in the mapping layer: missing is missing."""
    record = _record()
    record["fields"][0]["confidence"] = 0.0
    record["fields"][1]["confidence"] = None
    rows = rows_for(record, extracted_on=WHEN)
    assert rows[0].confidence == 0.0
    assert rows[1].confidence is None


def test_supersedes_is_supplied_rather_than_derived() -> None:
    rows = rows_for(_record(), extracted_on=WHEN, supersedes="e" * 64)
    assert {row.supersedes for row in rows} == {"e" * 64}
    assert {row.supersedes for row in rows_for(_record(), extracted_on=WHEN)} == {None}


@pytest.mark.parametrize("missing", ["document_id", "fingerprint"])
def test_a_record_with_no_key_is_refused(missing: str) -> None:
    record = _record()
    record[missing] = ""
    with pytest.raises(ValueError, match="supersede"):
        rows_for(record, extracted_on=WHEN)


def test_a_record_with_no_field_list_is_refused() -> None:
    with pytest.raises(ValueError, match="carries none"):
        rows_for(_record(fields=None), extracted_on=WHEN)
