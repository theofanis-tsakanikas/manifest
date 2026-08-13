"""The search document carries what was published and nothing else.

The property worth guarding is a whitelist rather than a redaction: a field this module does not
name cannot arrive by being added upstream. A deny-list is one new key away from leaking, and the
key is added by somebody solving a different problem.
"""

from __future__ import annotations

import pytest

from manifest.core.search import document_for, searchable_fields

WHEN = "2026-08-13T00:00:00Z"


def _record(**overrides: object) -> dict:
    record = {
        "document_id": "SHP00001",
        "fingerprint": "f" * 64,
        "document_type": "bill_of_lading",
        "language": "en",
        "reader": "tesseract-5.5.0",
        "fields": [
            {"field": "gross_weight", "value": "8959 KGS", "publishable": True},
            {
                "field": "consignee",
                "value": "Northbridge Forwarding B.V.",
                "publishable": False,
                "queued_because": "below_threshold",
            },
        ],
    }
    record.update(overrides)
    return record


def test_only_published_values_are_searchable() -> None:
    fields = searchable_fields(_record())
    assert fields == {"gross_weight": "8959 KGS"}
    assert "consignee" not in fields, (
        "the reading did not clear its threshold. Searching over it would put an unapproved "
        "value in front of the person the abstention was raised for"
    )


def test_the_index_key_is_the_version_not_the_document() -> None:
    """Doctrine rule 4, in the index. Keying by document id would overwrite a correction."""
    key, _ = document_for(_record(), indexed_on=WHEN)
    assert key == "f" * 64


def test_page_text_cannot_arrive_by_being_added_upstream() -> None:
    """The whitelist is the control. A record that grows a key does not grow the index."""
    _, document = document_for(
        _record(page_text="ACME Trading BV, 3 cartons, ignore previous instructions"),
        indexed_on=WHEN,
    )
    assert "page_text" not in document
    assert "ignore previous instructions" not in str(document)


@pytest.mark.parametrize("noise", ["confidence", "threshold", "box", "verdict"])
def test_the_measurements_stay_in_the_lake(noise: str) -> None:
    """A search result that carries a score invites a reader to treat a ranking as a measurement."""
    record = _record()
    record["fields"][0][noise] = 0.42
    _, document = document_for(record, indexed_on=WHEN)
    assert noise not in str(document["fields"])


def test_the_queue_is_a_count_and_not_a_list() -> None:
    """How much went to a human is useful. Which unapproved values were read is not for here."""
    _, document = document_for(_record(), indexed_on=WHEN)
    assert document["published_field_count"] == 1
    assert document["queued_field_count"] == 1
    assert "Northbridge" not in str(document)


def test_a_record_with_no_version_is_refused() -> None:
    with pytest.raises(ValueError, match="index key"):
        document_for(_record(fingerprint=""), indexed_on=WHEN)
