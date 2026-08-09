"""Deriving a confidence threshold from an error budget, and measuring how calibrated a score is.

Claim 1. ADR-0002. This is the module that separates the project from a demo, and it is the one
that would be easiest to write in a way that looks rigorous and is not.

**The threshold is the lowest score whose *upper confidence bound* on the error rate still fits
the budget.** Not the lowest score whose observed error rate fits it. Forty examples above 0.9
with one wrong is an observed rate of 2.5% and a 95% upper bound near 13%, and publishing
against a 3% budget on the strength of the 2.5% is not engineering. The bound is
Clopper–Pearson: exact, conservative, and correct at small `n` and at `k = 0`, which is where
the normal approximation is worst and where these decisions actually get made.

Conservative is the right direction to be wrong in. Being wrong here publishes wrong values.

**Where no threshold fits, the answer is `always-review`.** Not a high number. A field whose
evidence cannot support publishing it automatically is a field that goes to a human, declared as
such, with its `n` on the face of the report. A 0.999 written because 0.999 sounds safe is
doctrine rule 3's default wearing a decimal point, and it is worse than the default because it
looks derived.

**Calibration is reported, not enforced.** The threshold decides publication; the reliability
curve and ECE describe how far the score is from being a probability. A badly calibrated score
with a conservative threshold is safe and *wasteful*, and the curve is the only thing that shows
the waste. Every bin carries its count, and ECE is refused where the bins are too thin to mean
anything — ECE over ten bins on sixty samples is noise with a name.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

#: One-sided confidence for the bound. 95% is conventional; it is *declared* here rather than
#: passed in, because a caller who could lower it could lower it until a threshold appeared.
CONFIDENCE: Final = 0.95

#: A bin with fewer observations than this contributes to the reliability curve but not to ECE.
#: An ECE dominated by two bins of three samples is a number with a name and no content.
MINIMUM_BIN: Final = 20

#: The top of the confidence range, used only to describe *why* no threshold fits. Reporting
#: what the very top decile gave turns "no threshold works" into "no threshold works, and even
#: above this the bound is 4% against a 0.2% budget" — two sentences that lead to different
#: decisions about the field.
TOP_OF_RANGE: Final = 0.995

#: Convergence for the beta continued fraction. Double precision runs out around 1e-16; this is
#: two orders above it, which is where the iteration stops improving.
_FRACTION_TOLERANCE: Final = 1e-14


class Outcome(StrEnum):
    """What happened to one extracted value, against ground truth.

    `MISSING` is separate from `WRONG` on purpose. A field the reader did not find is an
    abstention, and counting it as an error would make the threshold derivation reward a reader
    that guesses — which is the precise behaviour claim 1 exists to prevent.
    """

    CORRECT = "correct"
    WRONG = "wrong"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class Observation:
    """One extracted value, its confidence, and whether it was right."""

    confidence: float
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class Bin:
    """One bucket of the reliability curve, with the count that makes it readable."""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float

    @property
    def gap(self) -> float:
        """How far this bucket's confidence is from its accuracy.

        Positive means over-confident, which is the direction that publishes wrong values.
        """
        return self.mean_confidence - self.accuracy


@dataclass(frozen=True, slots=True)
class Calibration:
    """How well a reader's confidence behaves as a probability, for one field.

    `expected_calibration_error` is `None` where no bin reached `MINIMUM_BIN`. A number would
    have been available; refusing to print one is the point.
    """

    bins: tuple[Bin, ...]
    expected_calibration_error: float | None
    observations: int

    @property
    def worst_overconfidence(self) -> Bin | None:
        eligible = [entry for entry in self.bins if entry.count >= MINIMUM_BIN]
        return max(eligible, key=lambda entry: entry.gap) if eligible else None


@dataclass(frozen=True, slots=True)
class Threshold:
    """A derived threshold, or the declared absence of one.

    `always_review` is not a failure of the derivation; it is one of its two answers. The report
    prints `n` beside every threshold because 0.87 at n=4,000 and 0.87 at n=40 are different
    claims and the number alone cannot tell them apart.
    """

    field: str
    value: float | None
    always_review: bool
    error_budget: Decimal
    published: int
    wrong: int
    upper_bound: float | None
    observations: int
    reason: str

    @property
    def coverage(self) -> float:
        """The fraction of observations this threshold would publish.

        The other half of the story. A threshold of 0.999 that satisfies every budget by
        publishing four percent of fields has not solved the problem, it has moved it into the
        review queue — where ADR-0001 has already declared the capacity finite.
        """
        return self.published / self.observations if self.observations else 0.0


def derive(
    field: str,
    observations: list[Observation] | tuple[Observation, ...],
    error_budget: Decimal,
    candidates: int = 200,
) -> Threshold:
    """The lowest threshold whose upper bound on the error rate fits the budget.

    Abstentions are excluded from the denominator: the budget is about *published and wrong*,
    and a value the reader never produced was never published. Including them would make a
    reader that abstains look worse than one that guesses, which is backwards.
    """
    scored = [entry for entry in observations if entry.outcome is not Outcome.MISSING]
    total = len(scored)
    if not scored:
        return Threshold(
            field=field,
            value=None,
            always_review=True,
            error_budget=error_budget,
            published=0,
            wrong=0,
            upper_bound=None,
            observations=0,
            reason="no observations; nothing supports publishing this field automatically",
        )

    budget = float(error_budget)
    # Sweep from the top down and stop at the first threshold that fits. Sweeping upward and
    # taking the first fit gives the same answer only when the bound is monotone in the
    # threshold, and it is not: adding a correct low-confidence observation lowers the observed
    # rate while widening the interval. Descending and remembering the last fit is what makes
    # the answer the *lowest* threshold that fits rather than the first one tried.
    best: Threshold | None = None
    for step in range(candidates + 1):
        threshold = 1.0 - step / candidates
        published = [entry for entry in scored if entry.confidence >= threshold]
        if not published:
            continue
        wrong = sum(1 for entry in published if entry.outcome is Outcome.WRONG)
        bound = upper_bound(wrong, len(published))
        if bound <= budget:
            best = Threshold(
                field=field,
                value=threshold,
                always_review=False,
                error_budget=error_budget,
                published=len(published),
                wrong=wrong,
                upper_bound=bound,
                observations=total,
                reason=(
                    f"at {threshold:.3f}, {wrong}/{len(published)} published values are wrong; "
                    f"the 95% upper bound on that rate is {bound:.4f}, within the budget of "
                    f"{budget:.4f}"
                ),
            )

    if best is not None:
        return best

    # Nothing fits. Report what the very top of the range would have given, because "no
    # threshold works" and "no threshold works, and even the top decile is at 4% against a 0.2%
    # budget" lead to different decisions about the field.
    top = [entry for entry in scored if entry.confidence >= TOP_OF_RANGE]
    wrong = sum(1 for entry in top if entry.outcome is Outcome.WRONG)
    bound = upper_bound(wrong, len(top)) if top else None
    return Threshold(
        field=field,
        value=None,
        always_review=True,
        error_budget=error_budget,
        published=0,
        wrong=0,
        upper_bound=bound,
        observations=total,
        reason=(
            f"no threshold fits a budget of {budget:.4f} at n={total}. "
            + (
                f"Even above {TOP_OF_RANGE}, {wrong}/{len(top)} are wrong with an upper bound of "
                f"{bound:.4f}. "
                if bound is not None
                else "Nothing scores above 0.995. "
            )
            + "The field is always-review: the evidence does not support publishing it "
            "automatically, and a high number written here would be a default wearing a "
            "decimal point"
        ),
    )


def calibrate(
    observations: list[Observation] | tuple[Observation, ...], bins: int = 10
) -> Calibration:
    """The reliability curve and, where the data supports one, an ECE."""
    scored = [entry for entry in observations if entry.outcome is not Outcome.MISSING]
    buckets: list[list[Observation]] = [[] for _ in range(bins)]
    for entry in scored:
        index = min(int(entry.confidence * bins), bins - 1)
        buckets[index].append(entry)

    curve = tuple(
        Bin(
            lower=index / bins,
            upper=(index + 1) / bins,
            count=len(bucket),
            mean_confidence=(sum(e.confidence for e in bucket) / len(bucket)) if bucket else 0.0,
            accuracy=(sum(1 for e in bucket if e.outcome is Outcome.CORRECT) / len(bucket))
            if bucket
            else 0.0,
        )
        for index, bucket in enumerate(buckets)
    )

    eligible = [entry for entry in curve if entry.count >= MINIMUM_BIN]
    weight = sum(entry.count for entry in eligible)
    ece = sum(entry.count * abs(entry.gap) for entry in eligible) / weight if weight else None
    return Calibration(bins=curve, expected_calibration_error=ece, observations=len(scored))


def upper_bound(wrong: int, published: int, confidence: float = CONFIDENCE) -> float:
    """The Clopper–Pearson one-sided upper bound on the error rate.

    `BetaInv(confidence; wrong + 1, published - wrong)`, with the two edges handled explicitly:
    at `wrong == published` the bound is 1, and at `wrong == 0` the closed form
    `1 - (1 - confidence)^(1/published)` is exact and avoids a root-find at the boundary.
    """
    if published <= 0:
        return 1.0
    if wrong >= published:
        return 1.0
    if wrong == 0:
        return 1.0 - (1.0 - confidence) ** (1.0 / published)
    return _beta_quantile(confidence, wrong + 1, published - wrong)


def _beta_quantile(probability: float, a: float, b: float) -> float:
    """`x` such that the regularised incomplete beta `I_x(a, b) == probability`.

    Bisection rather than Newton. It is a handful of microseconds either way at these sizes, it
    cannot diverge, and it needs no derivative — and the alternative is a dependency the core is
    not allowed to have. `scipy.stats.beta.ppf` is fifty lines of somebody else's code the
    purity gate would refuse, and this is thirty of ours with a test against published values.
    """
    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2
        if _regularised_incomplete_beta(middle, a, b) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _regularised_incomplete_beta(x: float, a: float, b: float) -> float:
    """`I_x(a, b)`, by the continued fraction, with the standard symmetry near x = 1.

    The continued fraction converges quickly for `x < (a + 1) / (a + b + 2)` and slowly beyond
    it, so the other side is evaluated through `I_x(a, b) = 1 - I_{1-x}(b, a)`.
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _continued_fraction(x, a, b) / a
    return 1.0 - front * _continued_fraction(1 - x, b, a) / b


def _continued_fraction(x: float, a: float, b: float, iterations: int = 300) -> float:
    """Lentz's algorithm for the beta continued fraction."""
    tiny = 1e-300
    c = 1.0
    # This initialisation *is* the first term of the fraction, `d_1`. The loop therefore starts
    # at 2, and starting it at 1 — which is the obvious reading of the recurrence — applies
    # `d_1` twice. The result stays monotone in `x`, so the bisection above still converges and
    # still returns a plausible number: `k=1, n=20` came back as 0.130 against a published
    # 0.216, which is wrong in the direction that publishes more. Nothing but a check against
    # published values would have found it.
    d = 1.0 - (a + b) * x / (a + 1)
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    result = d

    for index in range(2, iterations + 2):
        even = index // 2
        numerator = (
            even * (b - even) * x / ((a + 2 * even - 1) * (a + 2 * even))
            if index % 2 == 0
            else -(a + even) * (a + b + even) * x / ((a + 2 * even) * (a + 2 * even + 1))
        )
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        step = c * d
        result *= step
        if abs(step - 1.0) < _FRACTION_TOLERANCE:
            break
    return result
