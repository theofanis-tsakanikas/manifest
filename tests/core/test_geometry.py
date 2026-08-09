"""Where a value is on a page — the arithmetic claim 2 rests on.

The cases worth having are the ones where a plausible implementation is wrong: rounding that
shaves a stroke off a digit, a box that survived construction with pixels in it, a hull over
an empty list, and an intersection that touches at an edge.
"""

from __future__ import annotations

import pytest

from manifest.core.geometry import Box, GeometryError, PageSize


def test_a_box_is_fractions_of_the_page() -> None:
    box = Box(left=0.1, top=0.2, width=0.3, height=0.4)
    assert box.right == pytest.approx(0.4)
    assert box.bottom == pytest.approx(0.6)
    assert box.area == pytest.approx(0.12)


def test_pixels_are_refused_because_they_leave_the_page() -> None:
    """The mistake an adapter makes once: reporting what the engine reported.

    Refusing it at construction is the difference between a bad box and a bad *archive* — a
    pixel box stored as provenance points somewhere plausible and wrong for as long as the
    record exists.
    """
    with pytest.raises(GeometryError, match="fractions of the page"):
        Box(left=120, top=340, width=80, height=20)


def test_a_box_with_no_area_cannot_locate_anything() -> None:
    with pytest.raises(GeometryError, match="no area"):
        Box(left=0.5, top=0.5, width=0.0, height=0.1)


def test_a_float_round_trip_just_outside_the_page_is_clamped_not_refused() -> None:
    """`1.0000000000000002` is a division, not a defect."""
    box = Box(left=0.0, top=0.0, width=1.0 + 1e-12, height=1.0)
    assert box.right <= 1.0


def test_a_coordinate_far_outside_the_page_is_refused_rather_than_clamped() -> None:
    """Clamping is for a float round-trip. It is not for a wrong number.

    Found by `gate-proof`, which is the point of it: the mutation that deleted the guard and
    left only the clamp was accepted by every test written before this one. A `left` of -5 is
    an adapter with a sign error or an unsubtracted offset, and clamping it to 0 stores a
    provenance record pointing at the top-left corner of the page — plausible, checkable, and
    wrong in the direction nobody investigates. The band that may be clamped is one part in a
    million; everything outside it raises.
    """
    with pytest.raises(GeometryError, match="outside the page"):
        Box(left=-5.0, top=0.1, width=0.01, height=0.01)


def test_nan_is_refused() -> None:
    with pytest.raises(GeometryError, match="not a number"):
        Box(left=float("nan"), top=0.1, width=0.2, height=0.2)


def test_from_pixels_divides_by_the_page_it_was_measured_on() -> None:
    page = PageSize(width=2000, height=3000)
    box = Box.from_pixels(left=200, top=300, width=400, height=150, page=page)
    assert box.left == pytest.approx(0.1)
    assert box.top == pytest.approx(0.1)
    assert box.width == pytest.approx(0.2)
    assert box.height == pytest.approx(0.05)


def test_the_hull_of_several_words_is_one_rectangle() -> None:
    words = [
        Box(left=0.10, top=0.20, width=0.05, height=0.02),
        Box(left=0.16, top=0.205, width=0.04, height=0.02),
        Box(left=0.21, top=0.199, width=0.03, height=0.021),
    ]
    hull = Box.hull(words)
    assert all(hull.contains(word) for word in words)
    assert hull.left == pytest.approx(0.10)
    assert hull.right == pytest.approx(0.24)


def test_the_hull_of_nothing_is_not_a_location() -> None:
    with pytest.raises(GeometryError, match="not a location"):
        Box.hull([])


def test_boxes_that_only_touch_do_not_intersect() -> None:
    left = Box(left=0.1, top=0.1, width=0.2, height=0.2)
    right = Box(left=0.3, top=0.1, width=0.2, height=0.2)
    assert left.intersection(right) is None
    assert left.iou(right) == 0.0


def test_iou_tolerates_the_pixel_or_two_two_readers_disagree_by() -> None:
    """Equality would refuse every honest record; this is why the check is IoU."""
    recorded = Box(left=0.100, top=0.200, width=0.060, height=0.020)
    reread = Box(left=0.101, top=0.2005, width=0.059, height=0.0198)
    assert recorded.iou(reread) > 0.9


def test_a_crop_is_never_smaller_than_the_box_it_came_from() -> None:
    """Rounding out, never in.

    Rounding to nearest can shave a column of pixels off a digit, and the verifier would then
    be re-reading a damaged crop and reporting that the *record* was false. The rounding rule
    has to make the error that costs a second look, not the one that fabricates a failure.
    """
    page = PageSize(width=1000, height=1000)
    box = Box(left=0.1004, top=0.2006, width=0.0503, height=0.0207)
    rect = box.to_pixels(page)
    assert rect.left <= box.left * page.width
    assert rect.top <= box.top * page.height
    assert rect.right >= box.right * page.width
    assert rect.bottom >= box.bottom * page.height


def test_a_crop_is_at_least_one_pixel_and_stays_on_the_raster() -> None:
    page = PageSize(width=100, height=100)
    sliver = Box(left=0.999, top=0.999, width=0.0005, height=0.0005)
    rect = sliver.to_pixels(page)
    assert rect.width >= 1
    assert rect.height >= 1
    assert rect.right <= page.width
    assert rect.bottom <= page.height


def test_padding_grows_the_box_and_stops_at_the_page_edge() -> None:
    box = Box(left=0.01, top=0.5, width=0.1, height=0.1)
    padded = box.padded(0.02)
    assert padded.contains(box)
    assert padded.left == 0.0
    assert padded.right == pytest.approx(0.13)


def test_a_negative_margin_is_refused_rather_than_shrinking_the_crop() -> None:
    with pytest.raises(GeometryError, match="negative margin"):
        Box(left=0.1, top=0.1, width=0.1, height=0.1).padded(-0.01)


def test_a_page_with_no_pixels_is_refused() -> None:
    with pytest.raises(GeometryError, match="no pixels"):
        PageSize(width=0, height=100)
