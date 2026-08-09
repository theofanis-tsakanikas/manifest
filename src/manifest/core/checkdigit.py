"""ISO 6346 container numbers — the one field on these documents that can say it was misread.

**It is a falsifier, not ground truth.** A failing check digit proves the read is wrong. A
passing one proves nothing: the scheme is mod-11, so roughly one corruption in eleven passes,
and it is structurally blind to some transpositions. This module therefore has one caller
pattern — *refuse* a value whose digit fails — and no function that returns "this value is
correct", because there is no such fact to return.

Where it earns its place is the **public dataset**, which has no field-level labels at all.
There, counting provably-wrong container reads gives a **lower bound** on the error rate, and
`docs/SCENARIO.md` requires it to be reported as one. On the synthetic corpus it adds nothing
to ground truth, which already exists exactly.

The scheme, from ISO 6346: four letters (three owner code, one category identifier `U`, `J` or
`Z`), six digits, one check digit. Each character maps to a value — `A` is 10, and the
multiples of 11 (11, 22, 33) are **skipped**, so `K` is 21 and not 20. Each value is multiplied
by 2 raised to its position, the sum is taken modulo 11, and a remainder of 10 is written as 0.

That last rule is the one implementations get wrong. It makes the digit non-injective — both a
true remainder of 0 and a true remainder of 10 are written `0` — and an implementation that
rejects remainder 10 instead of folding it to 0 refuses a class of perfectly valid containers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Letter values with 11, 22 and 33 skipped, per ISO 6346. Built rather than written out, so
#: that the rule is visible instead of a table a reader has to trust.
_LETTER_VALUES: Final[dict[str, int]] = {}
_value = 10
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    while _value % 11 == 0:
        _value += 1
    _LETTER_VALUES[_letter] = _value
    _value += 1
del _value, _letter

#: Three owner letters, one category identifier, six digits, one check digit.
_PATTERN: Final = re.compile(r"^([A-Z]{3})([UJZ])(\d{6})(\d)$")

#: A container number is eleven characters: ten of body and one check digit.
_BODY_LENGTH: Final = 10
_TOTAL_LENGTH: Final = 11

#: The category identifier. `U` is a freight container, `J` detachable equipment, `Z` a trailer
#: or chassis. Anything else is not a container number, whatever else it might be.
_CATEGORIES: Final = frozenset("UJZ")


class ContainerNumberError(ValueError):
    """A string that is not shaped like a container number at all."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """What the arithmetic says, and what it does not.

    `provably_wrong` is the only field a caller may act on. `passed` deliberately does not
    exist as a positive assertion anywhere in this module's vocabulary — the name would invite
    exactly the inference the scheme cannot support.
    """

    value: str
    well_formed: bool
    provably_wrong: bool
    expected_digit: int | None
    reason: str

    @property
    def refuses(self) -> bool:
        """Whether this value must not be published without a human decision."""
        return not self.well_formed or self.provably_wrong


def check(value: str) -> CheckResult:
    """Evaluate a candidate container number.

    Whitespace and hyphens are removed first — `MSKU 123456 7` and `MSKU1234567` are the same
    number written two ways, and both appear on real paperwork. Case is folded up, because a
    reader that returns lower case has not read a different container.
    """
    cleaned = re.sub(r"[\s-]+", "", value).upper()
    match = _PATTERN.match(cleaned)
    if match is None:
        return CheckResult(
            value=value,
            well_formed=False,
            provably_wrong=False,
            expected_digit=None,
            reason=_why_not_well_formed(cleaned),
        )

    body = match.group(1) + match.group(2) + match.group(3)
    stated = int(match.group(4))
    expected = check_digit(body)
    wrong = stated != expected
    return CheckResult(
        value=value,
        well_formed=True,
        provably_wrong=wrong,
        expected_digit=expected,
        reason=(
            f"the check digit is {stated} and the arithmetic gives {expected}; this read is "
            f"provably wrong"
            if wrong
            else "the check digit is consistent — which proves the read is not obviously "
            "wrong, and nothing more: about one corruption in eleven passes this test"
        ),
    )


def check_digit(body: str) -> int:
    """The check digit for the first ten characters.

    `body` is four letters and six digits, upper case, already validated by the caller.
    """
    if len(body) != _BODY_LENGTH:
        raise ContainerNumberError(
            f"a container body is {_BODY_LENGTH} characters, not {len(body)}"
        )
    total = 0
    for position, character in enumerate(body):
        if character.isdigit():
            value = int(character)
        else:
            try:
                value = _LETTER_VALUES[character]
            except KeyError as exc:
                raise ContainerNumberError(f"{character!r} is not A-Z") from exc
        total += value * (2**position)
    remainder = total % 11
    # A remainder of 10 is written as 0. Both a true 0 and a true 10 therefore render as `0`,
    # which is the scheme's own ambiguity and not ours to fix — an implementation that treats
    # remainder 10 as invalid refuses a whole class of real containers.
    return remainder % 10


def _why_not_well_formed(cleaned: str) -> str:
    """A specific reason, because the review queue reads it.

    "Not a valid container number" makes a reviewer work out which of five things went wrong.
    Naming it turns the item into a glance.
    """
    if len(cleaned) != _TOTAL_LENGTH:
        return f"a container number is {_TOTAL_LENGTH} characters; this is {len(cleaned)}"
    if not cleaned[:4].isalpha():
        return f"the first four characters must be letters; got {cleaned[:4]!r}"
    if cleaned[3] not in _CATEGORIES:
        return (
            f"the category identifier is {cleaned[3]!r}; ISO 6346 allows only U, J or Z. A "
            f"reader confusing a category letter is the most likely cause"
        )
    if not cleaned[4:].isdigit():
        return f"the last seven characters must be digits; got {cleaned[4:]!r}"
    return "the value does not match the ISO 6346 pattern"


def is_well_formed(value: str) -> bool:
    """Shape only. Says nothing about the check digit, and is not a substitute for `check`."""
    return _PATTERN.match(re.sub(r"[\s-]+", "", value).upper()) is not None


def error_lower_bound(values: list[str] | tuple[str, ...]) -> tuple[int, int, float]:
    """`(provably wrong, checkable, rate)` over a set of container reads.

    The measurement for a corpus with no labels. The rate is a **lower bound** on the true
    error rate — every value counted is provably wrong, and an unknown number of the rest are
    wrong in ways mod-11 cannot see. Callers print it as a bound; a caller that prints it as
    "the error rate" has published a number that is too small by an unknown amount, which is
    the worst direction for this particular number to be wrong in.

    Values that are not well formed are counted as checkable and wrong: a read that is not
    shaped like a container number is a read that failed, and excluding it would flatter the
    figure by dropping the clearest failures.
    """
    results = [check(value) for value in values]
    checkable = len(results)
    wrong = sum(1 for result in results if result.refuses)
    return wrong, checkable, (wrong / checkable if checkable else 0.0)
