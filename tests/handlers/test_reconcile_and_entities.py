"""Claims 4 and 6 as the estate performs them, rather than as `evals/` scores them offline.

The pure functions are proved elsewhere, over a corpus with planted mismatches and a set of
transliterated party names. What is tested here is the adapter: that it compares **published**
values only, that a disagreement becomes a review item rather than a log line, and that an
un-merge re-points what pointed at the merged entity.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from manifest.handlers import entities as entities_handler
from manifest.handlers import reconcile as reconcile_handler


def _record(document_id: str, document_type: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "fingerprint": f"{document_id}-v1",
        "document_type": document_type,
        "language": "en",
        "reader": "reference-ocr@tesseract 5.5.0",
        "fields": fields,
    }


#: A bill of lading and a packing list that disagree on gross weight by more than the contract's
#: tolerance, and agree on the container number.
BILL = _record(
    "SHP-BOL",
    "bill_of_lading",
    [
        {"field": "gross_weight", "value": "22728 KGS", "publishable": True},
        {"field": "container_number", "value": "MSKU1234567", "publishable": True},
        {"field": "consignee", "value": "Northbridge Forwarding B.V.", "publishable": True},
        {"field": "shipper", "value": None, "publishable": False, "queued_because": "unscored"},
    ],
)
PACKING = _record(
    "SHP-PL",
    "packing_list",
    [
        {"field": "gross_weight", "value": "19100 KGS", "publishable": True},
        {"field": "container_number", "value": "MSKU1234567", "publishable": True},
    ],
)

#: The same consignee, spelled the way a different party's system spells it. Parties live on the
#: bill of lading, the certificate of origin, the invoice and the declaration — never on a
#: packing list, which carries weights and counts and no names at all.
CERTIFICATE = _record(
    "SHP-COO",
    "certificate_of_origin",
    [{"field": "consignee", "value": "NORTHBRIDGE FORWARDING", "publishable": True}],
)

#: The same company again, abbreviated. It does **not** merge, and that is the property rather
#: than a limitation: `core.entities.score` merges on exact equality after a rule's
#: normalisations and never on similarity, so `FWD` against `Forwarding` is at most a candidate
#: for a human. A system that merged it would be attaching shipments to companies on a guess.
INVOICE = _record(
    "SHP-INV",
    "commercial_invoice",
    [
        {"field": "buyer", "value": "NORTHBRIDGE FWD BV", "publishable": True},
        {"field": "seller", "value": "Hellenic Marble S.A.", "publishable": True},
    ],
)


class _Store:
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.puts: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        for name, record in self.records.items():
            if key.endswith(f"records/{name}/{record['fingerprint']}.json"):
                return {"Body": _Body(json.dumps(record).encode())}
        for put in reversed(self.puts):
            if put["Key"] == key:
                return {"Body": _Body(put["Body"])}
        raise KeyError(key)

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _Queue:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(json.loads(kwargs["MessageBody"]))
        return {}


DOCUMENTS = [
    {"document_id": "SHP-BOL", "version": "SHP-BOL-v1"},
    {"document_id": "SHP-PL", "version": "SHP-PL-v1"},
    {"document_id": "SHP-COO", "version": "SHP-COO-v1"},
    {"document_id": "SHP-INV", "version": "SHP-INV-v1"},
]


@pytest.fixture
def estate(monkeypatch: pytest.MonkeyPatch) -> tuple[_Store, _Queue]:
    store, queue = (
        _Store({"SHP-BOL": BILL, "SHP-PL": PACKING, "SHP-COO": CERTIFICATE, "SHP-INV": INVOICE}),
        _Queue(),
    )
    monkeypatch.setenv("RECORDS_BUCKET", "manifest-records-111111111111")
    monkeypatch.setenv("REVIEW_QUEUE_URL", "https://sqs.eu-central-1.amazonaws.com/1/manifest")
    monkeypatch.setenv("DATA_KEY_ARN", "arn:aws:kms:eu-central-1:111111111111:key/abc")
    monkeypatch.setenv("CONTRACTS_DIR", "contracts")
    for module in (reconcile_handler, entities_handler):
        monkeypatch.setattr(module, "_client", lambda name: queue if name == "sqs" else store)
    return store, queue


# ── Claim 4 ───────────────────────────────────────────────────────────────────


def test_a_weight_that_disagrees_is_found_and_sent_to_a_human(estate) -> None:
    """The operator is paid to catch this, which makes it a review item rather than telemetry."""
    _, queue = estate

    answer = reconcile_handler.handler({"shipment": "SHP00001", "documents": DOCUMENTS})

    assert answer["disagreements"] >= 1
    weights = [f for f in answer["findings"] if "gross_weight" in f["rule"]]
    assert weights and weights[0]["outcome"] == "disagree"
    assert any(item["reason"] == "disagreement" for item in queue.sent)


def test_a_container_number_that_agrees_produces_no_finding(estate) -> None:
    """Zero false positives on the set that agrees is half of what claim 4 asserts."""
    answer = reconcile_handler.handler({"shipment": "SHP00001", "documents": DOCUMENTS})

    containers = [f for f in answer["findings"] if "container" in f["rule"]]
    assert containers
    assert all(f["outcome"] != "disagree" for f in containers)


def test_an_abstained_field_is_absent_rather_than_compared(estate) -> None:
    """A value nobody published is not evidence, and a disagreement built on one is manufactured."""
    answer = reconcile_handler.handler({"shipment": "SHP00001", "documents": DOCUMENTS})

    for finding in answer["findings"]:
        if finding["left"]["document"] == "bill_of_lading" and "shipper" in finding["rule"]:
            assert finding["outcome"] != "disagree"


def test_reconciling_one_document_is_refused(estate) -> None:
    with pytest.raises(reconcile_handler.HandlerError, match="not a comparison"):
        reconcile_handler.handler({"shipment": "SHP00001", "documents": []})


def test_a_document_named_without_a_version_is_refused(estate) -> None:
    """'Whatever is current' would compare a shipment against a moving target."""
    with pytest.raises(reconcile_handler.HandlerError, match="version"):
        reconcile_handler.handler(
            {"shipment": "SHP00001", "documents": [{"document_id": "SHP-BOL"}]}
        )


# ── Claim 6 ───────────────────────────────────────────────────────────────────


def test_two_spellings_of_one_party_resolve_together(estate) -> None:
    """`Northbridge Forwarding B.V.` and `NORTHBRIDGE FORWARDING`: the same after the legal form."""
    answer = entities_handler.handler(
        {"action": "resolve", "shipment": "SHP00001", "documents": DOCUMENTS}
    )

    assert answer["mentions"] == 4
    merged = [e for e in answer["resolved"] if e["merged"]]
    assert merged, "the two spellings that differ only by a legal form must resolve together"
    assert {"SHP-BOL#consignee", "SHP-COO#consignee"} <= set(merged[0]["members"])


def test_an_abbreviation_is_not_merged_however_similar_it_looks(estate) -> None:
    """**Similarity never merges.** `FWD` against `Forwarding` is a candidate for a human.

    This is the decision with the most risk in `core.entities` and the one worth an estate-level
    test: a merge attaches every shipment of one party to another, and a system that did it on a
    resemblance would be wrong in a way nobody notices until an invoice goes to the wrong company.
    """
    answer = entities_handler.handler(
        {"action": "resolve", "shipment": "SHP00001", "documents": DOCUMENTS}
    )

    for entity in answer["resolved"]:
        if "SHP-INV#buyer" in entity["members"]:
            assert entity["members"] == ["SHP-INV#buyer"], "an abbreviation was merged"


def test_the_resolved_state_keeps_the_mentions_and_the_references(estate) -> None:
    """**This is the reversibility.** Storing only entity ids would make an un-merge a re-run."""
    store, _ = estate

    entities_handler.handler({"action": "resolve", "shipment": "SHP00001", "documents": DOCUMENTS})

    state = json.loads(store.puts[-1]["Body"])
    assert state["mentions"] and state["references"]


def test_an_unmerge_splits_the_entity_and_repoints_what_pointed_at_it(estate) -> None:
    """The half nobody builds: a partial answer leaves a pointer nobody follows until it breaks."""
    store, _ = estate
    resolved = entities_handler.handler(
        {"action": "resolve", "shipment": "SHP00001", "documents": DOCUMENTS}
    )
    merged = next(e for e in resolved["resolved"] if e["merged"])

    undone = entities_handler.handler(
        {"action": "unmerge", "shipment": "SHP00001", "entity_id": merged["entity_id"]}
    )

    assert undone["removed"] == merged["entity_id"]
    assert len(undone["replacements"]) == len(merged["members"])
    assert set(undone["repointed"]) == set(merged["members"])
    state = json.loads(store.puts[-1]["Body"])
    assert state["unmerged"], "the removed entity is recorded rather than forgotten"


def test_an_unmerge_of_something_never_resolved_is_refused(estate) -> None:
    with pytest.raises(entities_handler.HandlerError, match="no record of one"):
        entities_handler.handler(
            {"action": "unmerge", "shipment": "NEVER", "entity_id": "whatever"}
        )


def test_a_third_action_does_not_exist(estate) -> None:
    with pytest.raises(entities_handler.HandlerError, match="no third thing"):
        entities_handler.handler({"action": "merge", "shipment": "SHP00001"})
