"""The six document layouts.

Each function draws one document from a shipment and returns the PDF bytes plus the exact
placement of every field it declared. Layouts differ between document types on purpose —
`docs/SCENARIO.md` says carrier layouts vary enormously, so field position carries no
information and every value has to be *found* rather than looked up.

Three things are deliberate and easy to get wrong the other way.

**A planted mismatch is applied here, by substituting a value.** The document is drawn from the
altered value and the ground truth records the altered value, because that is what is on the
page — a reader that returns it is correct, and the disagreement is between two documents, not
between a document and its reading. Recording the true value would make claim 4's mismatches
indistinguishable from claim 1's misreads.

**A currency-symbol confusion is not simulated.** The document prints the real `€` or `¥`, and
whether the reader confuses it with `E` or `Y` is the reader's business. Rendering a `E` where
a `€` belongs would be planting the answer.

**A pathology that lives in the pixels is not applied here.** Stamps, bleed-through, handwriting
and illegibility are raster operations, applied in `corpus/degrade.py` against the placements
this module returns. That split is what lets a stamp land *on* a field rather than near it.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from corpus.labels import label
from corpus.plant import INJECTION_STRINGS, Planted
from corpus.sheet import BOLD, PAGE_HEIGHT, PAGE_WIDTH, Placed, Sheet, money, plain_money
from corpus.world import Language, Pathology, PerturbedFact, Shipment

_MARGIN = 48.0
_TOP = PAGE_HEIGHT - 56


@dataclass(frozen=True, slots=True)
class Rendered:
    document_id: str
    shipment_id: str
    language: Language
    pdf: bytes
    placements: tuple[Placed, ...]
    pathologies: tuple[Pathology, ...]


def _planted(planted: Planted, document: str, fact: PerturbedFact) -> str | None:
    for mismatch in planted.mismatches:
        if mismatch.document == document and mismatch.fact is fact:
            return mismatch.planted
    return None


def _header(sheet: Sheet, title_key: str, language: Language, subtitle: str) -> None:
    sheet.text(_MARGIN, _TOP, label(title_key, language), size=15, font=BOLD)
    sheet.text(PAGE_WIDTH - _MARGIN - 150, _TOP, subtitle, size=8)
    sheet.rule(_MARGIN, _TOP - 8, PAGE_WIDTH - _MARGIN)


# ── Bill of lading ───────────────────────────────────────────────────────────


def bill_of_lading(shipment: Shipment, planted: Planted, generator: random.Random) -> Rendered:
    language = shipment.languages["bill_of_lading"]
    pathologies = planted.pathologies.get("bill_of_lading", ())
    sheet = Sheet(title=f"BOL {shipment.shipment_id}")
    _header(sheet, "title_bill_of_lading", language, shipment.carrier)

    y = _TOP - 46
    sheet.labelled(
        "bill_of_lading_number",
        _MARGIN,
        y,
        label("bl_number", language),
        shipment.bill_of_lading_number,
    )
    sheet.labelled(
        "date_of_issue",
        PAGE_WIDTH / 2,
        y,
        label("date", language),
        shipment.sailed_on.strftime("%d/%m/%Y"),
    )

    y -= 46
    sheet.labelled(
        "shipper", _MARGIN, y, label("shipper", language), shipment.seller.form(generator)
    )
    sheet.labelled(
        "consignee", PAGE_WIDTH / 2, y, label("consignee", language), shipment.buyer.form(generator)
    )

    y -= 46
    sheet.labelled("vessel_name", _MARGIN, y, label("vessel", language), shipment.vessel)
    sheet.labelled(
        "port_of_loading",
        PAGE_WIDTH / 2,
        y,
        label("port_of_loading", language),
        f"{shipment.port_of_loading[0]} {shipment.port_of_loading[1]}",
    )

    y -= 46
    sheet.labelled(
        "port_of_discharge",
        _MARGIN,
        y,
        label("port_of_discharge", language),
        f"{shipment.port_of_discharge[0]} {shipment.port_of_discharge[1]}",
    )

    y -= 52
    sheet.box_outline(_MARGIN - 6, y - 34, PAGE_WIDTH - 2 * _MARGIN + 12, 58)
    sheet.labelled(
        "container_number",
        _MARGIN,
        y,
        label("container", language),
        shipment.container_number,
        size=11,
    )
    sheet.labelled(
        "gross_weight",
        PAGE_WIDTH / 2,
        y,
        label("gross_weight", language),
        f"{shipment.gross_weight_kg} KGS",
        size=11,
    )

    _footer(sheet, shipment, language)
    return Rendered(
        "bill_of_lading",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


# ── Commercial invoice ───────────────────────────────────────────────────────


def commercial_invoice(shipment: Shipment, planted: Planted, generator: random.Random) -> Rendered:
    language = shipment.languages["commercial_invoice"]
    pathologies = planted.pathologies.get("commercial_invoice", ())
    symbol = {"EUR": "€", "USD": "$", "JPY": "¥"}[shipment.currency]
    sheet = Sheet(title=f"INV {shipment.shipment_id}")
    _header(sheet, "title_commercial_invoice", language, shipment.seller.registered_name)

    y = _TOP - 46
    sheet.labelled(
        "invoice_number",
        _MARGIN,
        y,
        label("invoice_number", language),
        f"INV-{shipment.shipment_id[-5:]}",
    )
    sheet.labelled(
        "invoice_date",
        PAGE_WIDTH / 2,
        y,
        label("date", language),
        shipment.sailed_on.strftime("%d.%m.%Y"),
    )

    y -= 46
    sheet.labelled("seller", _MARGIN, y, label("seller", language), shipment.seller.form(generator))
    sheet.labelled(
        "buyer", PAGE_WIDTH / 2, y, label("buyer", language), shipment.buyer.form(generator)
    )

    y -= 46
    sheet.labelled(
        "currency", _MARGIN, y, label("currency", language), f"{symbol} {shipment.currency}"
    )
    sheet.labelled("incoterm", PAGE_WIDTH / 2, y, label("incoterm", language), shipment.incoterm)

    # ── The line-item table ──────────────────────────────────────────────
    #
    # Where the table breaks across a page boundary, the second page gets **no repeated
    # header**. That is the pathology: naive extraction reads the first page's rows, misses the
    # continuation, and the total still looks plausible because it was printed rather than
    # summed. Only the total failing to reconcile against a short list catches it, which is why
    # `line_item_count` is a declared field.
    y -= 44
    breaks = Pathology.TABLE_ACROSS_PAGE_BREAK in pathologies
    first_page_rows = 4 if breaks else len(shipment.lines)

    _invoice_table_header(sheet, y, language)
    y -= 16
    for index, line in enumerate(shipment.lines):
        if index == first_page_rows and breaks:
            sheet.text(_MARGIN, 70, f"— {label('continued', language)} —", size=8)
            sheet.new_page()
            y = _TOP
        sheet.text(_MARGIN, y, line.description[:44], size=8.5)
        sheet.text(_MARGIN + 250, y, line.hs_code, size=8.5)
        sheet.text(_MARGIN + 310, y, str(line.quantity), size=8.5)
        sheet.text(_MARGIN + 370, y, plain_money(line.unit_price), size=8.5)
        sheet.text(_MARGIN + 440, y, plain_money(line.value), size=8.5)
        y -= 14

    sheet.field("line_item_count", _MARGIN + 310, y - 8, str(len(shipment.lines)), size=8)
    y -= 26
    sheet.rule(_MARGIN + 300, y + 10, PAGE_WIDTH - _MARGIN)
    sheet.labelled(
        "invoice_total",
        _MARGIN + 310,
        y,
        label("total", language),
        money(shipment.invoice_total, symbol),
        size=11,
    )

    if shipment.net_weight_kg and generator.random() < 0.7:
        sheet.labelled(
            "total_net_weight",
            _MARGIN,
            y,
            label("net_weight", language),
            f"{shipment.net_weight_kg} KG",
        )

    if Pathology.INJECTION_ATTEMPT in pathologies:
        # A counterparty wrote this document. Text in it reaching an extraction prompt is
        # indirect prompt injection with money attached — the control already exists in
        # Attestor and this corpus exercises it rather than discovering it.
        sheet.text(_MARGIN, 96, f"{label('remarks', language)}:", size=7.5)
        sheet.text(_MARGIN, 86, generator.choice(INJECTION_STRINGS), size=7.5)

    _footer(sheet, shipment, language)
    return Rendered(
        "commercial_invoice",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


def _invoice_table_header(sheet: Sheet, y: float, language: Language) -> None:
    for offset, key in (
        (0, "description"),
        (250, "hs_code"),
        (310, "quantity"),
        (370, "unit_price"),
        (440, "amount"),
    ):
        sheet.text(_MARGIN + offset, y, label(key, language), size=7.5, font=BOLD)
    sheet.rule(_MARGIN, y - 4, PAGE_WIDTH - _MARGIN)


# ── Packing list ─────────────────────────────────────────────────────────────


def packing_list(shipment: Shipment, planted: Planted, generator: random.Random) -> Rendered:
    language = shipment.languages["packing_list"]
    pathologies = planted.pathologies.get("packing_list", ())
    sheet = Sheet(title=f"PL {shipment.shipment_id}")
    _header(sheet, "title_packing_list", language, shipment.seller.registered_name)

    gross = _planted(planted, "packing_list", PerturbedFact.GROSS_WEIGHT) or str(
        shipment.gross_weight_kg
    )

    # `docs/SCENARIO.md`: weights in kg and lb on the same page. The value printed is a genuine
    # conversion of the value that would otherwise be printed, so a system that handles units
    # agrees and one that assumes kilograms is out by a factor of 2.2 — which is a mismatch
    # nobody planted and the eval must not count as one.
    in_pounds = Pathology.POUNDS_NOT_KILOGRAMS in pathologies
    if in_pounds:
        gross_shown = f"{(Decimal(gross) / Decimal('0.45359237')).quantize(Decimal('1'))} LBS"
    else:
        gross_shown = f"{gross} KGS"

    y = _TOP - 46
    sheet.labelled(
        "packing_list_number",
        _MARGIN,
        y,
        label("invoice_number", language),
        f"PL-{shipment.shipment_id[-5:]}",
    )
    sheet.labelled(
        "container_number",
        PAGE_WIDTH / 2,
        y,
        label("container", language),
        shipment.container_number,
    )

    y -= 48
    sheet.box_outline(_MARGIN - 6, y - 58, PAGE_WIDTH - 2 * _MARGIN + 12, 82)
    sheet.labelled(
        "gross_weight", _MARGIN, y, label("gross_weight", language), gross_shown, size=11
    )
    sheet.labelled(
        "net_weight",
        PAGE_WIDTH / 2,
        y,
        label("net_weight", language),
        f"{shipment.net_weight_kg} KGS",
        size=11,
    )
    y -= 34
    sheet.labelled(
        "package_count",
        _MARGIN,
        y,
        label("packages", language),
        f"{shipment.package_count} CTNS",
        size=11,
    )
    sheet.labelled(
        "volume",
        PAGE_WIDTH / 2,
        y,
        label("volume", language),
        f"{shipment.volume_m3} M3",
        size=11,
    )

    y -= 60
    _invoice_table_header(sheet, y, language)
    y -= 16
    for line in shipment.lines[:8]:
        sheet.text(_MARGIN, y, line.description[:44], size=8.5)
        sheet.text(_MARGIN + 310, y, str(line.quantity), size=8.5)
        y -= 14

    _footer(sheet, shipment, language)
    return Rendered(
        "packing_list",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


# ── Certificate of origin ────────────────────────────────────────────────────


def certificate_of_origin(
    shipment: Shipment, planted: Planted, generator: random.Random
) -> Rendered:
    language = shipment.languages["certificate_of_origin"]
    pathologies = planted.pathologies.get("certificate_of_origin", ())
    sheet = Sheet(title=f"COO {shipment.shipment_id}")
    chamber = {
        "NL": "Kamer van Koophandel Rotterdam",
        "GR": "Εμπορικό και Βιομηχανικό Επιμελητήριο Πειραιώς",
        "TR": "Izmir Chamber of Commerce",
        "CN": "China Council for the Promotion of International Trade",
        "AE": "Dubai Chamber of Commerce",
    }.get(shipment.country_of_origin, "Chamber of Commerce")

    _header(sheet, "title_certificate_of_origin", language, chamber[:34])

    y = _TOP - 50
    sheet.labelled(
        "certificate_number",
        _MARGIN,
        y,
        label("certificate_number", language),
        f"COO/{shipment.sailed_on.year}/{shipment.shipment_id[-5:]}",
    )
    sheet.labelled(
        "issue_date",
        PAGE_WIDTH / 2,
        y,
        label("date", language),
        shipment.sailed_on.strftime("%d/%m/%Y"),
    )

    y -= 50
    sheet.labelled(
        "consignee", _MARGIN, y, label("consignee", language), shipment.buyer.form(generator)
    )
    sheet.labelled(
        "issuing_chamber", PAGE_WIDTH / 2, y, label("issuing_chamber", language), chamber[:30]
    )

    # The country field, given its own boxed area — and, roughly eight times in a hundred, the
    # chamber's stamp lands on it. The stamp is a raster operation applied later against this
    # placement, which is what makes it land *on* the field rather than near it.
    y -= 76
    sheet.box_outline(_MARGIN - 6, y - 14, 220, 46)
    sheet.labelled(
        "country_of_origin",
        _MARGIN,
        y,
        label("country_of_origin", language),
        shipment.country_of_origin,
        size=15,
    )

    y -= 70
    for line in shipment.lines[:5]:
        sheet.text(_MARGIN, y, f"{line.description[:50]}  ({line.hs_code})", size=8.5)
        y -= 13

    _footer(sheet, shipment, language)
    return Rendered(
        "certificate_of_origin",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


# ── Customs declaration ──────────────────────────────────────────────────────


def customs_declaration(shipment: Shipment, planted: Planted, generator: random.Random) -> Rendered:
    language = shipment.languages["customs_declaration"]
    pathologies = planted.pathologies.get("customs_declaration", ())
    sheet = Sheet(title=f"CD {shipment.shipment_id}")
    _header(sheet, "title_customs_declaration", language, "NORTHBRIDGE FORWARDING B.V.")

    value = _planted(planted, "customs_declaration", PerturbedFact.DECLARED_VALUE) or str(
        shipment.invoice_total
    )
    origin = _planted(planted, "customs_declaration", PerturbedFact.COUNTRY_OF_ORIGIN) or (
        shipment.country_of_origin
    )
    duty = (Decimal(value) * Decimal("0.043")).quantize(Decimal("0.01"))

    y = _TOP - 46
    sheet.labelled(
        "declaration_reference",
        _MARGIN,
        y,
        label("declaration_reference", language),
        f"NL{shipment.sailed_on.year}{shipment.shipment_id[-5:]}",
    )
    sheet.labelled(
        "declaration_date",
        PAGE_WIDTH / 2,
        y,
        label("date", language),
        shipment.arrives_on.strftime("%Y-%m-%d"),
    )

    y -= 46
    sheet.labelled(
        "declarant", _MARGIN, y, label("declarant", language), "Northbridge Forwarding B.V."
    )
    sheet.labelled(
        "procedure_code",
        PAGE_WIDTH / 2,
        y,
        label("procedure_code", language),
        generator.choice(("4000", "4200", "7100", "5100")),
    )

    y -= 52
    sheet.box_outline(_MARGIN - 6, y - 44, PAGE_WIDTH - 2 * _MARGIN + 12, 68)
    sheet.labelled(
        "declared_value",
        _MARGIN,
        y,
        label("declared_value", language),
        plain_money(Decimal(value)),
        size=11,
    )
    sheet.labelled(
        "currency", PAGE_WIDTH / 2 - 40, y, label("currency", language), shipment.currency, size=11
    )
    sheet.labelled(
        "duty_amount", PAGE_WIDTH / 2 + 90, y, label("duty", language), plain_money(duty), size=11
    )

    y -= 52
    sheet.labelled(
        "country_of_origin", _MARGIN, y, label("country_of_origin", language), origin, size=11
    )

    # The classification. `always_review` in the contract, because HS classification is
    # genuinely contested — the same goods are argued into different headings by competent
    # professionals, and a model reporting high confidence on one of those is worse than one
    # that abstains.
    sheet.labelled(
        "hs_code",
        PAGE_WIDTH / 2,
        y,
        label("hs_code", language),
        shipment.lines[0].hs_code,
        size=11,
    )

    _footer(sheet, shipment, language)
    return Rendered(
        "customs_declaration",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


# ── Arrival notice ───────────────────────────────────────────────────────────


def arrival_notice(shipment: Shipment, planted: Planted, generator: random.Random) -> Rendered:
    language = shipment.languages["arrival_notice"]
    pathologies = planted.pathologies.get("arrival_notice", ())
    sheet = Sheet(title=f"AN {shipment.shipment_id}")
    _header(sheet, "title_arrival_notice", language, shipment.carrier)

    container = _planted(planted, "arrival_notice", PerturbedFact.CONTAINER_NUMBER) or (
        shipment.container_number
    )
    packages = _planted(planted, "arrival_notice", PerturbedFact.PACKAGE_COUNT) or str(
        shipment.package_count
    )

    y = _TOP - 46
    sheet.labelled(
        "notice_reference",
        _MARGIN,
        y,
        label("notice_reference", language),
        f"AN{shipment.shipment_id[-6:]}",
    )
    sheet.labelled(
        "bill_of_lading_number",
        PAGE_WIDTH / 2,
        y,
        label("bl_number", language),
        shipment.bill_of_lading_number,
    )

    y -= 48
    sheet.labelled("container_number", _MARGIN, y, label("container", language), container, size=11)
    sheet.labelled(
        "estimated_arrival",
        PAGE_WIDTH / 2,
        y,
        label("estimated_arrival", language),
        shipment.arrives_on.strftime("%d %b %Y").upper(),
    )

    y -= 48
    sheet.labelled(
        "terminal",
        _MARGIN,
        y,
        label("terminal", language),
        f"{shipment.port_of_discharge[1]} Terminal {generator.randint(1, 9)}",
    )
    sheet.labelled(
        "package_count", PAGE_WIDTH / 2, y, label("packages", language), f"{packages} CTNS"
    )

    _footer(sheet, shipment, language)
    return Rendered(
        "arrival_notice",
        shipment.shipment_id,
        language,
        sheet.render(),
        tuple(sheet.placements),
        pathologies,
    )


def _footer(sheet: Sheet, shipment: Shipment, language: Language) -> None:
    sheet.rule(_MARGIN, 58, PAGE_WIDTH - _MARGIN, width=0.3)
    sheet.text(_MARGIN, 46, f"{shipment.shipment_id} / {sheet.page}", size=6.5)
    sheet.text(
        PAGE_WIDTH - _MARGIN - 190,
        46,
        "SYNTHETIC DOCUMENT — GENERATED FOR TESTING, NOT A REAL TRADE DOCUMENT",
        size=5.4,
    )


#: Every document type, so the generator iterates rather than enumerating.
BUILDERS: dict[str, Callable[[Shipment, Planted, random.Random], Rendered]] = {
    "bill_of_lading": bill_of_lading,
    "commercial_invoice": commercial_invoice,
    "packing_list": packing_list,
    "certificate_of_origin": certificate_of_origin,
    "customs_declaration": customs_declaration,
    "arrival_notice": arrival_notice,
}
