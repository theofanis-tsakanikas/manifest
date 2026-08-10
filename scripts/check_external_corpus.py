#!/usr/bin/env python3
"""The out-of-distribution set is licensed, or it is not read.

Every threshold in this repository is derived from a corpus the repository generated. Decision 13
requires a real public document set as the honesty check on that, and the same decision adds the
rule this script enforces: **verify the licence before committing anything.**

So the gate is inverted from the usual shape. It does not check that a dataset is *present* — its
absence is a stated gap, not a failure. It checks that if one **is** present, it arrived with its
terms read and quoted, and it refuses to let anything score against a set that did not.

**Why a check rather than a note.** The temptation here is specific and strong: a scoreboard with
an out-of-distribution column is worth a great deal to this project, and the fastest way to get
one is to download something, score it, and read the terms afterwards. That is the order this
refuses. A repository that spends nine hundred lines arguing that a number must be traceable
cannot vendor somebody else's documents on the strength of a search result.

**What it deliberately does not do:** judge whether a licence permits what is being done. That is
a legal reading, this is a script, and a script that returned "permitted" would be the most
dangerous output in the repository. What it requires is that a human wrote down which licence,
where they read it, and when — the same standard `docs/REGULATORY.md` applies to every legal
statement in this project.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "corpus" / "external"
LICENCE = EXTERNAL / "LICENCE.md"

#: Extensions that are documents rather than notes. A set is "present" when any of these is.
DOCUMENTS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"})

#: What the licence file must actually contain. Each is a thing a person had to look up, and
#: none can be produced by a script — which is the whole point of requiring them.
REQUIRED = {
    "a source URL": re.compile(r"https?://\S+"),
    "the date it was read": re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
    "a named licence": re.compile(
        r"\b(CC[- ]BY|CC0|CDLA|Apache|MIT|BSD|ODbL|OGL|public domain)\b", re.IGNORECASE
    ),
    "a quotation of the permission": re.compile(r"[\"“>]"),
}


def main() -> int:
    documents = sorted(
        path for path in EXTERNAL.rglob("*") if path.is_file() and path.suffix.lower() in DOCUMENTS
    )

    # **A recording counts as the set being present.**
    #
    # This repository deliberately redistributes no image: `recordings/external/` holds
    # confidences and correctness flags, and nothing else. The first version of this check
    # looked only for image files, so the state that actually exists here — a recording derived
    # from somebody's data, with no image beside it — read as "no set present" and passed
    # without ever looking at the licence.
    #
    # That is the same shape as every other finding in this repository: a check scoped to what
    # was expected rather than to what is there. What triggers the obligation is having used the
    # data, not having stored it.
    recording = (ROOT / "recordings" / "external").exists()

    if not documents and not recording:
        print(
            "external-corpus: no out-of-distribution set present.\n"
            "  This is a stated gap, not a failure — `corpus/external/README.md` records what a\n"
            "  set has to satisfy before it lands, and `docs/DECISIONS.md` 13 records why the\n"
            "  check matters. Until one arrives, the only measurement against paper this\n"
            "  repository did not design is the ISO 6346 check digit, which gives a lower bound\n"
            "  on the error rate and nothing else."
        )
        return 0

    if not LICENCE.exists():
        present = (
            f"{len(documents)} document(s) are present"
            if documents
            else "a recording derived from an external set exists"
        )
        print(
            f"external-corpus: {present} and {LICENCE.name} is not.\n\n"
            f"  Refused. Decision 13's rule is to verify the licence *before* committing "
            f"anything, and the failure mode it guards is specific: an out-of-distribution "
            f"column is worth a lot to this project, and the fastest way to get one is to "
            f"score first and read the terms afterwards.\n\n"
            f"  A dataset of scanned commercial documents is somebody's data before it is "
            f"anybody's benchmark.",
            file=sys.stderr,
        )
        return 1

    text = LICENCE.read_text(encoding="utf-8")
    missing = [name for name, pattern in REQUIRED.items() if not pattern.search(text)]
    if missing:
        print(
            f"external-corpus: {LICENCE.name} is missing {missing}.\n\n"
            f"  Each of those is something a person had to look up and none can be produced by "
            f"a script, which is why they are required. `docs/REGULATORY.md` holds every legal "
            f"statement in this project to article, instrument and date; a dataset licence is "
            f"not the place to relax it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"external-corpus: {len(documents)} document(s), licence recorded with a source and a "
        f"date read.\n"
        f"  This script does not judge whether the licence permits what is being done — that is "
        f"a legal reading, and a script returning 'permitted' would be the most dangerous "
        f"output in this repository. It requires that a human wrote down which licence, from "
        f"where, and when."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
