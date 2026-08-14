"""Turning the committed recording and the committed ground truth into scored observations.

Shared by every claim harness, so that "what the system extracted" means one thing across all
of them. A second implementation of this would let two claims disagree about the same document
while both reported green.

**Nothing here reads a page.** The recording is the reader's output (ADR-0005) and the ground
truth is the generator's; both are committed, both are checked against their own digests, and
a harness that re-ran the binary would be a harness whose numbers move when a runner image
changes.

**A value is correct, wrong, or missing — and the three are not interchangeable.** Missing is
an abstention and is excluded from the error denominator, because counting it as an error would
make a reader that abstains score worse than one that guesses, which is the behaviour claim 1
exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from manifest.contracts.loader import ContractSet, default_root, load
from manifest.contracts.model import FieldContract
from manifest.core.calibration import Observation, Outcome
from manifest.core.checkdigit import check
from manifest.core.document import Page
from manifest.core.fields import Extracted, extract_from_pages
from manifest.core.geometry import Box
from manifest.core.text import compare, normalise
from manifest.extraction.local.recording import read_pages

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "corpus" / "ground_truth" / "corpus.json"


@dataclass(frozen=True, slots=True)
class Scored:
    """One extracted field, scored against what was actually printed."""

    shipment: str
    document: str
    field: str
    language: str
    pathologies: tuple[str, ...]
    truth: str
    extracted: Extracted
    outcome: Outcome
    #: The box the generator recorded when it drew the value. Claim 2's fixtures are built by
    #: deliberately corrupting this; the gate never sees it in normal operation.
    truth_box: Box | None
    truth_page: int | None

    @property
    def observation(self) -> Observation:
        return Observation(confidence=self.extracted.confidence, outcome=self.outcome)


@cache
def contracts() -> ContractSet:
    return load(default_root())


@cache
def ground_truth() -> dict:
    if not GROUND_TRUTH.exists():
        raise SystemExit(
            f"{GROUND_TRUTH} does not exist. Every claim here is scored against the committed "
            f"corpus; run `make corpus`"
        )
    return json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))


@cache
def recorded_pages(directory: Path | None = None) -> dict[tuple[str, str], tuple[Page, ...]]:
    """The recording's pages, grouped by document.

    `directory` defaults to the tier-0 recording, which is what every claim gate scores against.
    It is a parameter because a *second* reader can be recorded over the same corpus — Textract,
    under `recordings/textract/` — and the whole point of deriving a threshold for it is that the
    derivation is the same one, over the same labelled set, with only the confidences changed.

    A second scoring path would be two functions that could disagree about what a field is, and
    the docstring below already says why that is the thing to avoid.
    """
    grouped: dict[tuple[str, str], list[Page]] = {}
    for shipment, document, page in read_pages(directory) if directory else read_pages():
        grouped.setdefault((shipment, document), []).append(page)
    return {
        key: tuple(sorted(pages, key=lambda page: page.number)) for key, pages in grouped.items()
    }


def score_all(directory: Path | None = None) -> list[Scored]:
    """Every declared field on every document, extracted and scored.

    One pass, cached by the caller. Claims 1, 2 and 5 all read from the same list, which is
    what stops them describing different systems — and `directory` is how a second *reader* is
    scored by that same list rather than by a copy of it.
    """
    contract_set = contracts()
    pages_by_document = recorded_pages(directory)
    scored: list[Scored] = []

    for document in ground_truth()["documents"]:
        key = (document["shipment_id"], document["document_id"])
        pages = pages_by_document.get(key)
        if not pages:
            continue
        contract = contract_set.document(document["document_id"])
        printed = {entry["field"]: entry for entry in document["fields"]}

        for field in contract.fields:
            entry = printed.get(field.name)
            if entry is None:
                # A field the generator did not print on this document — an optional field on a
                # document that omitted it. Scoring it would count a correct abstention as a
                # miss, which is the reader being punished for the document's contents.
                continue
            anchor = field.anchors.get(document["language"])
            if not anchor:
                continue
            extracted = extract_from_pages(pages, field.name, anchor)
            scored.append(
                Scored(
                    shipment=document["shipment_id"],
                    document=document["document_id"],
                    field=field.name,
                    language=document["language"],
                    pathologies=tuple(document["pathologies"]),
                    truth=entry["value"],
                    extracted=extracted,
                    outcome=judge(field, entry["value"], extracted),
                    truth_box=Box(*entry["box"]),
                    truth_page=int(entry["page"]),
                )
            )
    return scored


def judge(field: FieldContract, truth: str, extracted: Extracted) -> Outcome:
    """Correct, wrong, or missing — under the field's own declared comparison rules.

    Two things this deliberately does **not** do.

    It does not repair. A value close to the truth is wrong, because in production there is no
    truth to be close to and the system would have published the near-miss.

    It does not accept a value the field's own arithmetic refuses. A container number whose
    check digit fails is wrong even if it happens to match ground truth after normalisation —
    that combination means the *generator* produced an invalid number, and treating it as
    correct would hide a corpus defect behind a passing claim.
    """
    if not extracted.found or extracted.value is None:
        return Outcome.MISSING

    rules = tuple(field.comparison)
    if field.type.value == "container_number" and check(extracted.value).refuses:
        return Outcome.WRONG

    if compare(truth, extracted.value, rules).agree:
        return Outcome.CORRECT

    # A value printed with its unit — `8959 KGS` — is extracted whole, and the contract's
    # comparison rules do not strip units because stripping them from a *name* would be
    # destructive. So a match on the leading token counts, and only where the truth is a prefix
    # of what was read: the other direction would accept `89` for `8959`.
    normalised_truth = normalise(truth, rules)
    normalised_value = normalise(extracted.value, rules)
    if normalised_truth and normalised_value.startswith(normalised_truth):
        return Outcome.CORRECT
    return Outcome.WRONG


def by_field(scored: list[Scored]) -> dict[str, list[Scored]]:
    """Grouped by field name, across document types.

    Across types on purpose: `container_number` on a bill of lading and on a packing list are
    the same *kind* of thing read off two layouts, and a threshold derived per document type
    would have a third of the evidence for no gain. Where a layout genuinely reads differently,
    the reliability curve shows it.
    """
    grouped: dict[str, list[Scored]] = {}
    for entry in scored:
        grouped.setdefault(entry.field, []).append(entry)
    return grouped


def field_contract(field: str) -> FieldContract:
    """The contract for a field name, from whichever document declares it first.

    Fields with the same name across document types carry the same type and the same
    comparison rules by construction — the loader refuses a reconciliation rule between two
    fields whose types differ, which is what keeps that true.
    """
    for contract in sorted(contracts().documents.values(), key=lambda entry: entry.id):
        for declared in contract.fields:
            if declared.name == field:
                return declared
    raise KeyError(f"no contract declares a field called {field!r}")
