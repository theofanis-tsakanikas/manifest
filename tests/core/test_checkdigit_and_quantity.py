"""ISO 6346 arithmetic, and quantities that have to agree across documents."""

from __future__ import annotations

from decimal import Decimal

import pytest

from manifest.core.checkdigit import check, check_digit, error_lower_bound, is_well_formed
from manifest.core.quantity import (
    Quantity,
    QuantityError,
    Tolerance,
    ToleranceKind,
    Unit,
    agrees,
    parse,
)

# ── ISO 6346 ─────────────────────────────────────────────────────────────────


def test_the_letter_values_skip_the_multiples_of_eleven() -> None:
    """`A` is 10 and `K` is 21, not 20. An implementation that numbers the alphabet
    consecutively passes its own tests and disagrees with every container in the world."""
    # A=10, B=12 (11 skipped), ..., K=21 (22 skipped after J=20)
    assert check_digit("AAAU000000") == check_digit("AAAU000000")
    assert check_digit("CSQU305438") == 3  # the ISO 6346 worked example


@pytest.mark.parametrize(
    "number",
    ["CSQU3054383", "MSKU 123456 5", "msku1234565", "TGHU-856051-8"],
    ids=["worked-example", "spaced", "lower-case", "hyphenated"],
)
def test_a_consistent_number_is_not_refused(number: str) -> None:
    result = check(number)
    assert result.well_formed
    assert not result.provably_wrong
    assert not result.refuses


def test_a_pass_is_reported_as_proving_almost_nothing() -> None:
    """The wording is the control. A result that reads 'valid' invites the inference mod-11
    cannot support, and that inference would turn a falsifier into a label."""
    assert "nothing more" in check("CSQU3054383").reason


def test_a_wrong_digit_is_provably_wrong() -> None:
    result = check("CSQU3054384")
    assert result.provably_wrong
    assert result.expected_digit == 3
    assert result.refuses


def test_a_remainder_of_ten_is_written_as_zero() -> None:
    """The rule implementations get wrong. Rejecting remainder 10 instead of folding it to 0
    refuses a whole class of real containers, and the refusal looks like a reader error."""
    bodies = [f"CSQU30543{d}" for d in range(10)]
    digits = [check_digit(body) for body in bodies]
    assert all(0 <= digit <= 9 for digit in digits)


@pytest.mark.parametrize(
    ("value", "phrase"),
    [
        ("CSQU305438", "11 characters"),
        ("CS1U3054383", "must be letters"),
        ("CSQX3054383", "category identifier"),
        ("CSQU30543A3", "must be digits"),
    ],
    ids=["too-short", "digit-in-owner", "bad-category", "letter-in-serial"],
)
def test_a_malformed_number_says_which_thing_is_wrong(value: str, phrase: str) -> None:
    """ "Not a valid container number" makes a reviewer work out which of five things went
    wrong. ADR-0001 counts those seconds."""
    result = check(value)
    assert not result.well_formed
    assert phrase in result.reason
    assert result.refuses


def test_the_error_bound_counts_malformed_reads_as_failures() -> None:
    """Excluding them would flatter the figure by dropping the clearest failures."""
    wrong, checkable, rate = error_lower_bound(["CSQU3054383", "CSQU3054384", "nonsense"])
    assert (wrong, checkable) == (2, 3)
    assert rate == pytest.approx(2 / 3)


def test_shape_alone_is_not_a_check() -> None:
    assert is_well_formed("CSQU3054384")
    assert check("CSQU3054384").provably_wrong


# ── Quantities ───────────────────────────────────────────────────────────────


def test_the_pound_is_exact() -> None:
    """0.45359237 exactly, by the 1959 agreement. A factor rounded to five places is a
    systematic error the checking system introduced, and on a 25-tonne container it is
    kilograms."""
    assert Quantity(Decimal("1"), Unit.POUND).to(Unit.KILOGRAM).amount == Decimal("0.45359237")


def test_a_float_amount_is_refused() -> None:
    with pytest.raises(QuantityError, match="introduces an error"):
        Quantity(1250.5, Unit.KILOGRAM)  # type: ignore[arg-type]


def test_converting_across_dimensions_is_refused() -> None:
    with pytest.raises(QuantityError, match="no conversion"):
        Quantity(Decimal("10"), Unit.KILOGRAM).to(Unit.METRE)


def test_kilograms_and_pounds_on_the_same_page_agree_within_tolerance() -> None:
    """The scenario's pathology. 27,000 kg against 59,525 lb is the same shipment."""
    result = agrees(
        Quantity(Decimal("27000"), Unit.KILOGRAM),
        Quantity(Decimal("59525"), Unit.POUND),
        Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.005")),
    )
    assert result.agree
    assert "converted from" in result.explanation


def test_a_relative_tolerance_does_not_depend_on_argument_order() -> None:
    """Against the larger of the two. Against the left one, agreement would depend on
    argument order for exactly the values near the boundary a tolerance exists for."""
    left = Quantity(Decimal("1000"), Unit.KILOGRAM)
    right = Quantity(Decimal("1006"), Unit.KILOGRAM)
    tolerance = Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.005"))
    assert agrees(left, right, tolerance).agree == agrees(right, left, tolerance).agree


def test_incomparable_dimensions_are_not_reported_as_a_mismatch() -> None:
    """Reporting one would send a reviewer to look for a discrepancy that does not exist —
    which spends queue capacity claim 5 has already declared finite."""
    with pytest.raises(QuantityError, match="not comparable"):
        agrees(
            Quantity(Decimal("10"), Unit.KILOGRAM),
            Quantity(Decimal("10"), Unit.PIECE),
            Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.01")),
        )


def test_counts_take_an_absolute_tolerance_and_weights_do_not() -> None:
    with pytest.raises(QuantityError, match="needs a unit"):
        Tolerance(kind=ToleranceKind.ABSOLUTE, amount=Decimal("2"))
    with pytest.raises(QuantityError, match="cannot carry"):
        Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.01"), unit=Unit.PIECE)


def test_a_relative_tolerance_written_as_a_percentage_is_refused() -> None:
    """`0.5` meaning "half a percent" accepts anything within 50%. The units of a tolerance
    are the easiest thing in this file to get wrong and the least visible."""
    with pytest.raises(QuantityError, match=r"half a percent is 0\.005"):
        Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.5"))
    Tolerance(kind=ToleranceKind.RELATIVE, amount=Decimal("0.005"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.250,50", "1250.50"),
        ("1,250.50", "1250.50"),
        ("27000", "27000"),
        ("27 000,5", "27000.5"),
        ("0,75", "0.75"),
        ("-12.5", "-12.5"),
    ],
    ids=["european", "anglo", "plain", "spaced", "comma-decimal", "negative"],
)
def test_both_decimal_conventions_parse_to_the_same_number(raw: str, expected: str) -> None:
    assert parse(raw, Unit.KILOGRAM).amount == Decimal(expected)


def test_a_single_separator_with_three_digits_is_refused_rather_than_guessed() -> None:
    """`1,250` is one thousand two hundred and fifty, or it is one and a quarter. The two
    readings differ by a factor of a thousand and both look entirely plausible on a bill of
    lading. Guessing here is how a shipment's weight becomes a thousand times what it should be."""
    with pytest.raises(QuantityError, match="factor of a thousand"):
        parse("1,250", Unit.KILOGRAM)
    with pytest.raises(QuantityError, match="factor of a thousand"):
        parse("1.250", Unit.KILOGRAM)


def test_an_empty_string_is_missing_rather_than_zero() -> None:
    """Doctrine rule 3. A default is a lie with a plausible shape, and zero kilograms is the
    most plausible-shaped lie on a packing list."""
    with pytest.raises(QuantityError, match="missing is missing"):
        parse("   ", Unit.KILOGRAM)
