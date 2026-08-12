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

import random
from io import BytesIO

import pytest

from manifest.handlers.escalate import MODEL_IMAGE_LIMIT_BYTES, _within_the_model_limit

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


def test_an_oversized_page_fits_and_keeps_its_resolution() -> None:
    raw = _png(3000, 4200)
    assert len(raw) > MODEL_IMAGE_LIMIT_BYTES, "the fixture must actually be over the limit"

    fmt, out = _within_the_model_limit(raw)

    assert fmt == "jpeg"
    assert len(out) <= MODEL_IMAGE_LIMIT_BYTES
    assert Image.open(BytesIO(out)).size == (3000, 4200), (
        "quality is spent before pixels are. A shrunk page reads worse, and this is the only "
        "tier Greek and Dutch escalate to"
    )


def test_the_format_travels_with_the_bytes() -> None:
    """The pair exists so the content block cannot claim `png` over JPEG bytes.

    Bedrock validates the declared format against what it decodes, so getting this wrong is
    another `ValidationException` — one that would only appear on pages large enough to be
    re-encoded, which is the subset hardest to notice missing.
    """
    for raw in (_png(400, 560), _png(3000, 4200)):
        fmt, out = _within_the_model_limit(raw)
        assert Image.open(BytesIO(out)).format.lower() == fmt
