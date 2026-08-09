"""Reconciliation, versioning and entity resolution — the pure halves of claims 3, 4 and 6.

Fast, no corpus, no reader. The evals score these on 500 shipments; these state the behaviours
the evals then measure, so a change to any of them fails in milliseconds rather than in four
minutes.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from manifest.core.entities import (
    MatchRule,
    Mention,
    candidate,
    resolve,
    score,
    unmerge,
)
from manifest.core.quantity import Tolerance, ToleranceKind, Unit
from manifest.core.reconciliation import (
    Comparison,
    Outcome,
    Severity,
    Side,
    reconcile,
    summarise,
)
from manifest.core.text import Rule
from manifest.core.versioning import (
    Change,
    PublishedField,
    diff,
    publish,
    surviving_decisions,
)

RELATIVE = Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.005"))


def _comparison(tolerance=RELATIVE, numeric: bool = True) -> Comparison:
    return Comparison(
        rule_id="gross_weight_bol_against_packing_list",
        severity=Severity.BLOCKING,
        tolerance=tolerance,
        comparison=(Rule.UNICODE, Rule.WHITESPACE, Rule.CASE),
        numeric=numeric,
    )


def _side(document: str, value: str | None, unit: Unit | None = Unit.KILOGRAM) -> Side:
    return Side(document=document, field="gross_weight", value=value, unit=unit)


# ── Claim 4 ──────────────────────────────────────────────────────────────────


def test_the_same_weight_in_two_units_agrees() -> None:
    """`docs/SCENARIO.md`'s pathology, and the one that broke the harness first.

    Both fields declare kilograms; the packing list prints pounds. Reading the printed token
    is what makes these agree — and trusting the contract's unit instead reported 384 shipments
    that agree as disagreeing.
    """
    finding = reconcile(
        "SHP1",
        _comparison(),
        _side("bill_of_lading", "27000 KGS"),
        _side("packing_list", "59525 LBS"),
    )
    assert finding.outcome is Outcome.AGREE


def test_a_unit_token_of_the_wrong_dimension_falls_back_to_the_contract() -> None:
    """A reader returning `CTNS` on a mass field has misread, and the conservative move is to
    compare the declared dimension in the declared unit — which disagrees loudly — rather than
    to compare two different dimensions, which could agree by accident."""
    finding = reconcile(
        "SHP1",
        _comparison(),
        _side("bill_of_lading", "27000 KGS"),
        _side("packing_list", "59525 CTNS"),
    )
    assert finding.outcome is Outcome.DISAGREE


def test_a_planted_four_percent_difference_disagrees() -> None:
    finding = reconcile(
        "SHP1", _comparison(), _side("bill_of_lading", "27000"), _side("packing_list", "28080")
    )
    assert finding.outcome is Outcome.DISAGREE
    assert "outside a tolerance" in finding.explanation


def test_an_abstention_is_not_an_agreement() -> None:
    """The failure that would make claim 4 report zero mismatches on a corpus it could not
    read."""
    finding = reconcile(
        "SHP1", _comparison(), _side("bill_of_lading", "27000"), _side("packing_list", None)
    )
    assert finding.outcome is Outcome.NOT_COMPARABLE
    assert not finding.is_disagreement


def test_a_value_that_is_not_a_number_is_an_extraction_problem_not_a_disagreement() -> None:
    """Reporting it as a disagreement sends a reviewer to compare two documents when the fault
    is in one reading."""
    finding = reconcile(
        "SHP1", _comparison(), _side("bill_of_lading", "27000"), _side("packing_list", "KGS")
    )
    assert finding.outcome is Outcome.NOT_COMPARABLE
    assert "extraction problem" in finding.explanation


def test_nothing_in_reconciliation_picks_a_side() -> None:
    """The finding carries both values and no resolution. A `value` property returning "the"
    weight is how smoothing arrives, and it always looks helpful."""
    finding = reconcile(
        "SHP1", _comparison(), _side("bill_of_lading", "27000"), _side("packing_list", "28080")
    )
    assert finding.left.value == "27000"
    assert finding.right.value == "28080"
    assert not hasattr(finding, "value")


def test_the_summary_counts_shipments_rather_than_rule_firings() -> None:
    """One altered weight may break two rules, and the generator does not know how many rules
    exist. Counting shipments is a statement about the world; counting firings is a statement
    about the contract."""
    findings = [
        reconcile("SHP1", _comparison(), _side("a", "1"), _side("b", "2")),
        reconcile("SHP1", _comparison(), _side("c", "1"), _side("d", "2")),
    ]
    assert summarise(findings)["shipments_with_a_disagreement"] == ["SHP1"]


# ── Claim 3 ──────────────────────────────────────────────────────────────────


def _field(name: str, value: str | None) -> PublishedField:
    return PublishedField(
        field=name, value=value, confidence=0.9, page=1, box=(0.1, 0.2, 0.1, 0.01)
    )


def _version(reader: str = "reader@1", **values: str | None):
    return publish(
        document_id="SHP1/bill_of_lading",
        source_digest="abc",
        reader=reader,
        contract_version=1,
        fields=[_field(name, value) for name, value in values.items()],
    )


def test_the_same_input_publishes_the_same_version() -> None:
    assert _version(gross_weight="27000").version == _version(gross_weight="27000").version


def test_field_order_does_not_change_a_version() -> None:
    """A version that depended on emission order would differ between two runs that published
    exactly the same thing, and claim 3 would be false for a reason nobody could find."""
    one = publish(
        document_id="d",
        source_digest="s",
        reader="r",
        contract_version=1,
        fields=[_field("a", "1"), _field("b", "2")],
    )
    other = publish(
        document_id="d",
        source_digest="s",
        reader="r",
        contract_version=1,
        fields=[_field("b", "2"), _field("a", "1")],
    )
    assert one.version == other.version


def test_a_reader_change_produces_a_new_version() -> None:
    """A reader change that leaves the identifier alone is a silent overwrite."""
    assert (
        _version(gross_weight="27000").version != _version("reader@2", gross_weight="27000").version
    )


def test_a_contract_version_change_produces_a_new_version() -> None:
    one = _version(gross_weight="27000")
    other = publish(
        document_id=one.document_id,
        source_digest=one.source_digest,
        reader=one.reader,
        contract_version=2,
        fields=one.fields,
    )
    assert one.version != other.version


def test_a_diff_carries_the_value_it_changed_from() -> None:
    difference = diff(_version(gross_weight="27000"), _version(gross_weight="28080"))
    change = difference.material[0]
    assert change.change is Change.CHANGED
    assert change.before == "27000"
    assert change.after == "28080"


def test_an_abstention_after_a_publication_is_withdrawn_not_changed() -> None:
    """A reader that stopped guessing is a reader that got better, and the two are different
    events in a re-processing report."""
    difference = diff(_version(gross_weight="27000"), _version(gross_weight=None))
    assert difference.material[0].change is Change.WITHDRAWN


def test_diffing_two_different_documents_is_refused() -> None:
    other = publish(
        document_id="SHP2/bill_of_lading",
        source_digest="abc",
        reader="reader@1",
        contract_version=1,
        fields=[_field("gross_weight", "27000")],
    )
    with pytest.raises(ValueError, match="different documents"):
        diff(_version(gross_weight="27000"), other)


def test_a_decision_on_a_changed_field_is_requeued_and_one_on_an_unchanged_field_survives() -> None:
    """Decision 12, and the place an optimisation would be a lie: carrying a decision across a
    changed value preserves the record of oversight while destroying what it was oversight of."""
    difference = diff(
        _version(gross_weight="27000", container_number="CSQU3054383"),
        _version(gross_weight="28080", container_number="CSQU3054383"),
    )
    survives, requeue = surviving_decisions(
        {"gross_weight": "approved", "container_number": "approved"}, difference
    )
    assert list(survives) == ["container_number"]
    assert requeue == ("gross_weight",)


# ── Claim 6 ──────────────────────────────────────────────────────────────────

RULES = (
    MatchRule(
        "exact",
        "identical after case and spacing",
        (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE),
        Decimal("1.0"),
    ),
    MatchRule(
        "legal_form",
        "identical ignoring a legal form",
        (Rule.UNICODE, Rule.WHITESPACE, Rule.CASE, Rule.LEGAL_FORM),
        Decimal("0.8"),
    ),
)


def _mentions(*names: str) -> list[Mention]:
    return [
        Mention(mention_id=f"m{index}", name=name, document="d", shipment="s")
        for index, name in enumerate(names)
    ]


def test_the_same_company_with_and_without_its_legal_form_merges() -> None:
    entities = resolve(
        _mentions("Northbridge Forwarding BV", "NORTHBRIDGE FORWARDING"), RULES, Decimal("0.65")
    )
    assert len(entities) == 1
    assert entities[0].merged


def test_two_different_companies_do_not_merge() -> None:
    """The half that matters. A resolver that merges everything scores perfectly on the other
    half."""
    entities = resolve(
        _mentions("Hellenic Marble SA", "Hellenic Marine SA"), RULES, Decimal("0.65")
    )
    assert len(entities) == 2


def test_a_near_match_is_a_candidate_for_a_human_and_never_a_merge() -> None:
    """The test that changed the design.

    `Hellenic Marble SA` and `Hellenic Marine SA` are 89% similar, and a scored near-match
    merged them. Reader damage on a party name sits in the same similarity band as two
    different companies, so no threshold separates them — and a resolver that merges on
    similarity is not being cautious, it is claiming a distinction the signal does not contain.
    """
    suggestion = candidate("Hellenic Marble SA", "Hellenic Marine SA", RULES)
    assert suggestion is not None
    assert suggestion.is_candidate
    assert "not a merge" in suggestion.explanation
    assert score("Hellenic Marble SA", "Hellenic Marine SA", RULES) is None


def test_the_canonical_name_does_not_depend_on_document_order() -> None:
    forwards = resolve(
        _mentions("NORTHBRIDGE FORWARDING", "Northbridge Forwarding BV"), RULES, Decimal("0.65")
    )
    backwards = resolve(
        _mentions("Northbridge Forwarding BV", "NORTHBRIDGE FORWARDING"), RULES, Decimal("0.65")
    )
    assert forwards[0].canonical_name == backwards[0].canonical_name
    assert forwards[0].entity_id == backwards[0].entity_id


def test_an_unmerge_repoints_every_downstream_record() -> None:
    entity = resolve(
        _mentions("Northbridge Forwarding BV", "NORTHBRIDGE FORWARDING"), RULES, Decimal("0.65")
    )[0]
    references = {"invoice-1": "m0", "declaration-9": "m1"}
    undone = unmerge(entity, references, RULES, Decimal("0.65"))
    assert len(undone.replacements) == 2
    assert set(undone.repointed) == set(references)
    assert set(undone.repointed.values()) <= {entity.entity_id for entity in undone.replacements}


def test_an_unmerge_that_would_leave_a_dangling_pointer_is_refused() -> None:
    """A partial un-merge is worse than none: the pointer is invisible until somebody follows
    it."""
    entity = resolve(
        _mentions("Northbridge Forwarding BV", "NORTHBRIDGE FORWARDING"), RULES, Decimal("0.65")
    )[0]
    with pytest.raises(ValueError, match="dangling pointer"):
        unmerge(entity, {"invoice-1": "m0", "orphan": "m99"}, RULES, Decimal("0.65"))


def test_a_merge_carries_the_rule_and_the_score_that_produced_it() -> None:
    """An unexplained merge is one nobody can audit."""
    entity = resolve(
        _mentions("Northbridge Forwarding BV", "NORTHBRIDGE FORWARDING"), RULES, Decimal("0.65")
    )[0]
    assert entity.matches
    assert all(match.explanation and match.rule_id for match in entity.matches)
