"""Quantities with units, and whether two of them agree.

Claim 4 says a gross weight on a bill of lading must agree with the gross weight on a packing
list. `docs/SCENARIO.md` puts kilograms and pounds on the same page on purpose, so "agree" is
never string equality and cannot be a float comparison either.

Three decisions, each of which is a way the claim goes soft if it is left to the caller.

**Exact arithmetic, in `Decimal`, from the string the reader emitted.** `1250.5` is not
representable in binary floating point, and a tolerance comparison that starts by introducing
an error of its own has already lost the argument about small differences. Parsing from the
digits rather than through a float is what keeps `12,50` and `12.50` from becoming different
numbers.

**Conversion factors are exact and cited.** The international pound is exactly 0.45359237 kg
by definition, not approximately. A factor written to five places is a systematic error
introduced by the system doing the checking, and on a 25-tonne container it is kilograms.

**A tolerance is declared per comparison, in a contract, with its kind.** Relative for weights
and values, absolute for counts. There is no default: a comparison whose tolerance nobody
declared is a comparison whose author did not decide what "agree" means, and defaulting to
zero would flag every honest rounding while defaulting to anything else would be a number
chosen by whoever wrote the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Self

#: A relative tolerance at or above this is refused. Not a principled bound — the principled
#: one is 1.0, where a "fraction" starts accepting everything — but a *useful* one: no
#: reconciliation rule in trade documentation tolerates a half. What it actually catches is the
#: units error, somebody writing 0.5 for half a percent, and that error is invisible after the
#: fact because a tolerance a hundred times too wide never fails a test. It only stops finding
#: things, which is the one failure mode claim 4 cannot detect in itself.
_IMPLAUSIBLE_RELATIVE: Final = Decimal("0.5")


#: A single separator followed by exactly this many digits is the ambiguous case: `1,250`
#: is one thousand two hundred and fifty in one convention and one and a quarter in the
#: other. Three, because that is the grouping size both conventions use.
_GROUPING_SIZE: Final = 3


class QuantityError(ValueError):
    """A quantity that cannot be compared with another."""


class Dimension(StrEnum):
    MASS = "mass"
    LENGTH = "length"
    VOLUME = "volume"
    COUNT = "count"
    MONEY = "money"


class Unit(StrEnum):
    KILOGRAM = "kg"
    POUND = "lb"
    TONNE = "t"
    METRE = "m"
    CENTIMETRE = "cm"
    INCH = "in"
    FOOT = "ft"
    CUBIC_METRE = "m3"
    CUBIC_FOOT = "ft3"
    PIECE = "pcs"
    CARTON = "ctn"
    PALLET = "plt"


#: Unit → (dimension, factor to the dimension's base unit). Every factor is exact.
#:
#: The pound: 1 lb = 0.45359237 kg *exactly*, by the international yard and pound agreement of
#: 1959. The inch: 1 in = 0.0254 m exactly, by the same agreement. Both are definitions, so
#: writing them to full precision costs nothing and rounding them costs kilograms per container.
_UNITS: Final[dict[Unit, tuple[Dimension, Decimal]]] = {
    Unit.KILOGRAM: (Dimension.MASS, Decimal(1)),
    Unit.POUND: (Dimension.MASS, Decimal("0.45359237")),
    Unit.TONNE: (Dimension.MASS, Decimal(1000)),
    Unit.METRE: (Dimension.LENGTH, Decimal(1)),
    Unit.CENTIMETRE: (Dimension.LENGTH, Decimal("0.01")),
    Unit.INCH: (Dimension.LENGTH, Decimal("0.0254")),
    Unit.FOOT: (Dimension.LENGTH, Decimal("0.3048")),
    Unit.CUBIC_METRE: (Dimension.VOLUME, Decimal(1)),
    # 1 ft = 0.3048 m exactly, so 1 ft³ = 0.3048³ m³ exactly.
    Unit.CUBIC_FOOT: (Dimension.VOLUME, Decimal("0.3048") ** 3),
    Unit.PIECE: (Dimension.COUNT, Decimal(1)),
    Unit.CARTON: (Dimension.COUNT, Decimal(1)),
    Unit.PALLET: (Dimension.COUNT, Decimal(1)),
}


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number with a unit, held exactly."""

    amount: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise QuantityError(
                f"a quantity holds a Decimal, not {type(self.amount).__name__}. A float here "
                f"introduces an error before the tolerance comparison has started"
            )
        if self.amount.is_nan() or self.amount.is_infinite():
            raise QuantityError(f"{self.amount} is not a quantity")

    @property
    def dimension(self) -> Dimension:
        return _UNITS[self.unit][0]

    def to(self, unit: Unit) -> Self:
        """The same quantity in another unit of the same dimension."""
        if _UNITS[unit][0] is not self.dimension:
            raise QuantityError(
                f"{self.unit.value} is {self.dimension.value} and {unit.value} is "
                f"{_UNITS[unit][0].value}; there is no conversion and there should not be one"
            )
        base = self.amount * _UNITS[self.unit][1]
        return type(self)(amount=base / _UNITS[unit][1], unit=unit)

    def __str__(self) -> str:
        return f"{self.amount.normalize()} {self.unit.value}"


class ToleranceKind(StrEnum):
    #: A fraction of the larger of the two values. For weights, volumes and money.
    RELATIVE = "relative"
    #: An amount in the comparison's unit. For counts, where 0.5% of 3 cartons is meaningless.
    ABSOLUTE = "absolute"


@dataclass(frozen=True, slots=True)
class Tolerance:
    """How far apart two quantities may be and still agree.

    No default anywhere in this module. A contract declares it, and a reconciliation rule with
    no tolerance must fail to load — the same rule and the same reason as a field with no error
    budget.
    """

    kind: ToleranceKind
    amount: Decimal
    #: Required for an absolute tolerance and forbidden for a relative one. "Within 2" is not a
    #: tolerance until it says two of what.
    unit: Unit | None = None

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise QuantityError(f"a negative tolerance accepts nothing: {self.amount}")
        if self.kind is ToleranceKind.ABSOLUTE and self.unit is None:
            raise QuantityError("an absolute tolerance needs a unit; 'within 2' is not a rule")
        if self.kind is ToleranceKind.RELATIVE and self.unit is not None:
            raise QuantityError(
                f"a relative tolerance is a fraction and cannot carry {self.unit.value}"
            )
        if self.kind is ToleranceKind.RELATIVE and self.amount >= _IMPLAUSIBLE_RELATIVE:
            raise QuantityError(
                f"a relative tolerance of {self.amount} accepts a value {self.amount:.0%} away "
                f"from the other, which is not a reconciliation. It is expressed as a "
                f"**fraction**: half a percent is 0.005, not 0.5. That mistake is the easiest "
                f"one in this file to make and the least visible once made, because a "
                f"tolerance that is a hundred times too wide never fails a test — it only "
                f"stops finding things"
            )


@dataclass(frozen=True, slots=True)
class Agreement:
    """Whether two quantities agree, by how much they differ, and in what terms.

    `difference` and `allowed` are both carried so the review queue can show a reviewer *how
    far out* a mismatch is. "These disagree" and "these disagree by 0.6% against a 0.5%
    tolerance" produce different decisions, and the second one is a glance.
    """

    left: Quantity
    right: Quantity
    tolerance: Tolerance
    agree: bool
    difference: Decimal
    allowed: Decimal
    explanation: str


def agrees(left: Quantity, right: Quantity, tolerance: Tolerance) -> Agreement:
    """Compare two quantities under a declared tolerance.

    Comparison happens in the **left** quantity's unit, which is the unit the reader on the
    primary document used. That choice is arbitrary and therefore stated: what matters is that
    it is fixed, because comparing in whichever unit is larger would make agreement depend on
    the order of the arguments.
    """
    if left.dimension is not right.dimension:
        raise QuantityError(
            f"{left} and {right} are {left.dimension.value} and {right.dimension.value}; "
            f"these do not disagree, they are not comparable, and reporting a mismatch would "
            f"send a reviewer to look for a discrepancy that does not exist"
        )

    converted = right.to(left.unit)
    difference = abs(left.amount - converted.amount)

    if tolerance.kind is ToleranceKind.RELATIVE:
        # Against the larger of the two. Against the left one, agreement would depend on
        # argument order for exactly the values near the boundary that a tolerance is for.
        reference = max(abs(left.amount), abs(converted.amount))
        allowed = reference * tolerance.amount
        terms = f"{tolerance.amount:%} of {reference.normalize()} {left.unit.value}"
    else:
        assert tolerance.unit is not None  # guaranteed by Tolerance.__post_init__
        allowed = Quantity(tolerance.amount, tolerance.unit).to(left.unit).amount
        terms = f"{tolerance.amount.normalize()} {tolerance.unit.value}"

    agree = difference <= allowed
    return Agreement(
        left=left,
        right=right,
        tolerance=tolerance,
        agree=agree,
        difference=difference,
        allowed=allowed,
        explanation=(
            f"differ by {difference.normalize()} {left.unit.value}, "
            f"{'within' if agree else 'outside'} a tolerance of {terms}"
            + ("" if left.unit is right.unit else f" (converted from {right})")
        ),
    )


def parse(raw: str, unit: Unit) -> Quantity:
    """A quantity from the string a reader emitted, without going through a float.

    Handles the two decimal conventions on the same document set — `1.250,50` is European and
    `1,250.50` is Anglo-American, and they are the same number. The rule is the **last**
    separator: whichever of `.` or `,` appears last is the decimal mark, and every earlier one
    is a grouping separator.

    That rule is unambiguous except for a single separator with exactly three digits after it,
    where `1,250` could be one thousand two hundred and fifty or one and a quarter. This
    refuses that case rather than guessing, because guessing is how a shipment's weight becomes
    a thousand times what it should be while looking entirely plausible on the page.
    """
    text = raw.strip().replace(" ", "").replace(" ", "")  # noqa: RUF001 — the second is a
    # non-breaking space, which is what a reader returns for the thin space typeset between
    # a number and its grouping on a European invoice. Dropping it is the point.
    if not text:
        raise QuantityError("an empty string is not a quantity; missing is missing")

    negative = text.startswith("-")
    text = text.lstrip("+-")

    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_at = max(last_dot, last_comma)
        text = text[:decimal_at].replace(".", "").replace(",", "") + "." + text[decimal_at + 1 :]
    elif last_dot >= 0 or last_comma >= 0:
        at = max(last_dot, last_comma)
        tail = text[at + 1 :]
        if len(tail) == _GROUPING_SIZE and text.count(text[at]) == 1:
            raise QuantityError(
                f"{raw!r} is ambiguous: a single separator with three digits after it is a "
                f"decimal mark in one convention and a thousands separator in the other, and "
                f"the two readings differ by a factor of a thousand. The field's contract must "
                f"declare which convention the document uses"
            )
        text = text[:at].replace(".", "").replace(",", "") + "." + tail

    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise QuantityError(f"{raw!r} is not a number") from exc
    return Quantity(amount=-amount if negative else amount, unit=unit)
