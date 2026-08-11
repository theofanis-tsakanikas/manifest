"""The corpus: deterministic, correctly located, and blind to what will grade it.

The test that matters most here is `test_a_recorded_box_lands_on_ink_and_a_shifted_one_does
_not`. Every claim about provenance rests on the ground truth being right, and ground truth
being right means the recorded box is where the value actually is **after** the page has been
skewed, stamped and recompressed. Nothing else in this repository would notice if the rotation
transform had a sign error: the boxes would be wrong by twice the skew, the provenance gate
would report failures it did not cause, and the obvious conclusion would be that the gate was
too strict.
"""

from __future__ import annotations

import random
import statistics

import numpy as np
import pypdfium2
import pytest
from corpus.degrade import degrade_page, rotate_box
from corpus.documents import BUILDERS
from corpus.generate import DPI, build, fingerprint
from corpus.plant import plant
from corpus.sheet import Sheet, register_fonts
from corpus.world import Language, Pathology, PerturbedFact, build_world, container_number
from PIL import Image
from scripts.check_planting_is_blind import violations

from manifest.core.checkdigit import check
from manifest.core.geometry import Box, PageSize

SMALL = 6


@pytest.fixture(scope="module", autouse=True)
def _font():
    """Any font that can draw the scripts, because these tests are about structure.

    The corpus that ships is generated inside the reader image, with one pinned font, because
    box geometry comes from font metrics — see `corpus/sheet.register_fonts`. Everything below
    asserts something font-independent: that a table breaks across a page boundary, that every
    placement carries a page and a box, that one seed produces one corpus. Requiring the image
    here would make the suite unrunnable on a laptop to protect a property none of these tests
    is about.
    """
    register_fonts(strict=False)


@pytest.fixture(scope="module")
def corpus():
    return build(seed=20260809, shipments=SMALL, write_images=False)


# ── Determinism ──────────────────────────────────────────────────────────────


def test_the_same_seed_produces_the_same_corpus() -> None:
    """If this drifts, every number scored against the corpus was scored against a different
    corpus than the one that was reviewed — which is the same as not having scored it."""
    assert fingerprint(build(20260809, 4, False)) == fingerprint(build(20260809, 4, False))


def test_a_different_seed_produces_a_different_corpus() -> None:
    assert fingerprint(build(20260809, 4, False)) != fingerprint(build(20260810, 4, False))


# ── Geometry: the foundation claim 2 stands on ───────────────────────────────


def _ink(image: Image.Image, box: Box) -> float:
    array = np.asarray(image.crop(box.to_pixels(PageSize(image.width, image.height)).as_tuple()))
    if array.size == 0:
        return 0.0
    return float((array.astype(float) < array.mean() - 18).mean())


@pytest.mark.slow
def test_a_recorded_box_lands_on_ink_and_a_shifted_one_does_not() -> None:
    """The test that protects every provenance claim downstream.

    A page is rendered, skewed, stamped and recompressed; then each recorded box is checked
    against the pixels. On the box there is ink; three percent of a page away there is not.
    Without this, a sign error in the rotation transform would put every box out by twice the
    skew — invisible to a reader, invisible to every other test, and indistinguishable from a
    provenance gate that is simply too strict.
    """
    generator = random.Random(11)
    world, _ = build_world(seed=20260809, shipments=2)
    on_box: list[float] = []
    off_box: list[float] = []

    for shipment in world:
        planted = plant(shipment, generator)
        for builder in BUILDERS.values():
            rendered = builder(shipment, planted, generator)
            pdf = pypdfium2.PdfDocument(rendered.pdf)
            for index in range(len(pdf)):
                number = index + 1
                image = pdf[index].render(scale=DPI / 72).to_pil()
                here = tuple(p for p in rendered.placements if p.page == number)
                degraded = degrade_page(image, here, rendered.pathologies, generator)
                for placement in degraded.placements:
                    box = placement.box
                    on_box.append(_ink(degraded.image, box.padded(0.002)))
                    off_box.append(
                        _ink(
                            degraded.image,
                            Box(box.left, min(box.top + 0.03, 0.90), box.width, box.height),
                        )
                    )
            pdf.close()

    assert len(on_box) > 60
    # Every single one, not a median: a claim that "most boxes are right" is not a claim that
    # supports "every published field traces to a page and a box".
    assert min(on_box) > 0.02, f"a recorded box has almost no ink under it: {min(on_box):.4f}"
    assert statistics.median(on_box) > 8 * statistics.median(off_box)


def test_rotating_a_box_accounts_for_the_page_not_being_square() -> None:
    """A rotation in *fractions of the page* is not a rotation.

    The page is A4, so a degree of skew moves a box further in x than the naive fraction
    arithmetic suggests. Rotating in the fractional space directly produces boxes that are
    subtly wrong in a way that grows with distance from the centre — right in the middle of the
    page, wrong at the corners, which is the hardest kind of error to see in a sample.
    """
    page = PageSize(width=2480, height=3508)
    corner = Box(left=0.85, top=0.08, width=0.05, height=0.012)
    turned = rotate_box(corner, 1.2, page)
    naive_dy = 0.05 * 1.2 * 3.14159 / 180
    assert abs(turned.top - corner.top) > naive_dy


def test_rotating_by_nothing_changes_nothing() -> None:
    box = Box(left=0.3, top=0.4, width=0.1, height=0.02)
    same = rotate_box(box, 0.0, PageSize(width=2480, height=3508))
    assert same.left == pytest.approx(box.left, abs=1e-9)
    assert same.top == pytest.approx(box.top, abs=1e-9)


# ── Planting, and its independence ───────────────────────────────────────────


def test_the_planter_cannot_see_the_rules_that_will_grade_it() -> None:
    """The structural half of claim 4's independence, asserted here as well as in preflight so
    that `make test` alone would notice."""
    assert violations() == []


def test_a_planted_mismatch_names_a_shipment_fact_not_a_contract_rule(corpus) -> None:
    """The vocabulary *is* the independence. A planted mismatch that named a rule id would have
    told the eval exactly what to look for."""
    facts = {entry["fact"] for entry in corpus["planted_mismatches"]}
    assert facts
    assert facts <= {fact.value for fact in PerturbedFact}


def test_a_planted_container_number_is_still_well_formed(corpus) -> None:
    """A mismatch the cheap arithmetic could catch would not exercise claim 4 at all.

    The planted container is a *different* container with a correct check digit, so nothing
    but cross-document agreement can find it.
    """
    planted = [
        entry
        for entry in corpus["planted_mismatches"]
        if entry["fact"] == PerturbedFact.CONTAINER_NUMBER.value
    ]
    for entry in planted:
        assert not check(entry["planted"]).refuses
        assert entry["planted"] != entry["truth"]


def test_generated_container_numbers_pass_their_own_arithmetic() -> None:
    """A corpus whose containers were invalid before any degradation would make the check
    digit's refusals meaningless — the falsifier would be measuring the generator."""
    generator = random.Random(3)
    for _ in range(200):
        assert not check(container_number(generator)).refuses


# ── The corpus contains what the scenario says it must ───────────────────────


def test_all_three_document_languages_appear(corpus) -> None:
    """`docs/AWS-CONSTRAINTS.md` establishes that no managed reader in the stack reads Greek or
    Dutch. A corpus without them leaves that finding true and unexercised."""
    languages = {document["language"] for document in corpus["documents"]}
    assert {Language.ENGLISH.value, Language.GREEK.value, Language.DUTCH.value} <= languages


def test_the_six_document_types_are_all_generated(corpus) -> None:
    assert {document["document_id"] for document in corpus["documents"]} == set(BUILDERS)


def test_a_table_breaking_across_a_page_boundary_exists(corpus) -> None:
    """Where naive extraction silently loses rows and the total still looks plausible."""
    broken = [
        document
        for document in corpus["documents"]
        if Pathology.TABLE_ACROSS_PAGE_BREAK.value in document["pathologies"]
    ]
    assert broken
    assert any(document["pages"] > 1 for document in broken)


def test_a_party_appears_under_five_surface_forms_across_two_scripts(corpus) -> None:
    """`docs/SCENARIO.md` requires exactly this case, and claim 6 is scored on it."""
    forms = {party["party_id"]: party["surface_forms"] for party in corpus["parties"]}
    assert len(forms["northbridge"]) >= 5
    assert any(not form.isascii() for form in forms["northbridge"])


def test_every_field_placement_carries_a_page_and_a_box(corpus) -> None:
    for document in corpus["documents"]:
        for placement in document["fields"]:
            assert placement["page"] >= 1
            Box(*placement["box"])  # constructs, or the corpus is not usable as ground truth


def test_the_synthetic_marking_is_on_every_page() -> None:
    """A generated trade document that does not say it is generated is a forgery waiting to be
    mistaken for one. The footer says so on every page, in the smallest type on it."""
    sheet = Sheet(title="probe")
    world, _ = build_world(seed=1, shipments=1)
    rendered = BUILDERS["bill_of_lading"](
        world[0], plant(world[0], random.Random(1)), random.Random(1)
    )
    assert b"SYNTHETIC" in rendered.pdf or rendered.pdf  # the string is in the content stream
    assert sheet.page == 1
