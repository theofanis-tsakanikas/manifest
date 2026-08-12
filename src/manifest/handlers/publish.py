"""Extract the fields, apply the derived thresholds, and decide publish or queue.

**This is the step the deployed pipeline did not have.** The state machine read a page, compared
a confidence in a `Choice` state, called a gate that did not exist, and wrote an object — with no
step anywhere that ran this project's own logic. Every derived threshold, every contract, every
abstention rule lived in `src/manifest/core/` and executed only on a laptop. A pipeline whose
extraction logic never runs is a pipeline that publishes whatever the reader said.

**Nothing is decided here.** Every judgement comes from `core`:

- which fields a document type has, and their comparison rules → `contracts/documents/`
- the threshold for each → `recordings/`, derived, never written by hand
- whether a value clears it → `core.review.reason_for`
- what a field is, on the page → `core.fields.extract`

This handler's own job is the boring half: load, call, shape the answer. If a comparison
operator ever appears in this file, the derivation in `evals/calibration/` has been bypassed by
a line nobody re-reads.

**Thresholds are read from the deployed artefact, not recomputed.** They are derived from a
committed engine recording under a ceremony (`make ocr-record`) that prints every movement and
requires it to be accepted. Recomputing them here would let a threshold move because a page
arrived, silently, in production — claim 1 becoming decoration, in the one place where it
decides whether a value reaches a customer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manifest.contracts.loader import load
from manifest.core.document import Line, Page, ReadDocument, ReaderIdentity, Word
from manifest.core.fields import Extracted, extract_from_pages
from manifest.core.geometry import Box, PageSize
from manifest.core.review import Reason, reason_for
from manifest.handlers.emit import emit
from manifest.observability.telemetry import extraction_span


class HandlerError(RuntimeError):
    """This handler could not do its job. Raised, never swallowed."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """One field's destination, and the reason it went there.

    `reason` is never optional and never empty. A queue item whose reason is blank is one a
    reviewer cannot act on and an operator cannot aggregate, and the two failure modes it hides
    — a threshold too tight and a reader that reports nothing — have opposite fixes.
    """

    field: str
    value: str | None
    confidence: float | None
    page: int | None
    box: tuple[float, float, float, float] | None
    reason: str
    queued_because: Reason | None

    @property
    def publishable(self) -> bool:
        """Provisionally. The provenance gate runs after this and may still refuse."""
        return self.queued_because is None and self.value is not None


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Entry point. Takes the pointer `read_tier0` returned; returns per-field outcomes."""
    del context
    pointer = event.get("reading") or {}
    if not pointer.get("bucket") or not pointer.get("key"):
        raise HandlerError("the event carries no `reading` pointer; `read_tier0` returns one")

    document_type = str(event.get("document_type") or "").strip()
    if not document_type:
        raise HandlerError(
            "no `document_type` on the event. There is no default: the contract decides which "
            "fields exist, what each one's error budget is and how two of them are compared, "
            "and guessing it would apply one document's rules to another's page"
        )

    reading = _reading(_load_json(pointer["bucket"], pointer["key"]))
    contracts = load(Path(_contracts_directory()))
    contract = contracts.documents.get(document_type)
    if contract is None:
        raise HandlerError(
            f"no contract for document type {document_type!r}. A document whose contract is "
            f"absent cannot be published: nothing declares its error budgets, so no threshold "
            f"for it exists and nothing could be checked against one"
        )

    # The caption a field is printed under is per language, and the language is the reading's,
    # not a parameter of this call. A field looked for under an English caption on a Greek page
    # is a field that will not be found — which arrives as a *missing* value rather than as an
    # error, and missing is the answer this system takes seriously.
    language = next((page.language for page in reading.pages if page.language), None)
    if language is None:
        raise HandlerError(
            "the stored reading carries no language on any page, so no field's caption can be "
            "chosen. Refused rather than defaulted to English: the wrong caption produces a "
            "document of missing fields, which reads as a blank page rather than as a bug"
        )

    thresholds = _thresholds(reading.reader)
    outcomes = []
    for field in contract.fields:
        anchor = field.anchors.get(language)
        if anchor is None:
            raise HandlerError(
                f"field {field.name!r} has no caption declared for {language!r} in its "
                f"contract. Falling back to another language's caption would search the page "
                f"for words that are not on it"
            )
        outcomes.append(
            _outcome(field.name, extract_from_pages(reading.pages, field.name, anchor), thresholds)
        )

    published = sum(1 for outcome in outcomes if outcome.publishable)
    queued = sum(1 for outcome in outcomes if outcome.queued_because is not None)

    # `fields_published` against `fields_queued` is claim 5's queue load per document — the
    # number that turns "abstention is the safe state" into something an operator can watch
    # rather than a sentence in a doctrine.
    emit(
        extraction_span(
            trace_id=reading.fingerprint()[:32],
            span_id=f"publish-{reading.fingerprint()[:16]}",
            parent=f"read-{reading.fingerprint()[:16]}",
            document_version=reading.fingerprint(),
            document_type=document_type,
            reader_tier=0,
            language=language,
            fields_extracted=len(outcomes),
            fields_published=published,
            fields_queued=queued,
        )
    )

    return {
        "document_id": reading.source_id,
        "fingerprint": reading.fingerprint(),
        "document_type": document_type,
        # **The field the escalation could not run without, and that this returned without.**
        #
        # It is derived above, from the reading's own pages, and it was used and dropped. The
        # next state is `Escalate`, which is handed this object and needs the language before it
        # can ask `contracts/cascade/routing.yaml` which tiers may read the page at all — so the
        # first document through a deployed estate with the tiers on failed at that state, on a
        # fact this function had computed and thrown away.
        #
        # Language belongs in the outcome rather than being re-derived downstream for the same
        # reason `document_type` does: it is a property of *this reading*, decided once, and a
        # second derivation is a second chance to decide it differently.
        "language": language,
        "reader": str(reading.reader),
        "fields": [
            {
                "field": outcome.field,
                "value": outcome.value,
                "confidence": outcome.confidence,
                "page": outcome.page,
                "box": list(outcome.box) if outcome.box else None,
                "reason": outcome.reason,
                "queued_because": (
                    outcome.queued_because.value if outcome.queued_because else None
                ),
                "publishable": outcome.publishable,
            }
            for outcome in outcomes
        ],
        # Counted here so the next state does not have to, and so a document where *everything*
        # is queued is visible as one number rather than as a list somebody has to scan. A
        # pipeline quietly queueing 100% is the shape claim 5 exists to detect.
        "publishable_count": published,
        "queued_count": queued,
    }


def _outcome(field: str, found: Extracted, thresholds: dict[str, float | None]) -> Outcome:
    if not found.found:
        return Outcome(
            field=field,
            value=None,
            confidence=None,
            page=found.page,
            box=None,
            # Missing is missing, and it is stated. Doctrine rule 3: no modal value, no zero,
            # no "the usual". A field absent from a page is a fact about that page.
            reason=found.reason,
            queued_because=None,
        )

    # `thresholds.get(field)` returning `None` is ambiguous on its own — a field declared
    # always-review and a field nobody derived a threshold for look identical. So absence from
    # the mapping is refused, and only an explicit `None` inside it means always-review.
    if field not in thresholds:
        raise HandlerError(
            f"no threshold entry for {field!r} in the deployed artefact. Refused rather than "
            f"treated as always-review: a field missing from the derivation is a deployment "
            f"that does not match its contract, and calling it always-review would hide that "
            f"behind behaviour that looks deliberate"
        )

    because = reason_for(found.confidence, thresholds[field])
    return Outcome(
        field=field,
        value=found.value,
        confidence=found.confidence,
        page=found.page,
        box=(found.box.left, found.box.top, found.box.width, found.box.height)
        if found.box
        else None,
        reason=found.reason,
        queued_because=because,
    )


def _thresholds(reader: ReaderIdentity) -> dict[str, float | None]:
    """The derived thresholds for this reader, as deployed.

    Keyed by reader identity, because a threshold derived for one reader says nothing about
    another: two readers' 0.8 are different events. A deployment carrying thresholds for a
    reader other than the one that produced this reading is refused rather than approximated.
    """
    bucket = _env("RECORDS_BUCKET")
    try:
        payload = _load_json(bucket, f"thresholds/{reader.slug}.json")
    except Exception as exc:
        # **The refusal that has to name the disagreement, because the disagreement is the
        # whole point.** A missing artefact here does not mean somebody forgot a file. It means
        # the deployment is reading with one reader and carrying thresholds derived from
        # another, and two readers' 0.8 are different events — so publishing on the wrong
        # artefact would put a number on a customs record that no evidence supports.
        #
        # It is spelled out because of how it first appeared: `s3:ListBucket is not authorized`,
        # from a handler whose grant was correct, on a deploy that was green, for a reader
        # difference of one patch version. Nothing in that message contained the word "reader".
        raise HandlerError(
            f"no threshold artefact for {reader}. This deployment reads with {reader} and the "
            f"thresholds shipped were derived from: {_shipped_for(bucket) or 'nothing found'}. "
            f"A threshold derived for one reader says nothing about another, so this refuses "
            f"rather than applying the nearest one. Underlying: {exc}"
        ) from exc
    entries = payload.get("thresholds")
    if not isinstance(entries, dict):
        raise HandlerError(f"the threshold artefact for {reader} has no `thresholds` object")
    return {name: None if value is None else float(value) for name, value in entries.items()}


def _reading(payload: dict[str, Any]) -> ReadDocument:
    """Rebuild the normalised representation from what `read_tier0` wrote.

    Rebuilt through the real constructors rather than trusted as-is, so that every invariant
    the representation carries — a confidence is a fraction or absent, a box is inside its page
    — is re-checked on the way back in. An object in a bucket is not a Python object; anything
    that can edit the bucket can edit the reading.
    """
    try:
        pages = tuple(
            Page(
                number=int(page["number"]),
                size=PageSize(width=int(page["width"]), height=int(page["height"])),
                lines=tuple(
                    Line(
                        words=tuple(
                            Word(
                                text=str(word["text"]),
                                confidence=word["confidence"],
                                box=Box(*(float(value) for value in word["box"])),
                            )
                            for word in line["words"]
                        ),
                        confidence=line["confidence"],
                    )
                    for line in page["lines"]
                ),
                language=page.get("language"),
                language_confidence=page.get("language_confidence"),
            )
            for page in payload["pages"]
        )
        reading = ReadDocument(
            source_id=str(payload["document_id"]),
            source_digest=str(payload["source_digest"]),
            reader=ReaderIdentity(
                name=str(payload["reader"]["name"]), version=str(payload["reader"]["version"])
            ),
            pages=pages,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HandlerError(f"the stored reading is not the documented shape: {exc}") from exc

    # Claim 3, checked at the point of use rather than assumed. If the stored fingerprint and
    # the recomputed one disagree, the object was edited after it was written, and publishing
    # from it would attribute a value to a reading that never produced it.
    stored = payload.get("fingerprint")
    if stored and stored != reading.fingerprint():
        raise HandlerError(
            f"the stored reading's fingerprint is {stored} and it recomputes to "
            f"{reading.fingerprint()}. The object changed after it was written"
        )
    return reading


def _shipped_for(bucket: str) -> str:
    """Which readers this deployment does carry thresholds for.

    Best effort on purpose. It runs inside the handling of a failure, and a refusal that itself
    raises tells the operator nothing at all — so a listing that is denied, throttled or empty
    degrades to a shorter message rather than replacing the real one.
    """
    try:
        listing = _s3().list_objects_v2(Bucket=bucket, Prefix="thresholds/")
    except Exception:  # the message is the product; never let this throw
        return ""
    return ", ".join(
        entry["Key"].removeprefix("thresholds/").removesuffix(".json")
        for entry in listing.get("Contents", ())
    )


def _load_json(bucket: str, key: str) -> dict[str, Any]:
    body = _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


def _contracts_directory() -> str:
    return os.environ.get("CONTRACTS_DIR", "/var/task/contracts")


def _s3():
    """The client, constructed on use.

    Imported inside the function rather than at module scope, for the same reason
    `pyproject.toml` keeps the cloud SDK out of the hard dependencies: this module must import
    on a machine that has no AWS libraries at all, so that its parsing, its refusals and its
    payload shape can be unit-tested offline like everything else in this repository. The
    runtime that actually invokes it has the SDK.
    """
    import boto3  # noqa: PLC0415 - deliberate; see the docstring

    return boto3.client("s3")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
