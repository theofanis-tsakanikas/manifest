"""Read a document with the local reference reader and emit the normalised representation.

Tier 0, running in the estate. **The same binary, at the same version, from the same image as
the one that produces `recordings/ocr/` on a laptop.** That is the point of shipping this as a
container rather than a zip: the reader and its language data *are* the artefact, and a tier 0
that behaved differently in the cloud would make every threshold in this repository a statement
about a machine nobody else can reproduce.

**Why this handler exists.** The state machine called the metered per-page OCR service in a step
named `ReadAtTierZero`. That is not tier 0 — it is tier 1 wearing tier 0's name, and it deletes
the cascade's reason for existing, because the local reader is precisely what keeps the metered
engine off pages that do not need it. A cost model whose cheapest tier is a paid service is a
cost model for a different system.

**This handler decides nothing.** It rasterises, reads, writes, and returns a pointer. No
threshold is compared here and no field is extracted here. Nothing is skipped on error either: a
page that could not be read is a failure, because a reading short by an unknown amount is
indistinguishable downstream from a page with less text on it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manifest.extraction.local import reader as local_reader

#: The language data this scenario needs, checked on every cold start rather than assumed.
#:
#: A container missing its Greek data does not fail on a Greek page. It reads the page as though
#: it were English and returns confident text in the wrong alphabet, and every threshold derived
#: from that is a statement about a language nobody read. Fail-closed is the only safe posture.
REQUIRED_LANGUAGES: frozenset[str] = frozenset({"eng", "ell", "nld", "deu", "fra"})

#: The object-key convention the landing zone declares, and the only way a document's language
#: and type reach this system.
#:
#: `incoming/<language>/<document_type>/<document-id>.pdf`
#:
#: Parsed here, in Python, rather than in an EventBridge input transformer — which cannot split
#: a string, so the alternative was either a rule per document type or a default. Both are worse
#: than a convention that fails closed: an object whose key does not match is refused, by name,
#: instead of being read in a language nobody chose.
#:
#: There is no fallback and no guess. A page read in the wrong language returns confident text in
#: an alphabet the reader never saw, and `contracts/cascade/` routes on that language.
KEY_CONVENTION: re.Pattern[str] = re.compile(
    r"^incoming/(?P<language>[a-z]{2})/(?P<document_type>[a-z][a-z0-9_]*)/(?P<document_id>[^/]+)\.pdf$"
)

#: Render resolution. Declared here, once, because it is a number the whole system depends on:
#: `docs/AWS-CONSTRAINTS.md` records a 15-pixel character-height floor below which the managed
#: services reject a page, and a local render that goes under it would produce a corpus this
#: system can read and the escalation tiers cannot.
RENDER_SCALE: float = 200 / 72


class HandlerError(RuntimeError):
    """This handler could not do its job. Raised, never swallowed."""


@dataclass(frozen=True, slots=True)
class Request:
    """What the state machine passes in, parsed rather than trusted.

    The object key comes from an S3 notification, and an object key is chosen by whoever put the
    object in the landing bucket. In this domain that is a counterparty. So it is validated at
    the boundary, in the same spirit as `manifest.security.injection` treats document text.
    """

    bucket: str
    key: str
    document_id: str
    language: str
    document_type: str

    @classmethod
    def of(cls, event: dict[str, Any]) -> Request:
        if not event.get("bucket") or not event.get("key"):
            raise HandlerError(
                "the event must carry a bucket and a key; this is not the documented input"
            )

        key = str(event["key"])
        if ".." in key or key.startswith("/"):
            raise HandlerError(
                f"refusing an object key that traverses: {key!r}. The key is attacker-chosen "
                f"and it becomes a local path when the object is downloaded"
            )

        matched = KEY_CONVENTION.match(key)
        if not matched:
            raise HandlerError(
                f"{key!r} does not match the landing convention "
                f"`incoming/<language>/<document_type>/<id>.pdf`. Refused rather than guessed: "
                f"the language decides which reader may see the page and the document type "
                f"decides which contract applies, and neither has a safe default"
            )

        return cls(
            bucket=str(event["bucket"]),
            key=key,
            document_id=str(event.get("document_id") or matched.group("document_id")),
            language=matched.group("language"),
            document_type=matched.group("document_type"),
        )


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Entry point. Returns *where* the reading was written, never the reading itself.

    A normalised reading of a thirty-page scan is megabytes and a state-machine payload has a
    documented size limit. Returning a pointer means a large document fails here, by name, rather
    than three steps later as a payload error nobody can attribute.
    """
    del context
    request = Request.of(event)
    records = _env("RECORDS_BUCKET")
    _require_languages()

    with tempfile.TemporaryDirectory() as workspace:
        directory = Path(workspace)
        source = directory / "source"
        _s3().download_file(request.bucket, request.key, str(source))
        pages = _rasterise(source, directory, request.language)
        if not pages:
            raise HandlerError(
                f"{request.key!r} rendered to no pages. An empty reading published as a "
                f"successful one is a document that silently contributes nothing"
            )
        reading = local_reader.read_document(source_id=request.document_id, pages=pages)

        # **The renders go with the reading, and this is not an optimisation.**
        #
        # The provenance gate re-opens the page to check that the recorded box carries ink and
        # that the crop re-reads to the published value. It cannot do that from a temporary
        # directory in a process that has already exited, and a reviewer shown a queued field
        # needs the same crop. Without this upload the gate finds no page, reports every field
        # uncheckable — which is a refusal — and the pipeline sends 100% of its volume to a
        # human while reporting success. The sixth time in this repository that something read
        # an artefact nothing wrote.
        for path, number, _, _ in pages:
            _s3().put_object(
                Bucket=records,
                Key=f"renders/{request.document_id}/page-{number:04d}.png",
                Body=path.read_bytes(),
                ContentType="image/png",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=_env("DATA_KEY_ARN"),
            )

    payload = {
        "document_id": reading.source_id,
        "source_digest": reading.source_digest,
        "reader": {"name": reading.reader.name, "version": reading.reader.version},
        "fingerprint": reading.fingerprint(),
        "language": request.language,
        "pages": [
            {
                "number": page.number,
                "width": page.size.width,
                "height": page.size.height,
                "language": page.language,
                "language_confidence": page.language_confidence,
                "lines": [
                    {
                        "confidence": line.confidence,
                        "words": [
                            {
                                "text": word.text,
                                "confidence": word.confidence,
                                "box": [
                                    word.box.left,
                                    word.box.top,
                                    word.box.width,
                                    word.box.height,
                                ],
                            }
                            for word in line.words
                        ],
                    }
                    for line in page.lines
                ],
            }
            for page in reading.pages
        ],
    }

    # Keyed by reader version. Claim 3 is "same document, same reader version, identical record",
    # and a key that omitted the version would have one reader's reading overwrite another's —
    # which is the silent overwrite doctrine rule 4 exists to forbid.
    key = f"readings/{reading.reader.slug}/{request.document_id}.json"
    _s3().put_object(
        Bucket=records,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
        # Named on the request rather than inherited from a bucket default. A bucket default can
        # be edited by somebody who never read this file; a request parameter is in the diff.
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=_env("DATA_KEY_ARN"),
    )

    scores = [word.confidence for page in reading.pages for word in page.words]
    return {
        "document_id": reading.source_id,
        "reading": {"bucket": records, "key": key},
        "fingerprint": reading.fingerprint(),
        "language": request.language,
        # Carried out of here rather than taken from the trigger's payload, so that the type the
        # contract is chosen by and the language the page was read in come from one parse of one
        # key. Two parses in two places is how a document gets read as Greek and thresholded as
        # a bill of lading.
        "document_type": request.document_type,
        "tier": 0,
        "pages": len(reading.pages),
        # `None` where any word was unscored, and `None` survives into the state machine as
        # `null` for the routing Choice to test explicitly. A number standing in for the absence
        # is the one substitution that would clear every threshold in this repository.
        "lowest_confidence": (
            None if not scores or any(score is None for score in scores) else min(scores)
        ),
    }


def _require_languages() -> None:
    try:
        local_reader.require_languages(set(REQUIRED_LANGUAGES))
    except local_reader.ReaderUnavailable as exc:
        raise HandlerError(
            f"{exc}. Fail-closed on purpose: a reader missing a language does not error on a "
            f"page in it, it returns confident text in the wrong alphabet"
        ) from exc


def _rasterise(source: Path, directory: Path, language: str) -> list[tuple[Path, int, Any, str]]:
    """Render every page to an image the reader can open.

    Imported here rather than at module scope so that the module imports without the rendering
    library present — which is what lets the unit tests exercise `Request` parsing and the
    payload shape on a machine that has neither the library nor the OCR binary.
    """
    import pypdfium2  # noqa: PLC0415 - deliberate; see the docstring
    from PIL import Image  # noqa: PLC0415

    from manifest.core.geometry import PageSize  # noqa: PLC0415

    rendered: list[tuple[Path, int, Any, str]] = []
    document = pypdfium2.PdfDocument(str(source))
    try:
        for index in range(len(document)):
            image = document[index].render(scale=RENDER_SCALE).to_pil()
            path = directory / f"page-{index + 1:04d}.png"
            image.save(path, format="PNG")
            with Image.open(path) as opened:
                size = PageSize(width=opened.width, height=opened.height)
            rendered.append((path, index + 1, size, language))
    finally:
        document.close()
    return rendered


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        raise HandlerError(
            f"{name} is not set. Refused rather than defaulted: a default bucket or key name "
            f"here would write this document somewhere nobody declared"
        )
    return value
