"""Loading the contract set, and refusing it when it does not hold together.

Individual contracts validate themselves through their models. What cannot be checked one file
at a time is whether the **set** is coherent: a reconciliation rule naming a field that no
document declares, a rule comparing a weight against a count, a cascade tier nothing routes to.
Each of those is a contract that loads and then fails at run time, on a document, in front of
a customer — so each is checked here, at load, where the failure names the file.

`ContractSet` is the only object the rest of the system takes. Nothing imports a contract by
name, which is what makes adding a document type a change to a directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from manifest.contracts.model import (
    CascadeContract,
    DocumentContract,
    EntityContract,
    FieldType,
    ReconciliationContract,
    ReconciliationRule,
    ReviewAcceptances,
    ReviewCapacity,
    ToleranceSpec,
)
from manifest.core.quantity import Tolerance, ToleranceKind, dimension_of

#: Field types a reconciliation rule may compare. A rule over free text would be a string
#: comparison between two documents written by two parties, which disagrees constantly and for
#: reasons that are not errors — and a rule that fires constantly is a rule that gets muted.
_RECONCILABLE: frozenset[FieldType] = frozenset(
    {
        FieldType.QUANTITY,
        FieldType.MONEY,
        FieldType.CODE,
        FieldType.COUNTRY,
        FieldType.DATE,
        FieldType.CONTAINER_NUMBER,
        FieldType.HS_CODE,
    }
)


class ContractError(ValueError):
    """A contract set that does not hold together."""


@dataclass(frozen=True, slots=True)
class ContractSet:
    """Every contract, cross-checked."""

    documents: dict[str, DocumentContract]
    reconciliation: ReconciliationContract
    entities: EntityContract
    review: ReviewCapacity
    acceptances: ReviewAcceptances
    cascade: CascadeContract

    def document(self, document_id: str) -> DocumentContract:
        try:
            return self.documents[document_id]
        except KeyError as exc:
            raise ContractError(
                f"no document contract {document_id!r}; known: {sorted(self.documents)}"
            ) from exc

    @property
    def always_review_fields(self) -> tuple[tuple[str, str], ...]:
        """`(document, field)` for every field that never publishes automatically.

        The line item ADR-0001 requires to be visible rather than folded into a total: these
        consume 100% of their volume from the review queue, and they are what actually breaks
        the capacity budget.
        """
        return tuple(
            (contract.id, field.name)
            for contract in sorted(self.documents.values(), key=lambda c: c.id)
            for field in contract.fields
            if field.always_review
        )


def load(root: Path) -> ContractSet:
    """Read `contracts/` and refuse anything that does not hold together."""
    documents = {}
    for path in sorted((root / "documents").glob("*.yaml")):
        contract = _parse(path, DocumentContract)
        if contract.id in documents:
            raise ContractError(f"two document contracts claim the id {contract.id!r}")
        documents[contract.id] = contract
    if not documents:
        raise ContractError(f"no document contracts under {root / 'documents'}")

    reconciliation = _parse(root / "reconciliation" / "shipment.yaml", ReconciliationContract)
    entities = _parse(root / "entities" / "parties.yaml", EntityContract)
    review = _parse(root / "review" / "capacity.yaml", ReviewCapacity)
    acceptances = _parse(root / "review" / "acceptance.yaml", ReviewAcceptances)
    cascade = _parse(root / "cascade" / "routing.yaml", CascadeContract)

    contracts = ContractSet(
        documents=documents,
        reconciliation=reconciliation,
        entities=entities,
        review=review,
        acceptances=acceptances,
        cascade=cascade,
    )
    _cross_check(contracts)
    return contracts


def _parse[T](path: Path, model: type[T]) -> T:
    if not path.exists():
        raise ContractError(f"{path} does not exist")
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"{path} is not valid YAML: {exc}") from exc
    if payload is None:
        raise ContractError(f"{path} is empty")
    try:
        return model(**payload)
    except ValidationError as exc:
        raise ContractError(f"{path} does not satisfy {model.__name__}:\n{exc}") from exc


def _cross_check(contracts: ContractSet) -> None:
    for rule in contracts.reconciliation.rules:
        _check_rule(contracts, rule)
    _check_match_rules_can_fire(contracts)


def _check_match_rules_can_fire(contracts: ContractSet) -> None:
    """A match rule weighted below the merge threshold can never produce a merge.

    It is dead code in a data file, which is worse than dead code in a module: nothing compiles
    it, nothing greys it out, and it reads to a reviewer as a rule that is running. This was
    real — the entity contract declared a threshold of 0.85 and three of its four rules at 0.9,
    0.8 and 0.7, so only one of them could ever fire and the register resolved 21 surface forms
    into 18 entities while reporting no failure at all.
    """
    dead = [
        rule
        for rule in contracts.entities.rules
        if rule.weight < contracts.entities.merge_threshold
    ]
    if dead:
        raise ContractError(
            "match rule(s) "
            + ", ".join(f"{rule.id!r} (weight {rule.weight})" for rule in dead)
            + f" are weighted below the merge threshold of {contracts.entities.merge_threshold}, "
            f"so they can never produce a merge. A rule that cannot fire is dead code in a data "
            f"file: nothing compiles it and it reads as a rule that is running. Lower the "
            f"threshold or raise the weight, deliberately"
        )


def _check_rule(contracts: ContractSet, rule: ReconciliationRule) -> None:
    left = _resolve(contracts, rule, rule.left.document, rule.left.field)
    right = _resolve(contracts, rule, rule.right.document, rule.right.field)

    if left.type is not right.type:
        raise ContractError(
            f"rule {rule.id!r} compares a {left.type.value} against a {right.type.value}. "
            f"These do not disagree, they are not comparable — and a mismatch reported here "
            f"sends a reviewer to look for a discrepancy that does not exist, which spends "
            f"queue capacity ADR-0001 has already declared finite"
        )
    if left.type not in _RECONCILABLE:
        raise ContractError(
            f"rule {rule.id!r} reconciles {left.type.value} fields. Two parties writing the "
            f"same free text differently is not an error, so the rule would fire constantly — "
            f"and a rule that fires constantly is a rule somebody mutes"
        )
    if left.dimension is not right.dimension:
        raise ContractError(
            f"rule {rule.id!r} compares {left.dimension} against {right.dimension}; there is "
            f"no conversion between them and there should not be one"
        )
    if rule.left.document == rule.right.document:
        raise ContractError(
            f"rule {rule.id!r} reconciles {rule.left.document} against itself. Claim 4 is "
            f"about agreement *across* documents written by different parties; a within-"
            f"document check is a validation rule and belongs on the field"
        )

    # A tolerance whose unit is not the field's dimension is a rule that raises at run time on
    # the first document rather than at load, and by then it is in production.
    tolerance = to_tolerance(rule.tolerance)
    if (
        tolerance is not None
        and tolerance.unit is not None
        and left.dimension is not None
        and dimension_of(tolerance.unit) is not left.dimension
    ):
        raise ContractError(
            f"rule {rule.id!r} has a tolerance in {tolerance.unit.value} but compares "
            f"{left.dimension.value} fields"
        )


def _resolve(contracts: ContractSet, rule: ReconciliationRule, document: str, field: str) -> Any:
    try:
        return contracts.document(document).field(field)
    except (ContractError, KeyError) as exc:
        raise ContractError(
            f"rule {rule.id!r} names {document}.{field}, which does not exist"
        ) from exc


def to_tolerance(spec: ToleranceSpec) -> Tolerance | None:
    """The contract's tolerance as the core's type, or None where the rule demands exactness.

    `None` rather than a zero tolerance, because "these must be identical" and "these may
    differ by nothing" are the same arithmetic and different intentions, and the caller reads
    better when the exact case is a separate branch.
    """
    if spec.kind == "exact":
        return None
    kind = ToleranceKind.RELATIVE if spec.kind == "relative" else ToleranceKind.ABSOLUTE
    return Tolerance(kind=kind, amount=Decimal(spec.amount), unit=spec.unit)


def default_root() -> Path:
    """`contracts/` at the repository root.

    Resolved from this file's location rather than from the working directory, so that a test,
    a CLI and a batch job all find the same set. A contract set that depends on where the
    process started is a contract set that is different in production.
    """
    return Path(__file__).resolve().parents[3] / "contracts"
