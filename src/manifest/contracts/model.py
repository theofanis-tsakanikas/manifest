"""The contract schema. The model *is* the schema.

A separate JSON Schema beside these classes would be a second description of one contract, and
the two diverge on the first busy afternoon. Same rule as Attestor.

Contracts are **data**. No module imports one by name; they are loaded from `contracts/` as a
set and cross-checked as a set. That is what makes adding a document type a change to a
directory rather than a change to a code path.

The rules that make loading a refusal rather than a formality:

- **A field with no error budget cannot load.** The budget is what the threshold is derived
  from (ADR-0002). A field without one has no derivable threshold, so publishing it
  automatically would mean choosing a number — which is the practice this project exists to
  replace, and it would arrive silently, as a default.
- **A reconciliation rule with no tolerance cannot load.** Same rule, same reason: a comparison
  whose author did not decide what "agree" means.
- **A personal-data field with no purpose and no retention class cannot load.** GDPR Art.
  5(1)(b) and 5(1)(e); a field that carries a name and cannot say why is a compliance problem
  arriving behind a good intention.
- **A field marked `always_review` may not also declare a threshold.** They are alternative
  answers to one question, and a contract that gives both has not answered it.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from manifest.core.quantity import Dimension, Unit
from manifest.core.text import Rule


class Strict(BaseModel):
    """Every contract model forbids unknown keys.

    A typo in a contract key is otherwise a silently ignored declaration — `error_budjet: 0.01`
    loads, the field has no budget, and the loader's refusal never fires because the key it
    looks for is genuinely absent. Forbidding extras turns that into a load failure naming the
    key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldType(StrEnum):
    """What a field is, which decides how it is compared and what may check it."""

    TEXT = "text"
    CODE = "code"
    COUNTRY = "country"
    DATE = "date"
    QUANTITY = "quantity"
    MONEY = "money"
    CONTAINER_NUMBER = "container_number"
    HS_CODE = "hs_code"
    PARTY = "party"


class Retention(StrEnum):
    """How long a value is kept, and under which obligation.

    `customs_record` is the UCC Art. 51 class — at least three years from the end of the
    relevant year, and longer where national law says so, which is why the *period* is not
    written here. `docs/REGULATORY.md`: the floor is traced and the national extensions are
    not, so this names the class and the jurisdiction resolves the number.
    """

    CUSTOMS_RECORD = "customs_record"
    OPERATIONAL = "operational"
    TRANSIENT = "transient"


class PersonalData(Strict):
    """Why a field that identifies a person is extracted at all.

    Both parts are required. "We might need it" is not a purpose, and a retention class with no
    purpose cannot be justified to the person it is about.
    """

    purpose: Annotated[str, Field(min_length=10)]
    lawful_basis: Annotated[str, Field(min_length=3)]


class FieldContract(Strict):
    """One extractable field.

    `error_budget` is the most consequential number in this file: the acceptable rate of
    **published and wrong**, from which ADR-0002 derives the confidence threshold. It is not a
    target and not an expectation — it is the rate at which this field being wrong is
    tolerable, decided by what a wrong value costs.
    """

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    type: FieldType
    description: Annotated[str, Field(min_length=10)]
    required: bool = True

    #: The acceptable rate of published-and-wrong. `None` only where `always_review` is set.
    error_budget: Annotated[Decimal, Field(gt=0, lt=1)] | None = None

    #: This field never publishes without a human decision, whatever its confidence. Declared
    #: here when the *consequence* demands it; ADR-0002 also sets it at derivation time when no
    #: threshold fits the budget at the available N.
    always_review: bool = False

    #: How far a derived threshold may move between recordings before CI refuses (ADR-0002).
    #: Without one, the first extra document turns the build red and somebody deletes the check.
    threshold_tolerance: Annotated[Decimal, Field(gt=0, lt=1)] = Decimal("0.02")

    retention: Retention = Retention.CUSTOMS_RECORD
    personal_data: PersonalData | None = None

    #: Which normalisations apply when this field's value is compared, in order.
    comparison: tuple[Rule, ...] = (Rule.UNICODE, Rule.WHITESPACE)

    #: For `QUANTITY` and `MONEY`: what is being measured, and the unit the document states it
    #: in. A quantity field with no dimension cannot be reconciled against another document.
    dimension: Dimension | None = None
    unit: Unit | None = None

    #: The caption this field is printed under, per language. **One source of truth, used from
    #: both ends**: the corpus renders the caption from here, and extraction looks for it here.
    #:
    #: That shared use is deliberate and it is not a tautology. The anchor is how a field is
    #: *found*; what claims 1 and 2 measure is whether the value beside it was read correctly
    #: and located correctly, and ground truth for the value is recorded independently at the
    #: moment it is drawn. Duplicating the caption into the generator instead would give two
    #: descriptions of one label, and they would diverge on the first busy afternoon.
    anchors: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _budget_or_always_review(self) -> Self:
        if self.always_review and self.error_budget is not None:
            raise ValueError(
                f"field {self.name!r} declares both an error budget and always_review. They "
                f"are alternative answers to one question — what rate of wrong publication is "
                f"tolerable — and a contract that gives both has not answered it"
            )
        if not self.always_review and self.error_budget is None:
            raise ValueError(
                f"field {self.name!r} has no error budget. The confidence threshold is derived "
                f"from it (ADR-0002); a field without one has no derivable threshold, so "
                f"publishing it automatically would mean choosing a number — which is the "
                f"practice this project replaces, arriving silently as a default. Declare an "
                f"error_budget, or declare always_review and accept the queue cost"
            )
        return self

    @model_validator(mode="after")
    def _personal_data_is_justified(self) -> Self:
        # A party is a company more often than a person, so this is a requirement shaped as a
        # forced decision rather than an assumption: declare `personal_data` with a purpose, or
        # state in the description that this party is never a natural person.
        if (
            self.type is FieldType.PARTY
            and self.personal_data is None
            and "never a natural person" not in self.description
        ):
            raise ValueError(
                f"field {self.name!r} is a party. Either declare personal_data with a "
                f"purpose and a lawful basis, or state in the description that this party "
                f"is 'never a natural person'. A sole trader is a natural person, and a "
                f"consignee field that never considered the question is how personal data "
                f"gets processed without anyone deciding to"
            )
        if self.personal_data is not None and self.retention is Retention.TRANSIENT:
            raise ValueError(
                f"field {self.name!r} carries personal data with transient retention, which "
                f"means it is extracted and then dropped. If it is genuinely not needed, do "
                f"not extract it — Art. 5(1)(c) minimisation is the cheapest control here"
            )
        return self

    @model_validator(mode="after")
    def _quantities_declare_what_they_measure(self) -> Self:
        needs_dimension = {FieldType.QUANTITY, FieldType.MONEY}
        if self.type in needs_dimension and self.dimension is None:
            raise ValueError(
                f"field {self.name!r} is a {self.type.value} with no dimension; it cannot be "
                f"reconciled against another document, which is the only reason to extract it"
            )
        if self.type not in needs_dimension and self.dimension is not None:
            raise ValueError(f"field {self.name!r} is a {self.type.value} and has no dimension")
        return self


class DocumentContract(Strict):
    """One document type: what is extracted from it, and under what budget."""

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    title: Annotated[str, Field(min_length=3)]
    description: Annotated[str, Field(min_length=20)]
    version: Annotated[int, Field(ge=1)]
    fields: Annotated[tuple[FieldContract, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _field_names_are_unique(self) -> Self:
        names = [field.name for field in self.fields]
        duplicated = {name for name in names if names.count(name) > 1}
        if duplicated:
            raise ValueError(f"{self.id}: duplicate field names {sorted(duplicated)}")
        return self

    def field(self, name: str) -> FieldContract:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(f"{self.id} has no field {name!r}")


class ToleranceSpec(Strict):
    """A reconciliation tolerance, as data.

    Mirrors `core.quantity.Tolerance` rather than reusing it, because a contract is a document
    and the core type is arithmetic. The loader converts, and the conversion is where a
    contract's mistakes become a load failure instead of a runtime one.
    """

    kind: Annotated[str, Field(pattern=r"^(relative|absolute|exact)$")]
    amount: Decimal = Decimal(0)
    unit: Unit | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind == "absolute" and self.unit is None:
            raise ValueError("an absolute tolerance needs a unit; 'within 2' is not a rule")
        if self.kind == "relative" and self.unit is not None:
            raise ValueError("a relative tolerance is a fraction and cannot carry a unit")
        if self.kind == "exact" and self.amount != 0:
            raise ValueError("an exact tolerance is zero by definition")
        return self


class Side(Strict):
    """One end of a reconciliation rule: a document type and a field on it."""

    document: str
    field: str


class ReconciliationRule(Strict):
    """Two fields on two document types that must agree.

    `severity` decides what happens when they do not: a `blocking` disagreement stops
    publication and goes to a human; an `advisory` one is recorded and reported. Neither is
    ever smoothed — claim 4 is that the disagreement is surfaced, and an advisory finding that
    nobody counts would be smoothing with extra steps, so both are counted on the scoreboard.
    """

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    description: Annotated[str, Field(min_length=15)]
    left: Side
    right: Side
    tolerance: ToleranceSpec
    severity: Annotated[str, Field(pattern=r"^(blocking|advisory)$")] = "blocking"


class ReconciliationContract(Strict):
    """The whole set of agreement rules across document types."""

    version: Annotated[int, Field(ge=1)]
    rules: Annotated[tuple[ReconciliationRule, ...], Field(min_length=1)]


class MatchRule(Strict):
    """One way two party names may be recognised as the same party.

    `weight` contributes to a match score; `rules` are the normalisations applied before
    comparison. A rule with no explanation cannot load, because claim 6 requires a merge to
    carry a reason a human can read and disagree with — an unexplained merge is one nobody can
    audit, and every merge here has to be reversible *and* reviewable.
    """

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    explanation: Annotated[str, Field(min_length=15)]
    rules: tuple[Rule, ...]
    weight: Annotated[Decimal, Field(gt=0, le=1)]


class EntityContract(Strict):
    """The party model, its matching rules, and the score a merge needs.

    `merge_threshold` is not derived the way a confidence threshold is: there is no labelled
    set of "these two names are the same company" large enough to derive one honestly, and
    pretending otherwise would be the exact fabrication ADR-0002 refuses. So it is **chosen,
    declared as chosen**, and every merge above it is still reversible with lineage intact —
    which is why claim 6 is about reversibility rather than about accuracy.
    """

    version: Annotated[int, Field(ge=1)]
    merge_threshold: Annotated[Decimal, Field(gt=0, le=1)]
    merge_threshold_is_derived: bool = False
    rules: Annotated[tuple[MatchRule, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _no_false_claim_of_derivation(self) -> Self:
        if self.merge_threshold_is_derived:
            raise ValueError(
                "nothing in this repository derives a merge threshold. There is no labelled "
                "set of same-party judgements large enough to do it honestly, and claiming "
                "otherwise would be the fabrication ADR-0002 exists to refuse. The threshold "
                "is chosen; claim 6 is about the merge being reversible, not about it being "
                "right"
            )
        return self


class ReviewCapacity(Strict):
    """The declared finite resource claim 5 is about (ADR-0001).

    Every figure here is a **declared scenario parameter**, not a measurement. Nothing in this
    repository has observed a reviewer, and the projection built from these numbers is a model
    that says so wherever it appears.
    """

    version: Annotated[int, Field(ge=1)]
    reviewers: Annotated[int, Field(ge=1)]
    productive_hours_per_day: Annotated[Decimal, Field(gt=0, le=24)]
    seconds_per_decision: Annotated[Decimal, Field(gt=0)]
    peak_multiplier: Annotated[Decimal, Field(ge=1)]
    documents_per_day: Annotated[int, Field(ge=1)]

    #: A decision faster than this is recorded as unexamined (ADR-0001).
    minimum_seconds_on_task: Annotated[Decimal, Field(gt=0)]
    #: Fraction of decided items re-queued to a second reviewer.
    sampled_rereview_rate: Annotated[Decimal, Field(gt=0, lt=1)]
    #: An agreement rate at or above this, over a full window, is reported as rubber-stamping.
    rubber_stamp_agreement_rate: Annotated[Decimal, Field(gt=0, le=1)]

    @property
    def decisions_per_day(self) -> Decimal:
        return (
            Decimal(self.reviewers)
            * self.productive_hours_per_day
            * Decimal(3600)
            / self.seconds_per_decision
        )

    @property
    def decisions_per_peak_day(self) -> Decimal:
        """Capacity does not rise on a peak day. The *volume* does.

        Written as a property returning the same number as `decisions_per_day` would be a bug
        waiting to be written; this exists so the peak calculation is visibly about volume.
        """
        return self.decisions_per_day


class TierEligibility(Strict):
    """Which cascade tiers may read a page in a given language (ADR-0004).

    The rule that stops a Greek page being sent to a service that does not read Greek. That
    call does not fail loudly — it returns a confident-looking result over a language the model
    never saw, and the score then enters a threshold derived on the assumption that it means
    something. Failing to route is a bug; routing to an ineligible engine is a fabricated
    result.
    """

    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]
    eligible_tiers: Annotated[tuple[int, ...], Field(min_length=1)]


class CascadeContract(Strict):
    """Tiers, and who may read what."""

    version: Annotated[int, Field(ge=1)]
    tiers: Annotated[dict[int, str], Field(min_length=1)]
    languages: Annotated[tuple[TierEligibility, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _eligibility_names_real_tiers(self) -> Self:
        for entry in self.languages:
            unknown = set(entry.eligible_tiers) - set(self.tiers)
            if unknown:
                raise ValueError(
                    f"language {entry.language!r} is eligible for tiers {sorted(unknown)}, "
                    f"which do not exist"
                )
            if 0 not in entry.eligible_tiers:
                raise ValueError(
                    f"language {entry.language!r} excludes tier 0. Tier 0 is the only reader "
                    f"that runs in this repository, so a language without it has no reader at "
                    f"all and every page in it would abstain silently"
                )
        return self

    def eligible(self, language: str) -> tuple[int, ...]:
        for entry in self.languages:
            if entry.language == language:
                return entry.eligible_tiers
        # An undeclared language gets tier 0 and nothing else. Conservative on purpose: the
        # alternative is a page in an unknown language being sent to an engine that may not
        # read it, which is the failure this contract exists to prevent.
        return (0,)
