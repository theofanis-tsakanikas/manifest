"""The caption a field is printed under, read from the contract that declares it.

One source of truth. `contracts/documents/*.yaml` carries `anchors` per language; the corpus
renders the caption from there and extraction looks for it there. Duplicating the caption into
the generator would be two descriptions of one label, and they diverge on the first busy
afternoon.

This is not the tautology `PLAN.md` warns about. The anchor is how a field is *found*; what
claims 1 and 2 measure is whether the value beside it was read correctly and located correctly,
and ground truth for that value is recorded independently at the moment it is drawn.

Note which module this is: `corpus/anchors.py`, not `corpus/plant.py`. The planting-blindness
gate forbids `plant.py` and `world.py` from reaching the contract layer, because a planter that
knows the reconciliation rules is a planter that can be tuned to them. A *renderer* that knows
what a field is captioned is just a renderer that prints the right word.
"""

from __future__ import annotations

from functools import cache

from corpus.world import Language
from manifest.contracts.loader import ContractSet, default_root, load


@cache
def contracts() -> ContractSet:
    return load(default_root())


def anchor(document: str, field: str, language: Language) -> str:
    declared = contracts().document(document).field(field).anchors
    try:
        return declared[language.value]
    except KeyError as exc:
        raise KeyError(
            f"{document}.{field} declares no anchor in {language.value}. Every field carries a "
            f"caption in all three document languages; falling back to English would render a "
            f"document that claims to be Greek and is not"
        ) from exc
