"""What `publish` returns is what `escalate` requires.

**Two tests that each pass in isolation is what a contract looks like when nobody wrote it
down.** `escalate` had a test proving it refuses an event with no language. `publish` had tests
proving it thresholds correctly. Both were green while the deployed pipeline could not escalate
a single document: `publish` derived the language from the reading, used it to choose the field
captions, and returned an object without it — and `Escalate`, the very next state, is handed
that object and needs the language before it can ask which tiers may read the page.

The refusal test passed *because* it built the event by hand, which is the one way the event is
never built. So this one does not build an event. It runs the real producer and hands the real
return value to the real consumer's requirement.

Offline: the two calls that need AWS — the stored reading and the threshold artefact — are
replaced, and nothing else is. The reading is synthetic and deliberately unreadable, so every
field of the contract comes back missing and the outcome is small; what is under test is the
*shape* of what comes out, not what was in the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manifest.contracts.loader import load
from manifest.handlers import escalate, publish


@pytest.fixture
def a_reading() -> dict[str, Any]:
    """One page, one line, one word, in English. Nothing on it matches any caption."""
    return {
        "document_id": "SHP-CONTRACT-1",
        "source_digest": "0" * 64,
        "reader": {"name": "tesseract", "version": "5.5.0"},
        "pages": [
            {
                "number": 1,
                "width": 1000,
                "height": 1400,
                "language": "en",
                "language_confidence": 0.99,
                "lines": [
                    {
                        "confidence": 0.9,
                        # Fractions of the page, which the representation enforces.
                        "words": [
                            {"text": "qqqq", "confidence": 0.9, "box": [0.01, 0.01, 0.04, 0.02]}
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def outcome(monkeypatch: pytest.MonkeyPatch, a_reading: dict[str, Any]) -> dict[str, Any]:
    monkeypatch.setattr(publish, "_load_json", lambda bucket, key: a_reading)
    monkeypatch.setattr(publish, "_thresholds", lambda reader: dict.fromkeys(_FIELDS, 0.9))
    monkeypatch.setenv("CONTRACTS_DIR", "contracts")
    return publish.handler(
        {"reading": {"bucket": "b", "key": "k"}, "document_type": "bill_of_lading"}
    )


#: Every field the bill-of-lading contract declares, read from the contract rather than listed,
#: because `_outcome` refuses a field absent from the artefact — deliberately, so that a
#: deployment not matching its contract cannot hide behind behaviour that looks like
#: always-review.
def _bill_of_lading_fields() -> list[str]:
    return [field.name for field in load(Path("contracts")).documents["bill_of_lading"].fields]


_FIELDS = _bill_of_lading_fields()


def test_publish_supplies_every_fact_escalate_requires(outcome: dict[str, Any]) -> None:
    """The assertion the estate needed and neither handler's own tests could make."""
    document_id, document_type, language = escalate.required_facts(
        {"extraction": {"outcome": outcome}}, outcome
    )
    assert document_id == "SHP-CONTRACT-1"
    assert document_type == "bill_of_lading"
    assert language == "en", (
        "publish derived the language to choose the field captions and did not return it. "
        "Escalate is the next state and cannot route without it"
    )


def test_the_requirement_still_refuses_an_outcome_that_is_missing_one(
    outcome: dict[str, Any],
) -> None:
    """The guard is not satisfied by the producer merely existing.

    Paired with the test above on purpose: one shows the real producer satisfies the
    requirement, the other that the requirement is still capable of refusing. Either alone is
    the state this contract was already in.
    """
    without_language = {key: value for key, value in outcome.items() if key != "language"}
    with pytest.raises(escalate.HandlerError, match="language"):
        escalate.required_facts({"extraction": {"outcome": without_language}}, without_language)


def test_the_published_record_can_say_which_version_it_is(outcome: dict[str, Any]) -> None:
    """`fingerprint` and `reader` travel with the record, not only in its object key.

    The record is written to `records/<document_id>/<fingerprint>.json`. For a while the key
    said which version it was and the object did not — rename the file and nothing inside
    disagrees. Doctrine rule 4 is about versions being retrievable *and comparable*, and a diff
    between two records neither of which names itself is a diff between two anonymous documents.

    It surfaced as the landing function refusing a record with no version, which is the cheap
    end of that failure. The expensive end is a restatement nobody can attribute.
    """
    for fact in ("document_id", "fingerprint", "document_type", "reader", "language"):
        assert outcome.get(fact), f"a published record with no {fact} cannot be checked against"
