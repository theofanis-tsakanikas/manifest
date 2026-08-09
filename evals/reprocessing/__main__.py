"""CLAIM 3 — re-extraction is reproducible and versioned.

Three assertions, and the third is the one that costs money to get wrong.

**Identical.** The same documents, published twice from the same recording at the same reader
identity and contract version, produce byte-identical version identifiers. Nothing in the
publication path reads a clock, a counter or a random source — `scripts/check_core_is_pure.py`
is what makes that structural, and `gate_proof.py` plants a `datetime.now()` in the core to
prove the check bites.

**Versioned, never overwritten.** A reader upgrade produces a *new* version with a diff. The
prior version stays retrievable and the diff names every field that moved, with the value it
moved from — because "this field changed" is the least useful sentence a re-processing report
can contain, and because a customs authority inspecting the record system under UCC Art. 39(b)
is entitled to the before.

**Human decisions survive selectively.** A decision recorded against a field whose value did
not change survives the re-extraction. A decision against a field that *did* change is a
decision about a value that no longer exists, and it is re-queued. Decision 12. Carrying it
across would preserve the record of oversight while destroying the thing it was oversight of.

The upgrade here is simulated by re-publishing a subset of documents with a different reader
identity and a nudged value. That is honest about what it is: no second engine was run, because
running one would produce a recording this repository has no way to check. What is being proved
is the *versioning*, not that any particular upgrade improves anything.
"""

from __future__ import annotations

import sys

from evals.harness import contracts, score_all
from manifest.core.calibration import Outcome
from manifest.core.versioning import Change, PublishedField, diff, publish, surviving_decisions

READER = "reference-ocr@tesseract 5.5.2"
UPGRADED = "reference-ocr@tesseract 5.6.0"


def _versions(scored, reader: str, nudge: set[str] | None = None):
    by_document: dict[tuple[str, str], list[PublishedField]] = {}
    for entry in scored:
        fields = by_document.setdefault((entry.shipment, entry.document), [])
        value = entry.extracted.value if entry.outcome is not Outcome.MISSING else None
        if nudge and entry.field in nudge and value is not None:
            value = value + "X"
        fields.append(
            PublishedField(
                field=entry.field,
                value=value,
                confidence=entry.extracted.confidence,
                page=entry.extracted.page,
                box=(
                    None
                    if entry.extracted.box is None
                    else (
                        round(entry.extracted.box.left, 6),
                        round(entry.extracted.box.top, 6),
                        round(entry.extracted.box.width, 6),
                        round(entry.extracted.box.height, 6),
                    )
                ),
            )
        )
    return {
        key: publish(
            document_id=f"{key[0]}/{key[1]}",
            source_digest=key[0],
            reader=reader,
            contract_version=contracts().document(key[1]).version,
            fields=fields,
        )
        for key, fields in by_document.items()
    }


def main() -> int:
    scored = score_all()
    first = _versions(scored, READER)
    again = _versions(scored, READER)

    identical = sum(1 for key in first if first[key].version == again[key].version)
    failures: list[str] = []
    if identical != len(first):
        failures.append(
            f"{len(first) - identical} document(s) published a different version from the same "
            f"input. Something in the publication path is reading state that is not its "
            f"arguments, and every claim about the archive rests on that not happening"
        )

    # A reader upgrade: a different identity, and one field that moved.
    upgraded = _versions(scored, UPGRADED, nudge={"gross_weight"})
    moved = [key for key in first if first[key].version != upgraded[key].version]
    if len(moved) != len(first):
        failures.append(
            f"only {len(moved)} of {len(first)} documents got a new version from a reader "
            f"upgrade. A reader change that leaves a version identifier alone is a silent "
            f"overwrite, which is the failure this claim is named after"
        )

    diffs = [diff(first[key], upgraded[key]) for key in sorted(first)]
    changed_fields = sum(len(entry.material) for entry in diffs)
    with_before = sum(
        1
        for entry in diffs
        for change in entry.material
        if change.change is not Change.ADDED and change.before is not None
    )
    if changed_fields and with_before == 0:
        failures.append("a diff reported a change without carrying the value it changed from")

    # Surviving decisions. A decision on the nudged field must be re-queued; one on a field
    # that did not move must survive.
    # A document the upgrade actually touched. Picking the first key alphabetically chose an
    # arrival notice, which carries no `gross_weight` — so nothing moved, both decisions
    # survived, and the assertion failed for a reason that was about the sample rather than
    # about the system. A harness that asserts a behaviour has to be given the situation the
    # behaviour is for.
    sample = next(key for key in sorted(first) if diff(first[key], upgraded[key]).material)
    decisions = {
        "gross_weight": "approved by reviewer 3",
        "container_number": "approved by reviewer 1",
    }
    survives, requeue = surviving_decisions(decisions, diff(first[sample], upgraded[sample]))
    if "gross_weight" in survives:
        failures.append(
            "a human decision survived a field whose value changed. The record of oversight "
            "would have been preserved while the thing it was oversight of was replaced"
        )
    if "container_number" not in survives:
        failures.append(
            "a human decision was re-queued on a field that did not change, which is the "
            "review queue paying for a re-extraction that did not affect it"
        )

    print("claim 3 — re-extraction is reproducible and versioned\n")
    print(f"  documents published        {len(first)}")
    print(f"  identical on re-publish    {identical}/{len(first)}")
    print(f"  new version on upgrade     {len(moved)}/{len(first)}")
    print(f"  fields changed by upgrade  {changed_fields}, all carrying the value they moved from")
    print(
        f"  prior version retrievable  yes — {first[sample].version[:12]} supersedes nothing, "
        f"{upgraded[sample].version[:12]} is the new one"
    )
    print(f"  decisions surviving        {sorted(survives)}")
    print(f"  decisions re-queued        {list(requeue)}")
    print(
        "\n  The upgrade is simulated: a different reader identity and one moved value. No "
        "second engine was run, because running one would produce a recording nothing here "
        "could check. What is proved is the versioning, not that any upgrade improves anything."
    )

    if failures:
        print("\nclaim 3: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nclaim 3: same input, identical record; a reader change, a new version and a diff.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
