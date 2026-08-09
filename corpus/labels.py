"""Field labels in the three document languages.

**Field captions are not here.** They live in `contracts/documents/*.yaml` as `anchors`,
because extraction has to look for the same string the renderer printed and two descriptions of
one label diverge on the first busy afternoon. What remains here is what is *not* a field: the
document titles, the line-item table headers, and the words a form prints around its values.

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
