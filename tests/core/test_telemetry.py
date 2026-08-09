"""Telemetry: what may go in a trace, and what may not."""

from __future__ import annotations

from decimal import Decimal

import pytest

from manifest.observability.telemetry import (
    COST_METRIC,
    CostMeter,
    CostSample,
    Span,
    TelemetryError,
    extraction_span,
)


def _span(**attributes) -> Span:
    return Span(name="test", trace_id="t", span_id="s", parent=None, attributes=attributes)


def test_a_span_carrying_document_text_is_refused() -> None:
    """A trace is searchable, exportable and retained on somebody else's schedule. The first
    person to add `field_value` for debugging will be doing it at four in the afternoon."""
    with pytest.raises(TelemetryError, match="counterparty's content"):
        _span(field_value="27000 KGS")


def test_a_span_carrying_a_party_name_is_refused() -> None:
    with pytest.raises(TelemetryError, match="counterparty's content"):
        _span(consignee="Van Dijk Import B.V.")


def test_an_attribute_long_enough_to_be_a_document_is_refused() -> None:
    """The names this module knows to refuse are a list, and a list is never complete. A length
    bound catches the smuggling that a name check cannot."""
    with pytest.raises(TelemetryError, match="smuggled into a trace"):
        _span(manifest_note="x" * 300)


def test_the_extraction_span_carries_what_every_downstream_question_needs() -> None:
    span = extraction_span(
        trace_id="t",
        span_id="s",
        document_version="v1",
        document_type="bill_of_lading",
        reader_tier=0,
        language="el",
        fields_extracted=9,
        fields_published=2,
        fields_queued=7,
    )
    assert span.attributes["manifest.reader_tier"] == 0
    assert span.attributes["manifest.fields_queued"] == 7
    assert not set(span.attributes) & {"value", "text"}


def test_the_cost_metric_announces_itself_as_modelled() -> None:
    """A metric called `cost` is read as a measurement by the first person to open a dashboard,
    long after whoever knew better has moved on."""
    assert "modelled" in COST_METRIC


def test_the_meter_refuses_two_currencies() -> None:
    """Adding them needs an exchange rate, which is a measurement with a date."""
    meter = CostMeter()
    meter.add(CostSample("v1", 1, 1, Decimal("0.0015"), "USD"))
    with pytest.raises(TelemetryError, match="exchange rate"):
        meter.add(CostSample("v1", 2, 1, Decimal("0.0015"), "EUR"))


def test_the_meter_reports_per_tier_and_per_thousand_pages() -> None:
    meter = CostMeter()
    for page in range(1, 5):
        meter.add(CostSample("v1", page, 0, Decimal("0"), "USD"))
    for page in range(5, 7):
        meter.add(CostSample("v1", page, 1, Decimal("0.0015"), "USD"))
    assert meter.by_tier == {0: Decimal("0"), 1: Decimal("0.0030")}
    assert meter.per_thousand_pages() == Decimal("0.50")
    assert all(metric[0] == COST_METRIC for metric in meter.as_metrics())
