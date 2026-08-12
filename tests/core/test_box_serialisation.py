"""One conversion from a box to the four numbers a record stores.

`escalate` wrote `list(found.box)`, which reads as obviously correct and raises `TypeError:
'Box' object is not iterable` — in the fourth state of a tier that had never been reached, on a
deployed estate, after a billed call had already been made. `publish` had the same conversion
written out by hand, correctly, four lines long.

Two hand conversions of one value is the drift. `Box.as_tuple` is the one both call now, and
these tests hold the two properties that matter: the order, which every stored record and every
provenance check depends on, and the absence of a second spelling in the handlers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from manifest.core.geometry import Box

HANDLERS = Path(__file__).resolve().parents[2] / "src/manifest/handlers"


def test_the_order_is_left_top_width_height() -> None:
    """Not (left, top, right, bottom). A record read in the other order points somewhere else."""
    box = Box(left=0.1, top=0.2, width=0.3, height=0.4)
    assert box.as_tuple() == (0.1, 0.2, 0.3, 0.4)


def test_a_box_is_not_a_sequence() -> None:
    """Stated as a test because the mistake is to assume it is.

    Keeping `Box` un-iterable is deliberate: four floats in an unnamed order is what provenance
    stops being checkable. The cost is that `list(box)` fails at runtime, and the answer to that
    is `as_tuple`, not `__iter__`.
    """
    with pytest.raises(TypeError):
        list(Box(left=0.1, top=0.2, width=0.3, height=0.4))  # type: ignore[call-overload]


@pytest.mark.parametrize("handler", ["publish.py", "escalate.py"])
def test_no_handler_unpacks_a_box_by_hand(handler: str) -> None:
    source = (HANDLERS / handler).read_text(encoding="utf-8")
    by_hand = re.findall(r"\w+\.box\.left\s*,", source)
    assert not by_hand, (
        f"{handler} unpacks a box field by field. It is not wrong, and that is the problem: the "
        f"other handler did it differently and the difference was a production TypeError. One "
        f"conversion, in core, named"
    )
    # Scoped to `found`, which is what `extract_from_pages` returns and therefore the name that
    # holds a `Box`. `list(outcome.box)` is *not* caught and must not be: `Outcome.box` is a
    # tuple by construction — it was converted on the way in — and turning it into a list for
    # JSON is the ordinary thing. The rule is about the type, and the name is the only handle a
    # source-level check has on it.
    assert not re.search(r"list\(\s*found\.box\s*\)", source), (
        f"{handler} calls list() on an Extracted's box, which is a `Box` and is not iterable. "
        f"It raises only where the line runs, which for the escalation is a tier that is "
        f"expensive to reach — use `.as_tuple()`"
    )
