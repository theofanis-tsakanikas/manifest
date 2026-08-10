"""CLAIM 7 — bulk reprocessing is idempotent, and its cost is a model that says so.

**Idempotence is proved against the pure planner and its ledger, on a laptop.** No distributed
job in this repository has ever been executed; `infra/batch/` is an adapter that would run a
plan this produces, written and validated and never started. Claiming the property of the
cluster would be claiming something about a thing nobody has run.

Four runs over the same 3,000 documents:

1. a first pass — everything is work;
2. an immediate re-run — **nothing** is work, which is the claim;
3. a run that died halfway, resumed — exactly the remainder, not everything (the expensive
   failure) and not nothing (the silent one);
4. a reader upgrade — everything is work again, each item carrying the version it supersedes,
   because a reader change that looked like work already done is how a four-million-document
   re-extraction silently does nothing.

**The cost is a model.** The routing distribution is *measured* over the committed recording;
the unit price for tier 1 is a *published* figure with its source and the date it was read. The
tier-2 price is deliberately absent: it is charged per token, this repository has never made a
call to count tokens with, and inventing a per-page equivalent would be the fabricated number
everything else here exists to avoid. Its volume is reported in pages and its cost is shown as
a sensitivity sweep instead.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from evals.harness import by_field, contracts, field_contract, ground_truth, score_all
from manifest.core.calibration import derive
from manifest.core.cascade import Route, distribution, route
from manifest.core.scale import (
    Disposition,
    LedgerEntry,
    UnitPrice,
    model_cost,
    plan,
    record,
    sensitivity,
)

READER = "reference-ocr@tesseract 5.5.2"
UPGRADED = "reference-ocr@tesseract 5.6.0"

#: Published, cited, dated. `docs/AWS-CONSTRAINTS.md` records that the Textract pricing page
#: states this rate for US West (Oregon) and does not state a Frankfurt rate on the same page,
#: so the region is named here rather than quietly assumed to be the estate's.
PRICES = (
    UnitPrice(
        tier=1,
        per_page=Decimal("0.0015"),
        currency="USD",
        source=(
            "Amazon Textract pricing, DetectDocumentText, US West (Oregon), first 1M pages/month"
        ),
        read_on="2026-08-09",
    ),
)


def main() -> int:
    documents = sorted(
        f"{document['shipment_id']}/{document['document_id']}"
        for document in ground_truth()["documents"]
    )
    failures: list[str] = []

    # 1 — the first pass.
    first = plan(documents, [], READER)
    ledger: list[LedgerEntry] = []
    ledger = record(ledger, first, {item.document: "v1" for item in first.items})
    if first.work != len(documents):
        failures.append("the first pass planned less than everything")

    # 2 — the immediate re-run.
    again = plan(documents, ledger, READER)
    if again.work != 0:
        failures.append(
            f"a re-run planned {again.work} documents of work. Idempotence is the whole claim, "
            f"and at four million documents this is the difference between a job and a bill"
        )
    if len(again.of(Disposition.SKIP)) != len(documents):
        failures.append("a re-run did not skip everything it had already done")

    # 3 — the crash, resumed.
    half = len(documents) // 2
    crashed_ledger = record([], first, {item.document: "v1" for item in first.items[:half]})
    resumed = plan(documents, crashed_ledger, READER)
    if resumed.work != len(documents) - half:
        failures.append(
            f"a resumed run planned {resumed.work} documents where {len(documents) - half} "
            f"remained. Too many is the expensive failure; too few is the silent one, and a "
            f"job that recorded its whole plan optimistically would produce the second"
        )

    # 4 — the reader upgrade.
    upgraded = plan(documents, ledger, UPGRADED)
    if len(upgraded.of(Disposition.REPROCESS)) != len(documents):
        failures.append(
            "a reader upgrade did not re-plan every document. A ledger keyed by document alone "
            "would make an upgrade look like work already done"
        )
    if any(item.previous_version is None for item in upgraded.of(Disposition.REPROCESS)):
        failures.append("a re-processed document did not carry the version it supersedes")

    # ── The routing distribution, measured ───────────────────────────────────
    scored = score_all()
    thresholds: dict[str, float | None] = {}
    for name, entries in by_field(scored).items():
        contract = field_contract(name)
        if contract.always_review:
            thresholds[name] = None
            continue
        derived = derive(
            name, [entry.observation for entry in entries], contract.error_budget or Decimal("0")
        )
        thresholds[name] = derived.value

    decisions = []
    for entry in scored:
        eligible = contracts().cascade.eligible(entry.language)
        decisions.append(
            route(
                page=f"{entry.shipment}/{entry.document}/{entry.field}",
                language=entry.language,
                confidence=entry.extracted.confidence,
                threshold=thresholds.get(entry.field),
                eligible=eligible,
                current_tier=0,
            )
        )

    routed = distribution(decisions)
    abstained = sum(1 for decision in decisions if decision.route is Route.ABSTAIN)
    escalated = sum(1 for decision in decisions if decision.route is Route.ESCALATE)
    kept = sum(1 for decision in decisions if decision.route is Route.KEEP)

    # **This models the first hop only, and that is a limit rather than a simplification.**
    #
    # A page is routed from tier 0 on a tier-0 confidence, which this repository has actually
    # measured. Routing it *onward* — from the per-page OCR tier to the document-automation
    # tier, say — would need that tier's confidences, and no page has been sent to it. The
    # honest options were to assume a distribution or to stop, and stopping is the one that
    # does not put an invented number underneath a cost figure.
    #
    # A visible consequence: the document-automation tier shows **no volume at all**, because
    # nothing reaches it in one hop. That is the model reporting its own boundary rather than
    # quietly distributing pages across tiers to make the table look complete.
    unpriced = {tier: count for tier, count in routed.items() if tier >= 2}
    cost = model_cost(
        {tier: count for tier, count in routed.items() if tier <= 1},
        PRICES,
        assumption=(
            "the model tier is charged per token and this repository has never made a call to "
            "count tokens with, so it carries no unit price here. The document-automation tier "
            "is charged per page and is reachable only by a second escalation, which this model "
            "does not attempt: it would need per-page-OCR confidences, and no page has been "
            "sent to that tier. Both volumes are reported and both costs are left as explicit "
            "unknowns rather than invented"
        ),
    )

    print("claim 7 — bulk reprocessing is idempotent, and its cost is modelled\n")
    print(f"  documents                      {len(documents):,}")
    print(f"  first pass                     {first.work:,} planned")
    print(f"  immediate re-run               {again.work:,} planned  <- the claim")
    print(
        f"  resumed after a crash at 50%   {resumed.work:,} planned "
        f"of {len(documents) - half:,} remaining"
    )
    print(
        f"  after a reader upgrade         {len(upgraded.of(Disposition.REPROCESS)):,} "
        f"re-planned, all carrying a prior version"
    )

    print("\n  routing, measured over the committed recording:")
    print(f"     kept at tier 0              {kept:,}")
    print(f"     escalated                   {escalated:,}")
    print(f"     abstained (no eligible tier for the language)  {abstained:,}")
    print()
    for line in cost.as_lines():
        print(f"     {line}")

    for tier, count in sorted(unpriced.items()):
        print(
            f"     tier {tier}: {count:>6} pages — NOT PRICED. Charged per token; no call has "
            f"been made here to count tokens with, and a per-page equivalent invented for the "
            f"table would be the fabricated figure everything else here avoids"
        )
    print("\n     sensitivity — modelled cost per 1,000 pages at assumed escalation shares:")
    for share, figure in sensitivity(
        routed, PRICES, (Decimal("0.05"), Decimal("0.15"), Decimal("0.30"), Decimal("0.50"))
    ):
        print(f"        {share:>5.0%} escalated -> {figure} USD")
    print(
        "\n     Shown rather than hidden: the escalated fraction is this model's largest "
        "unknown, and a single figure taken from the most flattering assumption is how a cost "
        "model lies while every number in it is true."
    )
    print(
        "\n  What the cascade CANNOT claim here: what a higher tier would have read. No page has "
        "been sent to one. 'Accuracy held at X for Y% of the cost' is unavailable in this "
        "repository and does not appear in it."
    )

    if failures:
        print("\nclaim 7: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(
        "\nclaim 7: a re-run does no work; a crash resumes at exactly the remainder; a reader "
        "change re-plans everything with a diff. The cost is a model and says so."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
