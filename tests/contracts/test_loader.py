"""The contract set loads, and refuses.

A loader that only ever succeeds proves that the files parse. What matters is the refusals:
each one is a class of mistake that would otherwise be discovered at run time, on a document,
in front of a customer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from manifest.contracts.loader import ContractError, default_root, load
from manifest.contracts.model import DocumentContract, EntityContract, FieldContract, FieldType

ROOT = default_root()


@pytest.fixture(scope="session")
def contracts():
    return load(ROOT)


# ── The real set ─────────────────────────────────────────────────────────────


def test_the_committed_contract_set_loads_and_cross_checks(contracts) -> None:
    assert sorted(contracts.documents) == [
        "arrival_notice",
        "bill_of_lading",
        "certificate_of_origin",
        "commercial_invoice",
        "customs_declaration",
        "packing_list",
    ]
    assert len(contracts.reconciliation.rules) >= 1


def test_the_classification_field_never_publishes_automatically(contracts) -> None:
    """Claim 5's subject. HS classification is genuinely contested, so a model reporting high
    confidence on a contested item is worse than one that abstains — and this is a property of
    the consequence, declared in the contract, not of whatever the model happens to score."""
    assert ("customs_declaration", "hs_code") in contracts.always_review_fields
    assert contracts.document("customs_declaration").field("hs_code").error_budget is None


def test_the_review_capacity_is_the_number_adr_0001_argues_from(contracts) -> None:
    """Four reviewers, six productive hours, twenty seconds a decision. If any of these change,
    the doctrine's arithmetic changes with them, and the ADR should be reread."""
    assert round(contracts.review.decisions_per_day) == 4320


def test_greek_and_dutch_have_no_managed_reader(contracts) -> None:
    """The finding from `docs/AWS-CONSTRAINTS.md`, verified 2026-08-09, as data.

    Tier 1 is absent from these two lines and present on the other six. If a future
    documentation change adds Greek to a managed reader, this test is where it should be
    noticed — and its failure is good news that needs a citation, not a fix.
    """
    for language in ("el", "nl"):
        assert 1 not in contracts.cascade.eligible(language), language
    for language in ("en", "de", "fr", "es", "it", "pt"):
        assert 1 in contracts.cascade.eligible(language), language


def test_an_undeclared_language_gets_tier_zero_only(contracts) -> None:
    """Conservative on purpose. The alternative is a page in an unknown language sent to a
    reader that may not read it, which is the failure this contract exists to prevent."""
    assert contracts.cascade.eligible("xx") == (0,)


def test_every_personal_data_field_says_why_and_under_what_basis(contracts) -> None:
    """GDPR Art. 5(1)(b). A field that carries a name and cannot say why is a compliance
    problem arriving behind a good intention."""
    for contract in contracts.documents.values():
        for field in contract.fields:
            if field.personal_data is not None:
                assert field.personal_data.purpose
                assert "Art." in field.personal_data.lawful_basis, f"{contract.id}.{field.name}"


# ── The refusals ─────────────────────────────────────────────────────────────


def _field(**overrides: Any) -> dict[str, Any]:
    base = {
        "name": "gross_weight",
        "type": "quantity",
        "dimension": "mass",
        "unit": "kg",
        "description": "Total gross weight of the shipment as the carrier states it.",
        "error_budget": 0.005,
    }
    base.update(overrides)
    return base


def test_a_field_with_no_error_budget_cannot_load() -> None:
    """The rule the whole of claim 1 rests on. A field without a budget has no derivable
    threshold, so publishing it automatically would mean choosing a number — silently, as a
    default, which is the practice this project replaces."""
    with pytest.raises(ValueError, match="no error budget"):
        FieldContract(**_field(error_budget=None))


def test_a_field_cannot_declare_both_a_budget_and_always_review() -> None:
    """Alternative answers to one question. A contract that gives both has not answered it."""
    with pytest.raises(ValueError, match="alternative answers"):
        FieldContract(**_field(always_review=True))


def test_always_review_without_a_budget_is_the_valid_pairing() -> None:
    field = FieldContract(**_field(always_review=True, error_budget=None))
    assert field.always_review


def test_a_mistyped_key_is_a_load_failure_rather_than_a_missing_declaration() -> None:
    """`error_budjet: 0.01` would otherwise load with no budget at all, and the refusal above
    would never fire — because the key it looks for is genuinely absent."""
    payload = _field()
    payload["error_budjet"] = payload.pop("error_budget")
    with pytest.raises(ValueError, match="error_budjet"):
        FieldContract(**payload)


def test_a_party_field_must_decide_whether_it_can_be_a_person() -> None:
    """A sole trader is a natural person. A consignee field that never considered the question
    is how personal data gets processed without anyone deciding to."""
    with pytest.raises(ValueError, match="never a natural person"):
        FieldContract(
            name="consignee",
            type=FieldType.PARTY,
            description="The party to whom the goods are consigned.",
            error_budget=0.01,
        )


def test_personal_data_with_transient_retention_is_refused() -> None:
    """Extracted and then dropped. If it is genuinely not needed, do not extract it — Art.
    5(1)(c) minimisation is the cheapest control available here."""
    with pytest.raises(ValueError, match="minimisation"):
        FieldContract(
            name="consignee",
            type=FieldType.TEXT,
            description="The party to whom the goods are consigned.",
            error_budget=0.01,
            retention="transient",
            personal_data={
                "purpose": "Identifying the parties to a shipment for the declaration.",
                "lawful_basis": "Art. 6(1)(c) GDPR",
            },
        )


def test_a_quantity_with_no_dimension_cannot_load() -> None:
    with pytest.raises(ValueError, match="no dimension"):
        FieldContract(**_field(dimension=None))


def test_duplicate_field_names_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate field names"):
        DocumentContract(
            id="test_document",
            title="Test",
            description="A document contract used only by this test, and not by anything else.",
            version=1,
            fields=[_field(), _field()],
        )


def test_a_merge_threshold_may_not_claim_to_be_derived() -> None:
    """There is no labelled set of same-party judgements large enough to derive one honestly,
    and claiming otherwise would be the fabrication ADR-0002 exists to refuse."""
    with pytest.raises(ValueError, match="fabrication"):
        EntityContract(
            version=1,
            merge_threshold=0.85,
            merge_threshold_is_derived=True,
            rules=[
                {
                    "id": "exact",
                    "explanation": "The same string, differing only in case and spacing.",
                    "rules": ["unicode", "case"],
                    "weight": 1.0,
                }
            ],
        )


# ── Cross-set refusals, which no single file can catch ───────────────────────


def _set_with_rule(tmp_path: Path, rule: dict[str, Any]) -> None:
    """Copy the real contract set, replace the reconciliation rules, and load it."""
    # Every directory under `contracts/`, discovered rather than listed. A hardcoded list goes
    # stale the day a contract family is added — and it goes stale silently, as two unrelated
    # tests failing with "does not exist" while the thing they assert still works.
    for directory in sorted(path for path in ROOT.iterdir() if path.is_dir()):
        (tmp_path / directory.name).mkdir(parents=True)
        for source in directory.glob("*.yaml"):
            (tmp_path / directory.name / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    target = tmp_path / "reconciliation" / "shipment.yaml"
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    payload["rules"] = [rule]
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    load(tmp_path)


def test_a_rule_naming_a_field_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="does not exist"):
        _set_with_rule(
            tmp_path,
            {
                "id": "invented",
                "description": "A rule naming a field nobody declared anywhere.",
                "left": {"document": "bill_of_lading", "field": "no_such_field"},
                "right": {"document": "packing_list", "field": "gross_weight"},
                "tolerance": {"kind": "exact"},
            },
        )


def test_a_rule_comparing_two_different_types_is_refused(tmp_path: Path) -> None:
    """These do not disagree, they are not comparable — and a mismatch reported here sends a
    reviewer to look for a discrepancy that does not exist, which spends queue capacity
    ADR-0001 has already declared finite."""
    with pytest.raises(ContractError, match="not comparable"):
        _set_with_rule(
            tmp_path,
            {
                "id": "weight_against_a_code",
                "description": "Comparing a weight against a port code, which cannot disagree.",
                "left": {"document": "bill_of_lading", "field": "gross_weight"},
                "right": {"document": "bill_of_lading", "field": "port_of_loading"},
                "tolerance": {"kind": "exact"},
            },
        )


def test_a_rule_over_free_text_is_refused(tmp_path: Path) -> None:
    """Two parties writing the same text differently is not an error, so the rule would fire
    constantly — and a rule that fires constantly is a rule somebody mutes."""
    with pytest.raises(ContractError, match="mutes"):
        _set_with_rule(
            tmp_path,
            {
                "id": "vessel_against_terminal",
                "description": "Comparing free text written by two different parties.",
                "left": {"document": "bill_of_lading", "field": "vessel_name"},
                "right": {"document": "arrival_notice", "field": "terminal"},
                "tolerance": {"kind": "exact"},
            },
        )


def test_a_rule_reconciling_a_document_against_itself_is_refused(tmp_path: Path) -> None:
    """Claim 4 is about agreement across documents written by different parties. A
    within-document check is a validation rule and belongs on the field."""
    with pytest.raises(ContractError, match="against itself"):
        _set_with_rule(
            tmp_path,
            {
                "id": "self_check",
                "description": "Comparing a document's own two weights, which is validation.",
                "left": {"document": "packing_list", "field": "gross_weight"},
                "right": {"document": "packing_list", "field": "net_weight"},
                "tolerance": {"kind": "relative", "amount": 0.01},
            },
        )


def test_a_tolerance_in_the_wrong_dimension_is_refused_at_load(tmp_path: Path) -> None:
    """Otherwise it raises at run time on the first document, and by then it is in production."""
    with pytest.raises(ContractError, match="tolerance in"):
        _set_with_rule(
            tmp_path,
            {
                "id": "weight_with_a_carton_tolerance",
                "description": "A mass comparison whose tolerance is expressed in cartons.",
                "left": {"document": "bill_of_lading", "field": "gross_weight"},
                "right": {"document": "packing_list", "field": "gross_weight"},
                "tolerance": {"kind": "absolute", "amount": 1, "unit": "ctn"},
            },
        )
