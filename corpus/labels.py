"""Field labels in the three document languages.

Separate from the layout code because they are data, and because putting them here makes the
one thing a reader of the corpus wants to check — *is this really Greek, or is it English with
Greek characters?* — checkable in one file.

The Dutch and Greek here are the terms a freight forwarder actually prints, not translations of
the English labels: `Vrachtbrief` rather than a rendering of "bill of lading", `Δασμός` rather
than a rendering of "duty". A corpus whose Greek is machine-translated English exercises the
reader's handling of Greek glyphs and nothing else, which is a smaller claim than the one
`docs/AWS-CONSTRAINTS.md` makes this corpus for.
"""

from __future__ import annotations

from typing import Final

from corpus.world import Language

Labels = dict[Language, str]

LABELS: Final[dict[str, Labels]] = {
    # Document titles
    "title_bill_of_lading": {
        Language.ENGLISH: "BILL OF LADING",
        Language.GREEK: "ΦΟΡΤΩΤΙΚΗ",
        Language.DUTCH: "VRACHTBRIEF",
    },
    "title_commercial_invoice": {
        Language.ENGLISH: "COMMERCIAL INVOICE",
        Language.GREEK: "ΤΙΜΟΛΟΓΙΟ",
        Language.DUTCH: "HANDELSFACTUUR",
    },
    "title_packing_list": {
        Language.ENGLISH: "PACKING LIST",
        Language.GREEK: "ΔΕΛΤΙΟ ΣΥΣΚΕΥΑΣΙΑΣ",
        Language.DUTCH: "PAKLIJST",
    },
    "title_certificate_of_origin": {
        Language.ENGLISH: "CERTIFICATE OF ORIGIN",
        Language.GREEK: "ΠΙΣΤΟΠΟΙΗΤΙΚΟ ΚΑΤΑΓΩΓΗΣ",
        Language.DUTCH: "CERTIFICAAT VAN OORSPRONG",
    },
    "title_customs_declaration": {
        Language.ENGLISH: "CUSTOMS DECLARATION",
        Language.GREEK: "ΤΕΛΩΝΕΙΑΚΗ ΔΙΑΣΑΦΗΣΗ",
        Language.DUTCH: "DOUANEAANGIFTE",
    },
    "title_arrival_notice": {
        Language.ENGLISH: "ARRIVAL NOTICE",
        Language.GREEK: "ΕΙΔΟΠΟΙΗΣΗ ΑΦΙΞΗΣ",
        Language.DUTCH: "AANKOMSTBERICHT",
    },
    # Fields
    "shipper": {
        Language.ENGLISH: "Shipper",
        Language.GREEK: "Αποστολέας",
        Language.DUTCH: "Afzender",
    },
    "consignee": {
        Language.ENGLISH: "Consignee",
        Language.GREEK: "Παραλήπτης",
        Language.DUTCH: "Geadresseerde",
    },
    "seller": {
        Language.ENGLISH: "Seller",
        Language.GREEK: "Πωλητής",
        Language.DUTCH: "Verkoper",
    },
    "buyer": {Language.ENGLISH: "Buyer", Language.GREEK: "Αγοραστής", Language.DUTCH: "Koper"},
    "vessel": {Language.ENGLISH: "Vessel", Language.GREEK: "Πλοίο", Language.DUTCH: "Schip"},
    "port_of_loading": {
        Language.ENGLISH: "Port of loading",
        Language.GREEK: "Λιμένας φόρτωσης",
        Language.DUTCH: "Laadhaven",
    },
    "port_of_discharge": {
        Language.ENGLISH: "Port of discharge",
        Language.GREEK: "Λιμένας εκφόρτωσης",
        Language.DUTCH: "Loshaven",
    },
    "container": {
        Language.ENGLISH: "Container no.",
        Language.GREEK: "Αρ. εμπορευματοκιβωτίου",
        Language.DUTCH: "Containernr.",
    },
    "gross_weight": {
        Language.ENGLISH: "Gross weight",
        Language.GREEK: "Μικτό βάρος",
        Language.DUTCH: "Brutogewicht",
    },
    "net_weight": {
        Language.ENGLISH: "Net weight",
        Language.GREEK: "Καθαρό βάρος",
        Language.DUTCH: "Nettogewicht",
    },
    "packages": {
        Language.ENGLISH: "Packages",
        Language.GREEK: "Δέματα",
        Language.DUTCH: "Colli",
    },
    "volume": {Language.ENGLISH: "Volume", Language.GREEK: "Όγκος", Language.DUTCH: "Volume"},
    "bl_number": {
        Language.ENGLISH: "B/L no.",
        Language.GREEK: "Αρ. φορτωτικής",
        Language.DUTCH: "Vrachtbriefnr.",
    },
    "invoice_number": {
        Language.ENGLISH: "Invoice no.",
        Language.GREEK: "Αρ. τιμολογίου",
        Language.DUTCH: "Factuurnr.",
    },
    "date": {Language.ENGLISH: "Date", Language.GREEK: "Ημερομηνία", Language.DUTCH: "Datum"},
    "total": {Language.ENGLISH: "Total", Language.GREEK: "Σύνολο", Language.DUTCH: "Totaal"},
    "currency": {
        Language.ENGLISH: "Currency",
        Language.GREEK: "Νόμισμα",
        Language.DUTCH: "Valuta",
    },
    "incoterm": {
        Language.ENGLISH: "Incoterm",
        Language.GREEK: "Όρος παράδοσης",
        Language.DUTCH: "Leveringsconditie",
    },
    "description": {
        Language.ENGLISH: "Description of goods",
        Language.GREEK: "Περιγραφή εμπορευμάτων",
        Language.DUTCH: "Omschrijving goederen",
    },
    "quantity": {
        Language.ENGLISH: "Qty",
        Language.GREEK: "Ποσότητα",
        Language.DUTCH: "Aantal",
    },
    "unit_price": {
        Language.ENGLISH: "Unit price",
        Language.GREEK: "Τιμή μονάδας",
        Language.DUTCH: "Prijs p/st",
    },
    "amount": {Language.ENGLISH: "Amount", Language.GREEK: "Αξία", Language.DUTCH: "Bedrag"},
    "hs_code": {
        Language.ENGLISH: "HS code",
        Language.GREEK: "Κωδικός Σ.Ο.",
        Language.DUTCH: "GS-code",
    },
    "country_of_origin": {
        Language.ENGLISH: "Country of origin",
        Language.GREEK: "Χώρα καταγωγής",
        Language.DUTCH: "Land van oorsprong",
    },
    "certificate_number": {
        Language.ENGLISH: "Certificate no.",
        Language.GREEK: "Αρ. πιστοποιητικού",
        Language.DUTCH: "Certificaatnr.",
    },
    "issuing_chamber": {
        Language.ENGLISH: "Issuing chamber",
        Language.GREEK: "Εκδούσα αρχή",
        Language.DUTCH: "Afgevende instantie",
    },
    "declaration_reference": {
        Language.ENGLISH: "Declaration ref.",
        Language.GREEK: "Αρ. διασάφησης",
        Language.DUTCH: "Aangiftenr.",
    },
    "declared_value": {
        Language.ENGLISH: "Customs value",
        Language.GREEK: "Δασμολογητέα αξία",
        Language.DUTCH: "Douanewaarde",
    },
    "duty": {Language.ENGLISH: "Duty", Language.GREEK: "Δασμός", Language.DUTCH: "Rechten"},
    "procedure_code": {
        Language.ENGLISH: "Procedure code",
        Language.GREEK: "Κωδικός καθεστώτος",
        Language.DUTCH: "Regelingcode",
    },
    "declarant": {
        Language.ENGLISH: "Declarant",
        Language.GREEK: "Διασαφιστής",
        Language.DUTCH: "Aangever",
    },
    "notice_reference": {
        Language.ENGLISH: "Notice ref.",
        Language.GREEK: "Αρ. ειδοποίησης",
        Language.DUTCH: "Berichtnr.",
    },
    "estimated_arrival": {
        Language.ENGLISH: "Estimated arrival",
        Language.GREEK: "Εκτιμώμενη άφιξη",
        Language.DUTCH: "Verwachte aankomst",
    },
    "terminal": {
        Language.ENGLISH: "Terminal",
        Language.GREEK: "Τερματικός σταθμός",
        Language.DUTCH: "Terminal",
    },
    "remarks": {
        Language.ENGLISH: "Remarks",
        Language.GREEK: "Παρατηρήσεις",
        Language.DUTCH: "Opmerkingen",
    },
    "continued": {
        Language.ENGLISH: "continued",
        Language.GREEK: "συνέχεια",
        Language.DUTCH: "vervolg",
    },
}


def label(key: str, language: Language) -> str:
    try:
        return LABELS[key][language]
    except KeyError as exc:
        raise KeyError(
            f"no label {key!r} in {language.value}. Every label is declared in all three "
            f"languages; falling back to English would produce a document that claims to be "
            f"Greek and is not, which is the one thing this corpus must not do"
        ) from exc
