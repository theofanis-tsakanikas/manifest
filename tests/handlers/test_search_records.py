"""Asking the index a question, and the things it must refuse to ask.

The interesting cases here are all about what a search *cannot* reach. An index of published
customs records sitting behind a search box is the one surface where a field that arrived by
accident becomes a field a person reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from manifest.core.search import MAXIMUM_HITS, query_for
from manifest.handlers import _aoss, search_records

ANSWER = {
    "hits": {
        "hits": [
            {
                "_id": "sha256:abc",
                "_score": 4.2,
                "_source": {
                    "document_id": "SHP00001",
                    "document_type": "bill_of_lading",
                    "version": "sha256:abc",
                    "indexed_on": "2026-08-13T10:00:00+00:00",
                    "fields": {"container_number": "MSKU1234567"},
                    "published_field_count": 1,
                    "queued_field_count": 2,
                },
            }
        ]
    }
}


@pytest.fixture
def surface(monkeypatch: pytest.MonkeyPatch):
    """A collection answering `ANSWER`, remembering every call it was asked to make."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _call(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, url, payload))
        return ANSWER

    monkeypatch.setenv("SEARCH_ENDPOINT", "https://abc.eu-central-1.aoss.amazonaws.com")
    monkeypatch.setattr(search_records, "call", _call)
    return calls


def test_a_match_comes_back_with_the_version_it_belongs_to(surface) -> None:
    """A correction and the record it corrects are both in the index. A hit must say which."""
    result = search_records.handler({"term": "MSKU1234567"})

    assert result["matched"] == 1
    assert result["records"][0]["version"] == "sha256:abc"
    assert result["records"][0]["document_id"] == "SHP00001"


def test_the_question_is_a_search_and_not_a_write(surface) -> None:
    search_records.handler({"term": "MSKU1234567"})
    method, url, _ = surface[0]
    assert method == "POST"
    assert url.endswith("/records/_search")


def test_an_empty_term_is_refused_rather_than_matching_everything(surface) -> None:
    with pytest.raises(search_records.HandlerError, match="whole index"):
        search_records.handler({"term": "  "})
    assert surface == []


def test_the_page_size_is_capped_however_much_is_asked_for(surface) -> None:
    """An unbounded page is a way to read the index out through a search box."""
    search_records.handler({"term": "MSKU1234567", "hits": 10_000})

    assert surface[0][2]["size"] == MAXIMUM_HITS


def test_a_full_page_says_that_there_may_be_more(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_ENDPOINT", "https://abc.eu-central-1.aoss.amazonaws.com")
    monkeypatch.setattr(
        search_records,
        "call",
        lambda *_args, **_kwargs: {"hits": {"hits": [ANSWER["hits"]["hits"][0]] * 20}},
    )

    assert search_records.handler({"term": "MSKU1234567"})["truncated"] is True


def test_an_unreachable_collection_is_reported_as_a_refusal(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_ENDPOINT", "https://abc.eu-central-1.aoss.amazonaws.com")

    def _fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise _aoss.SearchSurfaceError("the collection could not be reached: timed out")

    monkeypatch.setattr(search_records, "call", _fail)

    with pytest.raises(search_records.HandlerError, match="could not be reached"):
        search_records.handler({"term": "MSKU1234567"})


def test_no_endpoint_configured_is_a_refusal_and_not_a_default(monkeypatch) -> None:
    monkeypatch.delenv("SEARCH_ENDPOINT", raising=False)

    with pytest.raises(search_records.HandlerError, match="refused rather than defaulted"):
        search_records.handler({"term": "MSKU1234567"})


# ── The query itself, which is where the whitelist lives ──────────────────────


def test_the_query_never_asks_against_every_field() -> None:
    """`_all` would make a field searchable the moment it arrived in a record.

    `document_for` refuses to *index* anything outside its whitelist and this is the other half:
    both lists have to be edited on purpose for a new value to become findable.
    """
    body = query_for("MSKU1234567")
    asked = body["query"]["bool"]["must"][0]["multi_match"]["fields"]

    assert "_all" not in asked
    assert asked == ["fields.*", "document_id"]


def test_the_term_is_text_and_not_an_expression() -> None:
    """`query_string` accepts an expression language. The term came from a document or a box."""
    body = query_for("shipper:* OR _exists_:raw_text")

    assert "query_string" not in str(body)
    assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == (
        "shipper:* OR _exists_:raw_text"
    )


def test_a_document_type_narrows_by_exact_match() -> None:
    body = query_for("Athens", document_type="commercial_invoice")

    assert {"term": {"document_type": "commercial_invoice"}} in body["query"]["bool"]["must"]


def test_the_newest_version_is_read_first() -> None:
    """A corrected record and the one it corrects are both hits. The current one comes first."""
    assert query_for("Athens")["sort"][0] == {"indexed_on": {"order": "desc"}}


def test_an_empty_term_is_refused_by_the_pure_function_too() -> None:
    """Both halves refuse it, because either can be the one a future caller reaches."""
    with pytest.raises(ValueError, match="whole index"):
        query_for("   ")
