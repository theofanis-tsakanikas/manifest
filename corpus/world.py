"""The seeded world the corpus is generated from.

A shipment is a fact. Six documents describe it, written by four different parties, and each
one states a subset of the facts in its own words, its own language and its own units. That is
where the whole corpus comes from, and it is why the generator is part of the argument rather
than a fixture factory.

**Determinism.** One seed, one world, every time. `random.Random(seed)` explicitly, never the
module-level generator: a module-level `random` shared with anything else in the process makes
the corpus depend on what else ran first, and the failure appears months later as a fingerprint
that will not reproduce.

**The independence claim 4 rests on.** Mismatches are planted here, by perturbing a *fact*
about a shipment on *one* of its documents. This module does not read
`contracts/reconciliation/` and does not know which fields any rule compares. A planter that
consulted the contract to decide what to break, and a detector that consulted the same contract
to find it, would be one function agreeing with itself — which is the trap `PLAN.md` names, and
the reason the perturbation vocabulary below is written in terms of shipment facts rather than
rule ids.

The consequence, stated so the eval does not have to rediscover it: **one perturbation may trip
more than one rule.** Changing the gross weight on a packing list breaks its agreement with the
bill of lading and may break its agreement with the invoice's net weight. So ground truth
records the *perturbed fact*, and claim 4's expectation is "these shipments carry a
disagreement and those do not" — never "exactly these N rule firings", which would require the
generator to know the rules.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from manifest.core.checkdigit import check_digit

# ── The world's vocabulary ───────────────────────────────────────────────────

#: Owner codes. Real prefixes are registered to real carriers, so these are invented ones that
#: are *shaped* correctly — a corpus that used a live owner code would be putting a real
#: company's identifier on a fabricated document.
_OWNER_CODES: Final = ("XZQU", "QRVU", "WKLU", "ZPNU", "YHTU", "VBGU")

_PORTS: Final = (
    ("NLRTM", "Rotterdam"),
    ("GRPIR", "Piraeus"),
    ("CNSHA", "Shanghai"),
    ("AEJEA", "Jebel Ali"),
    ("DEHAM", "Hamburg"),
    ("TRIZM", "Izmir"),
    ("EGALY", "Alexandria"),
    ("SGSIN", "Singapore"),
)

_VESSELS: Final = (
    "MSC ARIADNE",
    "EVER LUCENT",
    "NORTHERN SPIRIT",
    "HANSA MERIDIAN",
    "CMA CGM THALIA",
    "OOCL PIRAEUS",
)

_GOODS: Final = (
    ("Ceramic floor tiles, glazed", "690722", Decimal("12.40")),
    ("Olive oil, virgin, in containers", "150910", Decimal("6.80")),
    ("Cotton bed linen, printed", "630221", Decimal("18.50")),
    ("Aluminium window frames", "761010", Decimal("41.00")),
    ("Electric water heaters, storage", "851610", Decimal("88.00")),
    ("Marble slabs, cut to size", "681591", Decimal("135.00")),
    ("Polypropylene woven sacks", "630533", Decimal("2.15")),
    ("Steel wire rope, not plated", "731210", Decimal("9.90")),
)


class Language(StrEnum):
    """The language a document is written in.

    Greek and Dutch are here because they are the languages of the two offices, and because
    `docs/AWS-CONSTRAINTS.md` establishes that no managed reader in the intended stack reads
    either. A corpus without them would leave that finding true and unexercised.
    """

    ENGLISH = "en"
    GREEK = "el"
    DUTCH = "nl"


class Pathology(StrEnum):
    """A deliberate difficulty planted on one document.

    Every one of these is named in `docs/SCENARIO.md`. They are recorded in ground truth so a
    claim can be scored *by pathology* — "abstention works" is much less useful than "abstention
    works on stamped fields and fails on bleed-through", and only the second tells anybody what
    to fix.
    """

    STAMP_OVER_FIELD = "stamp_over_field"
    TABLE_ACROSS_PAGE_BREAK = "table_across_page_break"
    CURRENCY_CONFUSION = "currency_confusion"
    HANDWRITTEN_CORRECTION = "handwritten_correction"
    ILLEGIBLE_FIELD = "illegible_field"
    BLEED_THROUGH = "bleed_through"
    INJECTION_ATTEMPT = "injection_attempt"
    POUNDS_NOT_KILOGRAMS = "pounds_not_kilograms"


class PerturbedFact(StrEnum):
    """What a planted mismatch changes.

    **Facts about a shipment, not fields in a contract.** This vocabulary is the independence
    claim 4 rests on: the planter names a fact, the detector names a rule, and neither knows
    the other's vocabulary.
    """

    GROSS_WEIGHT = "gross_weight"
    DECLARED_VALUE = "declared_value"
    CONTAINER_NUMBER = "container_number"
    PACKAGE_COUNT = "package_count"
    COUNTRY_OF_ORIGIN = "country_of_origin"


@dataclass(frozen=True, slots=True)
class Party:
    """One company, and the surface forms it appears under.

    `surface_forms` is claim 6's subject matter. The same party appears as its registered name
    on one document, an abbreviation on another, a transliteration on a third and an
    OCR-damaged version on a fourth — and a merge of any two of them must be reversible.
    """

    party_id: str
    registered_name: str
    surface_forms: tuple[str, ...]
    country: str

    def form(self, generator: random.Random) -> str:
        return generator.choice(self.surface_forms)


@dataclass(frozen=True, slots=True)
class LineItem:
    description: str
    hs_code: str
    quantity: int
    unit_price: Decimal

    @property
    def value(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class Shipment:
    """The fact every document in a set is describing.

    Assembled first, consistently. Documents are then written *from* it, and a planted mismatch
    is a document written from a deliberately altered copy of one of its values.
    """

    shipment_id: str
    seller: Party
    buyer: Party
    carrier: str
    vessel: str
    port_of_loading: tuple[str, str]
    port_of_discharge: tuple[str, str]
    container_number: str
    gross_weight_kg: Decimal
    net_weight_kg: Decimal
    package_count: int
    volume_m3: Decimal
    currency: str
    incoterm: str
    country_of_origin: str
    lines: tuple[LineItem, ...]
    sailed_on: date
    arrives_on: date
    languages: dict[str, Language] = field(default_factory=dict)
    pathologies: dict[str, tuple[Pathology, ...]] = field(default_factory=dict)

    @property
    def invoice_total(self) -> Decimal:
        return sum((line.value for line in self.lines), start=Decimal("0.00"))

    @property
    def bill_of_lading_number(self) -> str:
        return f"{self.carrier}{self.shipment_id[-6:]}"


@dataclass(frozen=True, slots=True)
class PlantedMismatch:
    """One deliberate disagreement, recorded before any document is rendered.

    `document` names which of the shipment's documents was written from the altered value.
    Everything else is the fact and its two versions, which is all claim 4's eval is allowed to
    know — it may not be told which rule this trips.
    """

    shipment_id: str
    document: str
    fact: PerturbedFact
    truth: str
    planted: str


def container_number(generator: random.Random) -> str:
    """A well-formed ISO 6346 number with a correct check digit.

    Correct on purpose. A corpus whose container numbers were invalid before any degradation
    would make the check digit's refusals meaningless — every read would be provably wrong, and
    the falsifier would be measuring the generator instead of the reader.
    """
    owner = generator.choice(_OWNER_CODES)
    serial = f"{generator.randrange(1_000_000):06d}"
    return f"{owner}{serial}{check_digit(owner + serial)}"


def _parties(generator: random.Random) -> list[Party]:
    """The party register, including the one that appears in five forms across two scripts.

    `docs/SCENARIO.md` requires exactly that case, and it is the one claim 6 is scored on.
    """
    return [
        Party(
            party_id="northbridge",
            registered_name="Northbridge Forwarding B.V.",
            surface_forms=(
                "Northbridge Forwarding B.V.",
                "NORTHBRIDGE FWD BV",
                "N. Bridge Forwarding B.V.",
                "NORTHBRIDGE FORWARDING",
                "北方桥货运",
            ),
            country="NL",
        ),
        Party(
            party_id="hellenic_marble",
            registered_name="Ελληνικά Μάρμαρα Α.Ε.",
            surface_forms=(
                "Ελληνικά Μάρμαρα Α.Ε.",
                "HELLENIC MARBLE SA",
                "Ellinika Marmara AE",
                "HELLENIC MARBLE",
            ),
            country="GR",
        ),
        Party(
            party_id="delta_ceramics",
            registered_name="Delta Ceramics Ltd",
            surface_forms=("Delta Ceramics Ltd", "DELTA CERAMICS LTD", "Delta Ceramics"),
            country="TR",
        ),
        Party(
            party_id="van_dijk",
            registered_name="Van Dijk Import B.V.",
            surface_forms=("Van Dijk Import B.V.", "VAN DIJK IMPORT BV", "van Dijk Import"),
            country="NL",
        ),
        Party(
            party_id="jebel_trading",
            registered_name="Jebel Trading LLC",
            surface_forms=("Jebel Trading LLC", "JEBEL TRADING", "شركة جبل للتجارة"),
            country="AE",
        ),
        Party(
            party_id="shanghai_hardware",
            registered_name="Shanghai Hardware Export Co",
            surface_forms=(
                "Shanghai Hardware Export Co",
                "SHANGHAI HARDWARE EXPORT",
                "上海五金出口公司",
            ),
            country="CN",
        ),
    ]


def build_world(seed: int, shipments: int) -> tuple[list[Shipment], list[Party]]:
    """Every shipment, consistent, before any mismatch is planted or any page is degraded."""
    generator = random.Random(seed)
    parties = _parties(generator)
    built: list[Shipment] = []

    for index in range(shipments):
        seller, buyer = generator.sample(parties, 2)
        loading, discharge = generator.sample(_PORTS, 2)
        line_count = generator.randint(2, 9)
        lines = tuple(
            LineItem(
                description=goods[0],
                hs_code=goods[1],
                quantity=generator.randrange(20, 900),
                unit_price=goods[2],
            )
            for goods in generator.choices(_GOODS, k=line_count)
        )
        net = Decimal(generator.randrange(1_200, 24_000))
        packages = generator.randrange(6, 640)
        sailed = date(2026, 1, 6) + timedelta(days=generator.randrange(0, 200))

        built.append(
            Shipment(
                shipment_id=f"SHP{index + 1:05d}",
                seller=seller,
                buyer=buyer,
                carrier=generator.choice(("MAEU", "MSCU", "CMDU", "OOLU")),
                vessel=generator.choice(_VESSELS),
                port_of_loading=loading,
                port_of_discharge=discharge,
                container_number=container_number(generator),
                gross_weight_kg=net + Decimal(generator.randrange(80, 1_400)),
                net_weight_kg=net,
                package_count=packages,
                volume_m3=Decimal(generator.randrange(8, 66)),
                currency=generator.choice(("EUR", "USD", "EUR", "EUR", "JPY")),
                incoterm=generator.choice(("CIF", "FOB", "DAP", "EXW", "CFR")),
                country_of_origin=seller.country,
                lines=lines,
                sailed_on=sailed,
                arrives_on=sailed + timedelta(days=generator.randrange(9, 34)),
                languages=_assign_languages(generator),
                pathologies={},
            )
        )
    return built, parties


def _assign_languages(generator: random.Random) -> dict[str, Language]:
    """Which language each document in this set is written in.

    Not one language per shipment: a Greek exporter's invoice is in Greek and the Dutch
    carrier's bill of lading is in English, which is what actually arrives at a forwarder and
    what makes the routing contract's language eligibility a per-page decision rather than a
    per-shipment one.
    """
    return {
        "commercial_invoice": generator.choices(
            [Language.ENGLISH, Language.GREEK, Language.DUTCH], weights=[5, 3, 2]
        )[0],
        "bill_of_lading": Language.ENGLISH,
        "packing_list": generator.choices(
            [Language.ENGLISH, Language.GREEK, Language.DUTCH], weights=[6, 2, 2]
        )[0],
        "certificate_of_origin": generator.choices(
            [Language.ENGLISH, Language.GREEK], weights=[7, 3]
        )[0],
        "customs_declaration": generator.choices(
            [Language.ENGLISH, Language.DUTCH], weights=[6, 4]
        )[0],
        "arrival_notice": Language.ENGLISH,
    }
