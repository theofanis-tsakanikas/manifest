"""Threshold derivation, and the arithmetic underneath it.

The bound is checked **against its own definition rather than against this implementation**.
Clopper–Pearson's upper limit `U` for `k` errors in `n` trials is defined by
`P(X <= k | n, U) = 1 - confidence`, so the test evaluates that binomial sum directly with
`math.comb` and asserts it comes out at 0.05. Two genuinely different computations — a
continued-fraction beta quantile on one side, a factorial sum on the other — sharing only the
definition.

That matters here more than anywhere else in the repository. The first version of the
continued fraction applied its first term twice. The result stayed monotone, the bisection
still converged, and `k=1, n=20` came back as 0.130 against a true 0.216 — a bound a third of
its correct size, wrong in the direction that publishes more, and invisible to every test that
only compared the function with itself.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from manifest.core.calibration import (
    CONFIDENCE,
    MINIMUM_BIN,
    Observation,
    Outcome,
    calibrate,
    derive,
    upper_bound,
)


def _binomial_tail(k: int, n: int, p: float) -> float:
    """`P(X <= k)` for `X ~ Binomial(n, p)`, by definition.

    The independent path. No beta function, no continued fraction, no bisection — factorials
    and arithmetic, which is slow and obviously correct.
    """
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


@pytest.mark.parametrize(
    ("wrong", "published"),
    [(0, 10), (1, 20), (2, 100), (5, 50), (3, 30), (0, 1000), (17, 400), (1, 7)],
)
def test_the_bound_satisfies_its_own_definition(wrong: int, published: int) -> None:
    bound = upper_bound(wrong, published)
    assert _binomial_tail(wrong, published, bound) == pytest.approx(1 - CONFIDENCE, abs=1e-6)


def test_the_zero_error_case_matches_its_closed_form() -> None:
    """`1 - α^(1/n)`. Exact, and the case that decides most thresholds in a good corpus."""
    for n in (5, 10, 100, 1000):
        assert upper_bound(0, n) == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-9)


def test_every_error_gives_a_bound_of_one() -> None:
    assert upper_bound(7, 7) == 1.0
    assert upper_bound(0, 0) == 1.0


def test_the_bound_is_always_above_the_observed_rate() -> None:
    """The property that makes it conservative, and the reason it is used at all."""
    for wrong, published in ((1, 20), (2, 100), (5, 50), (40, 1000)):
        assert upper_bound(wrong, published) > wrong / published


def test_a_small_sample_produces_a_bound_far_above_its_point_estimate() -> None:
    """The whole argument for ADR-0002, in one assertion.

    One error in forty is an observed 2.5%. Publishing against a 3% budget on the strength of
    that is not engineering — the true rate could be four times it and this data would look the
    same.
    """
    assert upper_bound(1, 40) > 4 * (1 / 40)


# ── Derivation ───────────────────────────────────────────────────────────────


def _observations(pairs: list[tuple[float, Outcome]]) -> list[Observation]:
    return [Observation(confidence=c, outcome=o) for c, o in pairs]


def test_a_clean_high_confidence_population_gets_a_usable_threshold() -> None:
    data = _observations(
        [(0.99, Outcome.CORRECT)] * 900
        + [(0.55, Outcome.CORRECT)] * 60
        + [(0.55, Outcome.WRONG)] * 40
    )
    threshold = derive("container_number", data, Decimal("0.01"))
    assert not threshold.always_review
    assert threshold.value is not None
    assert threshold.value > 0.55
    assert threshold.upper_bound is not None
    assert threshold.upper_bound <= 0.01


def test_the_derivation_returns_the_lowest_threshold_that_fits() -> None:
    """Not the first one tried. Publishing more at the same budget is strictly better: every
    field kept below the threshold is a field spending review capacity ADR-0001 declared
    finite."""
    data = _observations([(0.99, Outcome.CORRECT)] * 500 + [(0.80, Outcome.CORRECT)] * 500)
    threshold = derive("port_of_loading", data, Decimal("0.01"))
    assert threshold.value is not None
    assert threshold.value <= 0.80
    assert threshold.coverage == pytest.approx(1.0)


def test_a_field_with_too_little_evidence_becomes_always_review() -> None:
    """Not a 0.999. A high number written because it sounds safe is doctrine rule 3's default
    wearing a decimal point, and it is worse than the default because it looks derived."""
    data = _observations([(0.99, Outcome.CORRECT)] * 30)
    threshold = derive("hs_code", data, Decimal("0.001"))
    assert threshold.always_review
    assert threshold.value is None
    assert "n=30" in threshold.reason


def test_the_always_review_reason_says_what_the_top_of_the_range_gave() -> None:
    """ "No threshold works" and "no threshold works, and even the top decile is at 4% against a
    0.2% budget" lead to different decisions about the field."""
    data = _observations([(0.999, Outcome.CORRECT)] * 96 + [(0.999, Outcome.WRONG)] * 4)
    threshold = derive("declared_value", data, Decimal("0.002"))
    assert threshold.always_review
    assert threshold.upper_bound is not None
    assert "0.995" in threshold.reason


def test_abstentions_are_not_counted_as_errors() -> None:
    """A value the reader never produced was never published.

    Counting a missing field as wrong would make a reader that abstains score worse than one
    that guesses — which is backwards, and it is the exact behaviour claim 1 exists to prevent.
    """
    published = [(0.99, Outcome.CORRECT)] * 400
    with_abstentions = _observations(published + [(0.10, Outcome.MISSING)] * 400)
    without = _observations(published)
    assert derive("f", with_abstentions, Decimal("0.01")).upper_bound == pytest.approx(
        derive("f", without, Decimal("0.01")).upper_bound
    )


def test_coverage_shows_what_a_threshold_costs_the_queue() -> None:
    """A threshold of 0.999 that satisfies every budget by publishing four percent of fields
    has not solved the problem; it has moved it into a queue with declared capacity."""
    data = _observations(
        [(0.999, Outcome.CORRECT)] * 40
        + [(0.60, Outcome.CORRECT)] * 900
        + [(0.60, Outcome.WRONG)] * 60
    )
    threshold = derive("country_of_origin", data, Decimal("0.005"))
    assert threshold.coverage < 0.10


def test_a_field_with_no_observations_is_always_review() -> None:
    threshold = derive("nothing", [], Decimal("0.01"))
    assert threshold.always_review
    assert threshold.observations == 0


# ── Calibration ──────────────────────────────────────────────────────────────


def test_a_perfectly_calibrated_score_has_almost_no_expected_error() -> None:
    """A score of 0.7 that is right 70% of the time, in every bucket."""
    data: list[Observation] = []
    for tenth in range(1, 10):
        confidence = tenth / 10 + 0.05
        correct = round(confidence * 200)
        data += _observations(
            [(confidence, Outcome.CORRECT)] * correct
            + [(confidence, Outcome.WRONG)] * (200 - correct)
        )
    calibration = calibrate(data)
    assert calibration.expected_calibration_error is not None
    assert calibration.expected_calibration_error < 0.01


def test_an_overconfident_score_is_reported_as_overconfident() -> None:
    """The direction that matters. Over-confidence publishes wrong values; under-confidence
    spends queue capacity, which is a different and cheaper problem."""
    data = _observations([(0.95, Outcome.CORRECT)] * 120 + [(0.95, Outcome.WRONG)] * 80)
    calibration = calibrate(data)
    worst = calibration.worst_overconfidence
    assert worst is not None
    assert worst.gap > 0.3


def test_ece_is_refused_where_the_bins_are_too_thin_to_mean_anything() -> None:
    """ECE over ten bins on a handful of samples is noise with a name. A number would have been
    available; refusing to print one is the point."""
    data = _observations([(0.1 * i + 0.05, Outcome.CORRECT) for i in range(9)])
    assert calibrate(data).expected_calibration_error is None


def test_every_bin_carries_its_count() -> None:
    """0.87 at n=4,000 and 0.87 at n=40 are different claims, and the number alone cannot tell
    them apart."""
    data = _observations([(0.95, Outcome.CORRECT)] * (MINIMUM_BIN + 5))
    calibration = calibrate(data)
    assert sum(entry.count for entry in calibration.bins) == MINIMUM_BIN + 5
    assert any(entry.count >= MINIMUM_BIN for entry in calibration.bins)
