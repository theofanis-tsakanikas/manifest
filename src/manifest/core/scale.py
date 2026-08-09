"""CLAIM 7 — bulk reprocessing is idempotent, and its cost is a model that says so.

Four million documents, a better engine, and a job that must not do the work twice.

**The planner is pure, and it is where the claim lives.** No distributed job in this repository
has ever been executed, so idempotence is proved against this planner and its ledger, running
on a laptop. `infra/batch/` is an adapter that would execute a plan this produces — written,
validated, never run. Saying it the other way round would be claiming a property of a cluster
nobody has started.

**Idempotence is a property of the ledger, not of the job.** A re-run consults what is already
recorded at the same reader version and plans nothing for it. That makes the interesting case
the *partial* one: a job that died halfway leaves a ledger with half its entries, and the
re-run must plan exactly the remainder — not everything, which is the expensive failure, and
not nothing, which is the silent one.

**Cost is a model, and it is labelled a model everywhere it appears.** The routing distribution
across cascade tiers is *measured* on the corpus; the unit prices are *published* figures, cited
and dated where they are used. The product is an estimate, and the value of the escalated
fraction is an **assumption** whose sensitivity is shown rather than hidden — because the upper
tiers are never called and there is no accuracy figure for them (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Disposition(StrEnum):
    """What a plan decided about one document."""

    #: Not yet processed at this reader version.
    PROCESS = "process"
    #: Already processed at this reader version. Skipped, and the skip is the claim.
    SKIP = "skip"
    #: Processed at an older version. Re-processed, and its diff is required.
    REPROCESS = "reprocess"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One document, processed once, at one reader version.

    The ledger is keyed by `(document, reader)` rather than by document alone. Keyed by document
    only, a reader upgrade would look like work already done — which is the failure that makes a
    four-million-document re-extraction silently do nothing.
    """

    document: str
    reader: str
    version: str


@dataclass(frozen=True, slots=True)
class PlannedItem:
    document: str
    disposition: Disposition
    previous_version: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class Plan:
    """What a run would do, before it does any of it."""

    reader: str
    items: tuple[PlannedItem, ...]

    def of(self, disposition: Disposition) -> tuple[PlannedItem, ...]:
        return tuple(item for item in self.items if item.disposition is disposition)

    @property
    def work(self) -> int:
        """How many documents this run would actually read."""
        return sum(1 for item in self.items if item.disposition is not Disposition.SKIP)


def plan(
    documents: list[str] | tuple[str, ...],
    ledger: list[LedgerEntry] | tuple[LedgerEntry, ...],
    reader: str,
) -> Plan:
    """What to do with each document, given what the ledger already records.

    Deterministic and total: every document gets exactly one disposition, and the same inputs
    produce the same plan. A planner that consulted a clock or a queue depth would make a
    re-run's behaviour depend on when it ran, and "idempotent" would become "idempotent if
    nothing else was happening".
    """
    at_this_reader = {entry.document for entry in ledger if entry.reader == reader}
    latest: dict[str, LedgerEntry] = {}
    for entry in ledger:
        latest[entry.document] = entry

    items = []
    for document in documents:
        if document in at_this_reader:
            items.append(
                PlannedItem(
                    document=document,
                    disposition=Disposition.SKIP,
                    previous_version=latest[document].version,
                    reason=f"already processed at {reader}; a re-run plans no work for it",
                )
            )
        elif document in latest:
            items.append(
                PlannedItem(
                    document=document,
                    disposition=Disposition.REPROCESS,
                    previous_version=latest[document].version,
                    reason=(
                        f"last processed at {latest[document].reader}; a new reader version is "
                        f"a new record version and requires a diff, never an overwrite"
                    ),
                )
            )
        else:
            items.append(
                PlannedItem(
                    document=document,
                    disposition=Disposition.PROCESS,
                    previous_version=None,
                    reason="never processed",
                )
            )
    return Plan(reader=reader, items=tuple(items))


def record(
    ledger: list[LedgerEntry], executed: Plan, versions: dict[str, str]
) -> list[LedgerEntry]:
    """Append what a run completed. Only what it completed.

    `versions` carries the outcome per document, so a run that died halfway appends the half it
    finished. That is what makes the resumed plan the *remainder* — recording the whole plan
    optimistically would make a crashed job look complete, which is the silent failure, and
    recording nothing would make it repeat everything, which is the expensive one.
    """
    done = [
        LedgerEntry(document=item.document, reader=executed.reader, version=versions[item.document])
        for item in executed.items
        if item.disposition is not Disposition.SKIP and item.document in versions
    ]
    return [*ledger, *done]


# ── The cost model ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UnitPrice:
    """A published price, with where it came from and when it was read.

    Both are required. A price with no source is a number that sounds right, and a price with no
    date is a number that was right once — `CLAUDE.md` forbids each of them separately.
    """

    tier: int
    per_page: Decimal
    currency: str
    source: str
    read_on: str

    def __post_init__(self) -> None:
        if not self.source or not self.read_on:
            raise ValueError(
                f"the price for tier {self.tier} has no source or no date. Every cost figure in "
                f"this repository is traced or deleted; a price with neither is the kind of "
                f"number that sounds right"
            )


@dataclass(frozen=True, slots=True)
class CostModel:
    """A modelled cost per thousand pages, and the assumption it rests on.

    Every field name here says "modelled" or carries a caveat, because the one thing this must
    never do is read as a measurement. No page in this repository has been sent to a billed API.
    """

    pages: int
    distribution: dict[int, int]
    prices: tuple[UnitPrice, ...]
    modelled_cost_per_1000_pages: Decimal
    currency: str
    escalated_share: Decimal
    assumption: str

    def as_lines(self) -> tuple[str, ...]:
        by_tier = {price.tier: price for price in self.prices}
        lines = [
            f"modelled cost per 1,000 pages: {self.modelled_cost_per_1000_pages} "
            f"{self.currency}  — MODELLED, never measured",
            f"routing measured over {self.pages} pages of the committed recording:",
        ]
        for tier in sorted(self.distribution):
            share = Decimal(self.distribution[tier]) / Decimal(self.pages or 1)
            price = by_tier.get(tier)
            lines.append(
                f"   tier {tier}: {self.distribution[tier]:>6} pages ({share:6.1%})"
                + (
                    f"  x {price.per_page} {price.currency}/page"
                    f"  [{price.source}, read {price.read_on}]"
                    if price
                    else "  (no unit price — this tier runs here and costs nothing)"
                )
            )
        lines.append(f"assumption: {self.assumption}")
        return tuple(lines)


def model_cost(
    distribution: dict[int, int],
    prices: tuple[UnitPrice, ...],
    assumption: str,
) -> CostModel:
    """The measured routing distribution multiplied by published unit prices.

    The multiplication is the whole model, and stating it that plainly is the point: there is
    no measurement of a bill anywhere in it, and a reader who takes the output for one has been
    misled by the caller rather than by this function.

    **The currency comes from the prices, and mixing two is refused.** The first version took a
    currency argument defaulting to EUR and was handed a price published in USD, so it printed
    a dollar figure with a euro sign on it. A cost model that gets its own unit wrong is the
    exact failure `core/quantity.py` exists to prevent, one layer up, and there is no exchange
    rate in this repository to convert with — nor should there be, because a rate is a
    measurement with a date and this file would then need one.
    """
    currencies = {price.currency for price in prices}
    if len(currencies) > 1:
        raise ValueError(
            f"these prices are published in {sorted(currencies)}. Adding them needs an exchange "
            f"rate, which is a measurement with a date; this model has no such thing and will "
            f"not invent one. Model each currency separately"
        )
    currency = next(iter(currencies), "USD")
    pages = sum(distribution.values())
    by_tier = {price.tier: price for price in prices}
    total = sum(
        (
            Decimal(count) * by_tier[tier].per_page
            for tier, count in distribution.items()
            if tier in by_tier
        ),
        start=Decimal("0"),
    )
    per_thousand = (
        (total / Decimal(pages) * Decimal(1000)).quantize(Decimal("0.01"))
        if pages
        else Decimal("0.00")
    )
    escalated = sum(count for tier, count in distribution.items() if tier > 0)
    return CostModel(
        pages=pages,
        distribution=dict(distribution),
        prices=prices,
        modelled_cost_per_1000_pages=per_thousand,
        currency=currency,
        escalated_share=(Decimal(escalated) / Decimal(pages)) if pages else Decimal("0"),
        assumption=assumption,
    )


def sensitivity(
    distribution: dict[int, int],
    prices: tuple[UnitPrice, ...],
    shares: tuple[Decimal, ...],
) -> tuple[tuple[Decimal, Decimal], ...]:
    """The modelled cost at several assumed escalation shares.

    Shown rather than hidden, because the escalated fraction is the model's largest unknown and
    a single figure derived from the most flattering assumption is the way a cost model lies
    while every number in it is true.
    """
    pages = sum(distribution.values())
    results = []
    for share in shares:
        escalated = int(Decimal(pages) * share)
        hypothetical = {0: pages - escalated, 1: escalated}
        results.append(
            (
                share,
                model_cost(hypothetical, prices, "sensitivity sweep").modelled_cost_per_1000_pages,
            )
        )
    return tuple(results)
