"""A page render is made to fit the model tier's image limit, without losing resolution first.

**The failure this closes was a `ValidationException`, not a bad reading.** Bedrock caps one
image in a `Converse` request at 5 MB; a full page at reading resolution arrived at 7,054,160
bytes and the call was rejected outright. The tier that had just been made reachable — the only
escalation Greek and Dutch have — could not be called at all.

The property under test is the *order* of the concessions. A page that is shrunk reads worse,
and this tier is the last one those two languages get, so quality is spent before pixels are.
PNG is lossless and enormous for a page of black text on white paper; JPEG at the same
resolution is a fraction of the size and loses colour fidelity the reader was never using.
"""

from __future__ import annotations

import base64
import random
from io import BytesIO

import pytest

from manifest.handlers.escalate import (
    MODEL_IMAGE_LIMIT_BYTES,
    MODEL_IMAGE_RAW_BUDGET,
    _within_the_model_limit,
)

Image = pytest.importorskip("PIL.Image", reason="Pillow ships with the deployed zip and dev")


def _png(width: int, height: int) -> bytes:
    """A PNG that does not compress away.

    Deterministic noise, seeded. The first version of this drew a regular pattern and PNG
    squeezed a 3000×4200 page down to 926 KB — the fixture for "over the limit" was comfortably
    under it, and the test failed on its own premise rather than on the code. A scanned page is
    noisy; a generated pattern is not, and the difference is a factor of six.
    """
    noise = random.Random(0).randbytes(width * height * 3)
    buffer = BytesIO()
    Image.frombytes("RGB", (width, height), noise).save(buffer, "PNG")
    return buffer.getvalue()


def test_a_page_under_the_limit_is_untouched() -> None:
    """Byte-identical, and still PNG. Re-encoding a page that fits would lose detail for nothing."""
    raw = _png(400, 560)
    assert len(raw) <= MODEL_IMAGE_LIMIT_BYTES
    assert _within_the_model_limit(raw) == ("png", raw)


def _scan(width: int, height: int) -> bytes:
    """A PNG that looks like a scanned page rather than like static.

    Mostly white, with black marks where text would be and mild sensor noise over all of it —
    which is what a scan is, and which JPEG compresses the way it compresses a real one. The
    noise-only fixture below is kept for the opposite case: something no quality setting can get
    under budget, where giving up pixels is the correct answer rather than a regression.
    """
    generator = random.Random(1)
    page = Image.new("L", (width, height), 255)
    pixels = page.load()
    for row in range(0, height, 6):
        for column in range(0, width, 2):
            if generator.random() < 0.45:
                pixels[column, row] = 20

    # **Sensor noise on every pixel**, which is what makes a scan's PNG enormous and its JPEG
    # small. A first version noised one pixel in forty; PNG squeezed the page to 1.9 MB, the
    # fixture was under the budget, and the test failed on its own premise rather than on the
    # code — the same way this file's *other* fixture once did, for the same reason, which is
    # why the story is written down twice.
    noise = Image.effect_noise((width, height), 14)
    buffer = BytesIO()
    Image.blend(page.convert("L"), noise, 0.35).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def test_an_oversized_page_fits_and_keeps_its_resolution() -> None:
    """Quality is spent before pixels are, on a page that looks like a document."""
    raw = _scan(3000, 4200)
    assert len(raw) > MODEL_IMAGE_RAW_BUDGET, "the fixture must actually be over the budget"

    fmt, out = _within_the_model_limit(raw)

    assert fmt == "jpeg"
    assert len(out) <= MODEL_IMAGE_RAW_BUDGET
    assert Image.open(BytesIO(out)).size == (3000, 4200), (
        "quality is spent before pixels are. A shrunk page reads worse, and this is the only "
        "tier Greek and Dutch escalate to"
    )


def test_what_the_request_actually_carries_is_under_the_ceiling() -> None:
    """**The property the API enforces, which nothing here was checking.**

    Bedrock's 5 MB ceiling applies to the image as the request carries it, and a `Converse`
    request carries it base64 encoded — four bytes out for every three in. Sizing against the
    ceiling produced a file that fit and a request that did not: a dense two-page invoice
    re-encoded to 3,987,501 bytes, comfortably under 5 MiB, and arrived as 5,316,668. The API
    refused it by exactly that number and the execution died, because the escalation step has no
    `Catch` — deliberately, so a broken escalation stops loudly rather than publishing on tier-0
    evidence.

    Every earlier escalation was a bill of lading whose JPEG came out near 600 KB, where the two
    budgets are indistinguishable.
    """
    _, out = _within_the_model_limit(_scan(3000, 4200))

    assert len(base64.b64encode(out)) <= MODEL_IMAGE_LIMIT_BYTES


def test_a_page_no_quality_setting_can_shrink_gives_up_pixels_instead() -> None:
    """Static compresses nowhere, so the last resort has to run — and still has to fit."""
    fmt, out = _within_the_model_limit(_png(3000, 4200))

    assert fmt == "jpeg"
    assert len(out) <= MODEL_IMAGE_RAW_BUDGET
    assert Image.open(BytesIO(out)).size != (3000, 4200)


def test_the_format_travels_with_the_bytes() -> None:
    """The pair exists so the content block cannot claim `png` over JPEG bytes.

    Bedrock validates the declared format against what it decodes, so getting this wrong is
    another `ValidationException` — one that would only appear on pages large enough to be
    re-encoded, which is the subset hardest to notice missing.
    """
    for raw in (_png(400, 560), _png(3000, 4200)):
        fmt, out = _within_the_model_limit(raw)
        assert Image.open(BytesIO(out)).format.lower() == fmt
