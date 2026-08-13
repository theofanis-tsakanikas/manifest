"""The recorded human decision, which is the half of claim 5 nobody builds.

Every test here is about a property the pure function already has and the *estate* has never
demonstrated: that a field below threshold does not publish without a decision, that a decision
is written whatever it decided, and that the one door with no key stays shut.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from manifest.handlers import decide

RECORD = {
    "document_id": "SHP00001",
    "fingerprint": "a" * 32,
    "reader": "reference-ocr@tesseract 5.5.0",
    "document_type": "bill_of_lading",
    "language": "en",
    "contract_version": 1,
    "source_digest": "d" * 32,
    "fields": [
        {
            "field": "gross_weight",
            "value": "8959 KGS",
            "confidence": 0.97,
            "threshold": 0.9,
            "page": 1,
            "box": [0.1, 0.2, 0.3, 0.04],
            "publishable": True,
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
        },
        {
            "field": "shipper",
            "value": None,
            "confidence": 0.0,
            "threshold": 0.9,
            "page": 1,
            "box": None,
            "publishable": False,
            "queued_because": "no_provenance",
        },
    ],
}


class _Store:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.puts: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        if not kwargs["Key"].endswith(f"{self.record['fingerprint']}.json"):
            raise KeyError(kwargs["Key"])
        return {"Body": _Body(json.dumps(self.record).encode())}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _Table:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        self.rows.append(kwargs["Item"])
        return {}


@pytest.fixture
def estate(monkeypatch: pytest.MonkeyPatch) -> tuple[_Store, _Table]:
    store, table = _Store(RECORD), _Table()
    monkeypatch.setenv("RECORDS_BUCKET", "manifest-records-111111111111")
    monkeypatch.setenv("DECISIONS_TABLE", "manifest-review-decisions")
    monkeypatch.setenv("DATA_KEY_ARN", "arn:aws:kms:eu-central-1:111111111111:key/abc")
    monkeypatch.setattr(decide, "_client", lambda name: store if name == "s3" else table)
    return store, table


def _decide(**overrides: Any) -> dict[str, Any]:
    return decide.handler(
        {
            "document_id": "SHP00001",
            "version": "a" * 32,
            "field": "consignee",
            "reviewer": "eirini@piraeus",
            "decision": "approved",
            "seconds_on_task": 34,
            **overrides,
        }
    )


def test_an_approval_publishes_a_new_version_and_never_edits_the_old_one(estate) -> None:
    """Doctrine rule 4. The old object is untouched and the new one names it."""
    store, _ = estate

    answer = _decide()

    assert answer["published"] is True
    assert answer["supersedes"] == "a" * 32
    assert answer["version"] != "a" * 32
    assert len(store.puts) == 1
    written = json.loads(store.puts[0]["Body"])
    assert written["supersedes"] == "a" * 32
    assert store.puts[0]["Key"].endswith(f"{written['fingerprint']}.json")


def test_the_same_decision_twice_produces_the_same_version(estate) -> None:
    """The version is derived from content, so a repeat lands nothing new.

    The same property re-reading identical bytes has, arrived at the same way — which is what
    makes a reviewer clicking twice cost a lookup instead of a duplicate record.
    """
    first, second = _decide(), _decide()

    assert first["version"] == second["version"]


def test_a_field_with_no_provenance_cannot_be_approved_into_existence(estate) -> None:
    """**The one door with no key.** Doctrine rule 7, on the estate rather than in a unit test.

    Nobody — including an approver — has the information the approval would be about. A reviewer
    may *supply* a value, which is a new value with their provenance; they may not approve one
    the system cannot point to on a page.
    """
    _, table = estate

    answer = _decide(field="shipper", decision="approved")

    assert answer["published"] is False
    assert "nothing to approve" in answer["reason"]
    assert table.rows, "the refusal is still a recorded decision"


def test_the_same_field_may_be_supplied_even_though_it_may_not_be_approved(estate) -> None:
    """The asymmetry that makes rule 7 a rule rather than a wall."""
    answer = _decide(field="shipper", decision="supplied", value="Northbridge Forwarding B.V.")

    assert answer["published"] is True


def test_a_rejection_publishes_nothing_and_is_still_written_down(estate) -> None:
    """A table holding only approvals makes every agreement rate 100% by construction."""
    store, table = estate

    answer = _decide(decision="rejected")

    assert answer["published"] is False
    assert store.puts == []
    assert table.rows[0]["decision"]["S"] == "rejected"
    assert table.rows[0]["agreed_with_model"]["BOOL"] is False


def test_agreement_is_computed_and_not_taken_from_the_caller(estate) -> None:
    """A reviewer client supplying its own agreement flag is marking its own homework."""
    _, table = estate

    _decide(decision="corrected", value="Northbridge Forwarding BV", agreed_with_model=True)

    assert table.rows[0]["agreed_with_model"]["BOOL"] is False


def test_time_on_task_is_recorded_because_a_rate_needs_a_denominator(estate) -> None:
    _, table = estate

    _decide(seconds_on_task=4)

    assert table.rows[0]["seconds_on_task"]["N"] == "4"


def test_a_field_that_published_on_its_own_score_is_refused(estate) -> None:
    """A decision recorded against it is evidence of oversight that was never needed."""
    with pytest.raises(decide.HandlerError, match="nothing waiting"):
        _decide(field="gross_weight")


def test_the_queue_reason_comes_from_the_record_and_not_from_the_request(estate) -> None:
    """Otherwise a client could call a no-provenance field `below_threshold` and approve it."""
    answer = _decide(field="shipper", decision="approved", reason="below_threshold")

    assert answer["published"] is False


def test_a_decision_naming_a_version_that_does_not_exist_is_refused(estate) -> None:
    with pytest.raises(decide.HandlerError, match="nothing at that version"):
        _decide(version="f" * 32)


def test_an_unknown_decision_word_is_refused_rather_than_coerced(estate) -> None:
    with pytest.raises(decide.HandlerError, match="not a decision"):
        _decide(decision="looks_fine")


def test_the_published_version_records_who_decided_it(estate) -> None:
    """A version published on a human's decision was not produced by the reader alone."""
    store, _ = estate

    _decide()

    written = json.loads(store.puts[0]["Body"])
    assert "review:eirini@piraeus" in written["reader"]
    decided = next(f for f in written["fields"] if f["field"] == "consignee")
    assert decided["publishable"] is True
    assert decided["queued_because"] is None
    assert decided["decided_by"] == "eirini@piraeus"
