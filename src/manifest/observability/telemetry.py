"""Spans and metrics for one document's journey, and the meter that prices it.

**No OpenTelemetry SDK is imported here.** This module builds the span *records* — the shape,
the attributes, the parent links — as plain data, and an exporter turns them into whatever the
estate runs. That split is the same one the reader adapters follow, and it buys the same thing:
the attribute set a claim depends on is testable without a collector.

Two rules that are not conventions.

**No document text in an attribute, ever.** A commercial invoice is a counterparty's content
and a trace is a place people search. Attributes carry ids, counts, tiers, confidences and
decisions — never a value read off a page. `forbidden_attribute` refuses at construction rather
than trusting review, because the first person to add `field_value` to a span for debugging
will be doing it at four in the afternoon.

**Cost is modelled and the attribute name says so.** `modelled_cost` rather than `cost`, in the
span, in the metric and in the warehouse column. `docs/DECISIONS.md` 15: nothing here has been
sent to a billed API, and a metric called `cost` is read as a measurement by the first person
to open a dashboard — long after whoever knew better has moved on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

#: Attribute names that would put a counterparty's content into a trace. Refused rather than
#: documented: a trace is searchable, exportable and retained on somebody else's schedule.
FORBIDDEN_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "field_value",
        "value",
        "text",
        "page_text",
        "raw",
        "document_text",
        "party_name",
        "shipper",
        "consignee",
        "seller",
        "buyer",
        "declarant",
    }
)

#: The one metric name that carries money, and it announces itself. A `manifest.cost` would be
#: read as a measurement; this cannot be.
COST_METRIC: Final = "manifest.extraction.modelled_cost"

#: The longest a span attribute may be. The forbidden-name list above is a list, and a list is
#: never complete; a length bound catches the document smuggled in under a name this module does
#: not know to refuse.
LONGEST_ATTRIBUTE: Final = 256


class TelemetryError(ValueError):
    """A span that would put something in a trace that does not belong there."""


@dataclass(frozen=True, slots=True)
class Span:
    """One step in a document's journey, as data.

    `parent` links rather than nests, so a span can be built where the work happens and
    assembled afterwards. A tree built by nesting requires the whole journey to be in one
    process, which the batch path is not.
    """

    name: str
    trace_id: str
    span_id: str
    parent: str | None
    attributes: dict[str, str | int | float | bool]

    def __post_init__(self) -> None:
        offending = sorted(set(self.attributes) & FORBIDDEN_ATTRIBUTES)
        if offending:
            raise TelemetryError(
                f"span {self.name!r} carries {offending}, which is a counterparty's content. A "
                f"trace is searchable, exportable and retained on somebody else's schedule — "
                f"ids, counts and decisions go in a span; the values read off a page do not"
            )
        for key, value in self.attributes.items():
            if isinstance(value, str) and len(value) > LONGEST_ATTRIBUTE:
                raise TelemetryError(
                    f"span {self.name!r} attribute {key!r} is {len(value)} characters. A span "
                    f"attribute that long is a document being smuggled into a trace under a "
                    f"name this module does not know to refuse"
                )


def extraction_span(
    *,
    trace_id: str,
    span_id: str,
    document_version: str,
    document_type: str,
    reader_tier: int,
    language: str,
    fields_extracted: int,
    fields_published: int,
    fields_queued: int,
    parent: str | None = None,
) -> Span:
    """The span for reading one document.

    The attribute set is the one every downstream question needs and nothing more:
    `fields_published` against `fields_queued` is claim 5's queue load per document, and
    `reader_tier` is the join between quality and cost.
    """
    return Span(
        name="manifest.extract_document",
        trace_id=trace_id,
        span_id=span_id,
        parent=parent,
        attributes={
            "manifest.document_version": document_version,
            "manifest.document_type": document_type,
            "manifest.reader_tier": reader_tier,
            "manifest.language": language,
            "manifest.fields_extracted": fields_extracted,
            "manifest.fields_published": fields_published,
            "manifest.fields_queued": fields_queued,
        },
    )


@dataclass(frozen=True, slots=True)
class CostSample:
    """One page read, priced by the model.

    Named `modelled_*` throughout, and the currency travels with the amount. A meter that
    accumulated a bare number would eventually add dollars to euros, which is the error
    `core/scale.model_cost` already refuses one layer down.
    """

    document_version: str
    page: int
    reader_tier: int
    modelled_cost: Decimal
    modelled_currency: str


@dataclass
class CostMeter:
    """Accumulates modelled cost per document and per tier.

    Not a measurement, and every name in it says so. What it produces is the input to the
    warehouse's `modelled_cost` column and to claim 7's figure — both of which carry the word
    into every place a reader could meet the number.
    """

    samples: list[CostSample] = field(default_factory=list)

    def add(self, sample: CostSample) -> None:
        currencies = {existing.modelled_currency for existing in self.samples}
        if currencies and sample.modelled_currency not in currencies:
            raise TelemetryError(
                f"this meter is accumulating {sorted(currencies)} and was given "
                f"{sample.modelled_currency}. Adding them needs an exchange rate, which is a "
                f"measurement with a date; meter each currency separately"
            )
        self.samples.append(sample)

    @property
    def total(self) -> Decimal:
        return sum((sample.modelled_cost for sample in self.samples), start=Decimal("0"))

    @property
    def by_tier(self) -> dict[int, Decimal]:
        totals: dict[int, Decimal] = {}
        for sample in self.samples:
            totals[sample.reader_tier] = (
                totals.get(sample.reader_tier, Decimal("0")) + sample.modelled_cost
            )
        return dict(sorted(totals.items()))

    def per_thousand_pages(self) -> Decimal:
        if not self.samples:
            return Decimal("0.00")
        return (self.total / Decimal(len(self.samples)) * Decimal(1000)).quantize(Decimal("0.01"))

    def as_metrics(self) -> tuple[tuple[str, Decimal, dict[str, str | int]], ...]:
        """`(name, value, attributes)` for each tier. The name carries the word `modelled`."""
        return tuple(
            (
                COST_METRIC,
                amount,
                {"manifest.reader_tier": tier, "manifest.modelled": "true"},
            )
            for tier, amount in self.by_tier.items()
        )
