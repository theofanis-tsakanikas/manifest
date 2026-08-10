"""CLAIM 3 — re-extraction is reproducible and versioned.

Same document, same reader identity, same contract version: identical record. A reader upgrade
produces a **new version with a diff**, never a silent overwrite, and the prior version stays
retrievable.

This is the claim that decides whether the archive is trustworthy after the fourth engine
change, and it rests on one property established elsewhere: `manifest.core` cannot read a
clock, a counter or a random source, so a version identifier here is derived from content and
from nothing else. `scripts/check_core_is_pure.py` is what makes that true rather than
intended, and `gate_proof.py` plants a `datetime.now()` in the core to prove it bites.

**A version is a digest over everything that could change the record.** The source bytes, the
reader identity, the contract version, and the published values themselves. Leaving any one out
makes two genuinely different records share a version, which is worse than having no version at
all: it is a version that lies.

**A diff never says "changed" without saying from what.** The prior value is carried into the
diff, because that is the whole of what a customs authority inspecting the record system under
UCC Art. 39(b) is entitled to see — and because "this field changed in the re-extraction" is
the least useful sentence a re-processing report can contain.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class Change(StrEnum):
    """What happened to one field between two versions."""

    UNCHANGED = "unchanged"
    #: The value moved. The most consequential kind, and the one that invalidates any human
    #: decision recorded against the old value.
    CHANGED = "changed"
    #: The old version published a value and the new one abstains. **Not a regression by
    #: itself**: a reader that stopped guessing is a reader that got better, and claim 1's
    #: threshold is what decides which it was.
    WITHDRAWN = "withdrawn"
    #: The new version publishes where the old one abstained.
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class PublishedField:
    """One field as a version published it, or the recorded fact that it abstained."""

    field: str
    value: str | None
    confidence: float | None
    page: int | None
    #: `left, top, width, height`, rounded, so a version identifier does not move on a float's
    #: last bit. Six places is a tenth of a pixel on an A4 page at 300 DPI.
    box: tuple[float, float, float, float] | None

    @property
    def published(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """One reading of one document, published.

    `version` is derived in `derive_version` and is a pure function of the arguments. Two
    versions with the same identifier are the same record, on any machine, in any year.
    """

    document_id: str
    source_digest: str
    reader: str
    contract_version: int
    fields: tuple[PublishedField, ...]
    version: str
    #: The version this one supersedes, or `None` for a first extraction. A chain rather than a
    #: pointer to "current", because the question an auditor asks is *what did you say before*.
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class FieldDiff:
    field: str
    change: Change
    before: str | None
    after: str | None

    def __str__(self) -> str:
        match self.change:
            case Change.CHANGED:
                return f"{self.field}: {self.before!r} -> {self.after!r}"
            case Change.WITHDRAWN:
                return f"{self.field}: {self.before!r} -> abstained"
            case Change.ADDED:
                return f"{self.field}: abstained -> {self.after!r}"
            case _:
                return f"{self.field}: unchanged"


@dataclass(frozen=True, slots=True)
class VersionDiff:
    """What changed between two versions of one document."""

    document_id: str
    before: str
    after: str
    changes: tuple[FieldDiff, ...]

    @property
    def identical(self) -> bool:
        return all(change.change is Change.UNCHANGED for change in self.changes)

    @property
    def material(self) -> tuple[FieldDiff, ...]:
        """Everything that is not `UNCHANGED`.

        The set a re-processing job must act on: a human decision recorded against a field in
        here is a decision about a value that no longer exists, and re-queuing it is the whole
        of decision 12.
        """
        return tuple(change for change in self.changes if change.change is not Change.UNCHANGED)


def derive_version(
    document_id: str,
    source_digest: str,
    reader: str,
    contract_version: int,
    fields: tuple[PublishedField, ...] | list[PublishedField],
) -> str:
    """A version identifier, from content alone.

    Everything that could change the record goes in. The fields are sorted by name first: a
    version that depended on the order the extractor happened to emit them would differ between
    two runs that published exactly the same thing, and claim 3 would be false for a reason
    nobody could find.
    """
    digest = hashlib.sha256()
    digest.update(f"{document_id}\x1f{source_digest}\x1f{reader}\x1f{contract_version}".encode())
    for field in sorted(fields, key=lambda entry: entry.field):
        box = "-" if field.box is None else ",".join(f"{value:.6f}" for value in field.box)
        # An unscored field hashes as the word, not as a number: a record whose reader reported
        # no confidence must not fingerprint identically to one that reported some value.
        score = "unscored" if field.confidence is None else f"{field.confidence:.6f}"
        digest.update(
            f"\x1e{field.field}\x1f{field.value}\x1f{score}\x1f{field.page}\x1f{box}".encode()
        )
    return digest.hexdigest()[:32]


def publish(
    *,
    document_id: str,
    source_digest: str,
    reader: str,
    contract_version: int,
    fields: tuple[PublishedField, ...] | list[PublishedField],
    supersedes: str | None = None,
) -> DocumentVersion:
    """Publish a version. Keyword-only, because the four strings in this signature are
    interchangeable to the type checker and a transposed pair would produce a plausible
    identifier for the wrong record."""
    ordered = tuple(sorted(fields, key=lambda entry: entry.field))
    return DocumentVersion(
        document_id=document_id,
        source_digest=source_digest,
        reader=reader,
        contract_version=contract_version,
        fields=ordered,
        version=derive_version(document_id, source_digest, reader, contract_version, ordered),
        supersedes=supersedes,
    )


def diff(before: DocumentVersion, after: DocumentVersion) -> VersionDiff:
    """What changed between two versions of the same document.

    Refuses two versions of *different* documents. Diffing them would produce a report in which
    every field changed, which reads like a catastrophic re-extraction and is a caller passing
    the wrong argument.
    """
    if before.document_id != after.document_id:
        raise ValueError(
            f"these are versions of different documents ({before.document_id} and "
            f"{after.document_id}); a diff between them would report that every field changed"
        )

    names = sorted({field.field for field in (*before.fields, *after.fields)})
    by_before = {field.field: field for field in before.fields}
    by_after = {field.field: field for field in after.fields}

    changes = []
    for name in names:
        old, new = by_before.get(name), by_after.get(name)
        old_value = old.value if old else None
        new_value = new.value if new else None
        if old_value == new_value:
            change = Change.UNCHANGED
        elif old_value is None:
            change = Change.ADDED
        elif new_value is None:
            change = Change.WITHDRAWN
        else:
            change = Change.CHANGED
        changes.append(FieldDiff(field=name, change=change, before=old_value, after=new_value))

    return VersionDiff(
        document_id=before.document_id,
        before=before.version,
        after=after.version,
        changes=tuple(changes),
    )


def surviving_decisions(
    decisions: dict[str, str], difference: VersionDiff
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Which recorded human decisions survive a re-extraction, and which must be re-queued.

    Decision 12: a decision already made on a field whose value did not change survives; a
    decision on a field that *did* change is a decision about a value that no longer exists.

    This is the function that makes bulk re-processing affordable, and it is also the one where
    an optimisation would be a lie. Carrying a decision across a changed value would preserve
    the *record* of human oversight while destroying the thing it was oversight of — a
    signature on a document somebody else has since edited.
    """
    changed = {change.field for change in difference.material}
    survives = {field: decision for field, decision in decisions.items() if field not in changed}
    requeue = tuple(sorted(field for field in decisions if field in changed))
    return survives, requeue
