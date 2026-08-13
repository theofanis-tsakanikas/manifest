"""The cascade climbs one rung at a time, and it used to take one step and stop.

**The defect this file exists for.** `core.cascade.route` returns `min(higher)` — the cheapest
tier above the current one — which is the right rule. The handler called it once, with
`current_tier=0`, read at whatever came back, and returned. For English, `min(higher)` is always
1, so `eligible_tiers: [0, 1, 2, 3]` described a ladder the estate climbed one rung of: tier 2
was reachable in the contract and unreachable in the code, and tier 3 only for the languages
where 3 is the *first* rung.

Nothing failed. Every escalation this estate ever performed reported `reader_tier: 1` and looked
exactly like a cascade working — which is why it took a question from outside to notice.

`current_tier` has been a parameter of `route` since the first version of `core.cascade` and
nothing ever passed it anything but zero. These tests are what makes that parameter mean
something.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manifest.handlers import escalate

#: Two fields on page one: one that published at tier 0 and one that did not. The second is the
#: only one that may cost anything.
FIELDS = [
    {"field": "gross_weight", "value": "8959 KGS", "confidence": 0.97, "page": 1},
    {
        "field": "consignee",
        "value": None,
        "confidence": 0.41,
        "page": 1,
        "queued_because": "below the derived threshold",
    },
]

EVENT = {
    "extraction": {
        "outcome": {
            "document_id": "SHP00001",
            "document_type": "bill_of_lading",
            "language": "en",
            "fingerprint": "f" * 64,
            "fields": FIELDS,
            "publishable_count": 1,
            "queued_count": 1,
        }
    },
    "tier0": {"reading": {"bucket": "manifest-records", "key": "readings/SHP00001.json"}},
}


@pytest.fixture
def climb(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every tier the handler asks to read at, and never rescue anything.

    A reader that rescues nothing is the worst case and the one worth testing: it is what makes
    the loop go all the way up, and going all the way up is what was not happening.
    """
    asked: list[int] = []

    def _read_at(*, tier: int, **_kwargs: Any) -> dict[str, Any]:
        asked.append(tier)
        return {"pages": []}

    def _redecide(*, fields: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        # Unchanged: every field that abstained is still abstaining. The loop must decide to go
        # up again from that alone.
        return fields

    monkeypatch.setattr(escalate, "_read_at", _read_at)
    monkeypatch.setattr(escalate, "_redecide", _redecide)
    monkeypatch.setattr(escalate, "_reading", lambda _event: {"pages": []})
    monkeypatch.setattr(escalate, "_thresholds", lambda _reader: {"consignee": 0.9})
    monkeypatch.setattr(escalate, "_reader_of", lambda _event: "reference-ocr@tesseract 5.5.0")
    monkeypatch.setattr(escalate, "emit", lambda _span: None)
    monkeypatch.setenv("CONTRACTS_DIR", "contracts")
    return asked


def test_english_climbs_every_eligible_tier_rather_than_stopping_at_the_first(climb) -> None:
    """`en: [0, 1, 2, 3]`. A field that nothing rescues must reach all three."""
    answer = escalate.handler(EVENT)

    assert climb == [1, 2, 3]
    assert answer["escalation"]["tier"] == 3
    assert [round_["tier"] for round_ in answer["escalation"]["rounds"]] == [1, 2, 3]


def test_the_climb_stops_when_no_tier_is_left_rather_than_on_a_counter(climb) -> None:
    """The bound is the contract's eligible tiers, not a number chosen here.

    A loop bounded by a counter would keep working when a tier was added and quietly stop one
    rung short.
    """
    answer = escalate.handler(EVENT)

    assert len(answer["escalation"]["rounds"]) == len({1, 2, 3})


def test_greek_climbs_to_the_only_tier_it_has(climb) -> None:
    """`el: [0, 3]` — no managed OCR reads Greek, so the model tier is the whole ladder."""
    greek = {
        **EVENT,
        "extraction": {
            "outcome": {**EVENT["extraction"]["outcome"], "language": "el"},
        },
    }

    answer = escalate.handler(greek)

    assert climb == [3]
    assert answer["escalation"]["tier"] == 3


def test_a_field_rescued_at_tier_one_costs_nothing_above_it(monkeypatch) -> None:
    """The whole economic argument for a cascade, and the case the loop must not break.

    A loop that climbed regardless of what came back would pay for every tier on every document,
    which is worse than the single step it replaced.
    """
    asked: list[int] = []

    def _read_at(*, tier: int, **_kwargs: Any) -> dict[str, Any]:
        asked.append(tier)
        return {"pages": []}

    def _rescues(*, fields: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in entry.items() if k != "queued_because"} | {"publishable": True}
            for entry in fields
        ]

    monkeypatch.setattr(escalate, "_read_at", _read_at)
    monkeypatch.setattr(escalate, "_redecide", _rescues)
    monkeypatch.setattr(escalate, "_reading", lambda _event: {"pages": []})
    monkeypatch.setattr(escalate, "_thresholds", lambda _reader: {"consignee": 0.9})
    monkeypatch.setattr(escalate, "_reader_of", lambda _event: "reference-ocr@tesseract 5.5.0")
    monkeypatch.setattr(escalate, "emit", lambda _span: None)
    monkeypatch.setenv("CONTRACTS_DIR", "contracts")

    answer = escalate.handler(EVENT)

    assert asked == [1]
    assert answer["escalation"]["tier"] == 1


def test_the_payload_records_the_whole_climb_and_not_only_where_it_ended(climb) -> None:
    """A document that went 0 → 1 → 2 → 3 paid for three reads, and the history must say so.

    Reporting only the tier it ended at would understate the bill by two thirds, in the one
    place claim 7's cost model reads from.
    """
    rounds = escalate.handler(EVENT)["escalation"]["rounds"]

    assert [round_["reports_confidence"] for round_ in rounds] == [True, False, False]


def test_the_handler_still_names_the_page_the_field_is_on(climb) -> None:
    """Unchanged by the loop, and worth an assertion because the loop rewrote the call site."""
    source = Path("src/manifest/handlers/escalate.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    assert 'entry["page"]' in code
    assert "page-0001.png" not in code
