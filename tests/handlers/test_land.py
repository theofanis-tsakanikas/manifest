"""The landing adapter — what it writes, and what it refuses to write twice.

The idempotence tests here exist because the estate proved they were missing: three runs of one
document put twenty-seven rows in the table for nine fields, and nothing failed.
"""

from __future__ import annotations

from manifest.handlers import land

#: One published record: two fields that cleared their thresholds and one that did not. An
#: abstention lands as a row with a null value — dropping it would hide the review queue from
#: every query in the analytics layer, which is the number a thresholding system is judged on.
RECORD = {
    "document_id": "SHP00001",
    "fingerprint": "f" * 64,
    "reader": "reference-ocr@tesseract 5.5.0",
    "document_type": "bill_of_lading",
    "language": "en",
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
            "field": "vessel_name",
            "value": "MSC ARIADNE",
            "confidence": 0.94,
            "threshold": 0.9,
            "page": 1,
            "box": [0.1, 0.3, 0.3, 0.04],
            "publishable": True,
            "verdict": "verified",
        },
        {
            "field": "consignee",
            "value": None,
            "confidence": 0.41,
            "threshold": 0.9,
            "page": 1,
            "box": [0.1, 0.5, 0.3, 0.04],
            "publishable": False,
            "queued_because": "below the derived threshold",
        },
    ],
}

# ── Idempotence, which the estate proved was missing ──────────────────────────


def test_a_version_already_in_the_table_lands_nothing(monkeypatch) -> None:
    """The same document read twice is the same version, and this appended anyway.

    A fingerprint is a function of the bytes and the reader — claim 3 — so three runs of one
    document produce one version and produced twenty-seven rows for nine fields. Nothing failed:
    every count in the analytics layer was a multiple of the truth, including the abstention
    counts claim 5 is judged on.
    """
    inserted: list[str] = []
    monkeypatch.setattr(land, "_already_landed", lambda *_args: True)
    monkeypatch.setattr(land, "_execute", inserted.append)
    monkeypatch.setattr(land, "_previous_version", lambda *_args: None)
    monkeypatch.setenv("GLUE_DATABASE", "manifest")
    monkeypatch.setenv("LAKE_TABLE", "document_version")
    monkeypatch.setenv("RECORDS_BUCKET", "manifest-records-111111111111")

    answer = land.handler({"record": RECORD})

    assert answer["landed"] == 0
    assert "already in the table" in answer["skipped"]
    assert inserted == []


def test_a_version_not_in_the_table_lands_every_field(monkeypatch) -> None:
    """Including the abstentions, as rows with a null value."""
    inserted: list[str] = []
    monkeypatch.setattr(land, "_already_landed", lambda *_args: False)
    monkeypatch.setattr(land, "_execute", inserted.append)
    monkeypatch.setattr(land, "_previous_version", lambda *_args: None)
    monkeypatch.setenv("GLUE_DATABASE", "manifest")
    monkeypatch.setenv("LAKE_TABLE", "document_version")
    monkeypatch.setenv("RECORDS_BUCKET", "manifest-records-111111111111")

    answer = land.handler({"record": RECORD})

    assert answer["landed"] == len(RECORD["fields"])
    assert len(inserted) == 1
