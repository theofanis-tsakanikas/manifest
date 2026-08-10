"""Check every publishable field's box against the page, before anything is written.

**This is the function the state machine named and that did not exist.** `VerifyProvenance`
invoked `manifest-provenance-gate`; no layer created it, no code implemented it, and the step
carried `Catch: States.ALL -> QueueForReview`. So on a real deployment every document would have
failed here with `ResourceNotFoundException`, been caught, and gone to a human — a pipeline
reporting success while routing 100% of its volume to review. Green, silent, and exactly the
failure mode this project exists to argue against.

**Why it is a separate step rather than part of `publish`.** It needs the raster a second time.
Claim 2 is that the box is checked *against the page*, not against the record that produced it,
so this handler re-opens the image and looks. Folding it into the extraction step would put the
check in the same process as the thing it checks, which is the tautology decision 8 refuses.

**It decides nothing either.** `manifest.gates.provenance.verify` decides; this loads the page,
implements the `Raster` protocol over it, and reports. The three layers and their unequal
strengths are declared there and are not restated here, because a second description of a rule
is a second rule.

**Fail closed.** A page that cannot be fetched, a crop that cannot be read, an unexpected error
of any kind — all of them refuse the field. The tempting `except Exception: return verified` is
this file's one catastrophic edit, and the reason the mutation harness attacks it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from manifest.contracts.loader import load
from manifest.core.geometry import Box, PageSize
from manifest.gates.provenance import InkStatistics, Provenance, verify


class HandlerError(RuntimeError):
    """This handler could not do its job. Raised, never swallowed."""


class _PageRaster:
    """The `Raster` protocol over pages fetched from object storage.

    Fetches lazily and caches per page, because a document's fields cluster on one or two pages
    and re-downloading a 4 MB render per field would make the gate the slowest thing in the
    pipeline — which is how a gate ends up being made optional.
    """

    def __init__(self, bucket: str, prefix: str, workspace: Path) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._workspace = workspace
        self._pages: dict[int, Path | None] = {}

    def _path(self, page: int) -> Path | None:
        if page not in self._pages:
            destination = self._workspace / f"page-{page:04d}.png"
            try:
                _s3().download_file(
                    self._bucket, f"{self._prefix}/page-{page:04d}.png", str(destination)
                )
                self._pages[page] = destination
            except Exception:
                # Deliberately broad, and deliberately `None` rather than a raise. The gate
                # treats a missing page as *uncheckable*, which is a refusal — see
                # `Verdict.UNCHECKABLE`. Distinguishing a 404 from a KMS denial from a timeout
                # would change nothing here: none of them lets us look at the page.
                self._pages[page] = None
        return self._pages[page]

    def size(self, page: int) -> PageSize | None:
        path = self._path(page)
        if path is None:
            return None
        from PIL import Image  # noqa: PLC0415 - keeps this module importable without imaging

        with Image.open(path) as image:
            return PageSize(width=image.width, height=image.height)

    def ink(self, page: int, box: Box) -> InkStatistics | None:
        path = self._path(page)
        if path is None:
            return None
        from manifest.extraction.local.raster import PageRaster  # noqa: PLC0415

        return PageRaster(pages={page: path}).ink(page, box)

    def reread(self, page: int, box: Box, language: str) -> tuple[str, float]:
        path = self._path(page)
        if path is None:
            raise HandlerError(f"page {page} is not available, so its crop cannot be re-read")
        from manifest.extraction.local.raster import PageRaster  # noqa: PLC0415

        return PageRaster(pages={page: path}).reread(page, box, language)


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    """Entry point. Takes `publish`'s output; returns it with a verdict per field.

    The return carries `verified: false` when **any** publishable field was refused. The state
    machine branches on that single boolean, so there is no arrangement of per-field results
    that lets a refused field through because a later state read the wrong key.
    """
    del context
    fields = event.get("fields")
    if not isinstance(fields, list):
        raise HandlerError("the event carries no `fields` list; `publish` returns one")

    document_id = str(event.get("document_id") or "")
    document_type = str(event.get("document_type") or "")
    if not document_id or not document_type:
        raise HandlerError("the event must name both the document and its type")

    language = str(event.get("language") or "")
    if not language:
        raise HandlerError(
            "no language on the event. The re-read layer reads the crop in a language, and "
            "reading a Greek crop as English returns confident text in the wrong alphabet — "
            "which would refuse a correct field and, worse, teach an operator to mute the gate"
        )

    # Every check on the event itself happens before any I/O. An event that was never going to
    # be processable should fail on its own contents, not on a contract read or an object fetch
    # — otherwise the error a reader sees names the storage and not the cause.
    contracts = load(Path(os.environ.get("CONTRACTS_DIR", "/var/task/contracts")))
    contract = contracts.documents.get(document_type)
    if contract is None:
        raise HandlerError(f"no contract for document type {document_type!r}")

    checked: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as workspace:
        raster = _PageRaster(
            bucket=_env("RECORDS_BUCKET"),
            prefix=f"renders/{document_id}",
            workspace=Path(workspace),
        )
        for entry in fields:
            checked.append(_check(entry, contract, language, raster))

    refused = [entry for entry in checked if entry["verdict"] != "verified"]
    return {
        **event,
        "fields": checked,
        # One boolean, computed here. A state machine `Choice` that had to scan a list would be
        # a rule expressed in Amazon States Language, where nothing in this repository can test
        # it — and claim 2's gate would live somewhere no mutation can attack.
        "verified": not refused,
        "refused_count": len(refused),
    }


def _check(
    entry: dict[str, Any], contract: Any, language: str, raster: _PageRaster
) -> dict[str, Any]:
    """One field. Anything unexpected refuses it."""
    if not entry.get("publishable"):
        # A field already bound for the queue is not checked and is not marked verified either.
        # Marking it verified would be a lie of convenience that an aggregate over this list
        # would then count as a passing check.
        return {**entry, "verdict": "not_applicable", "layer": None, "check_reason": "queued"}

    try:
        field = contract.field(entry["field"])
        box = entry.get("box")
        if not box or entry.get("page") is None:
            raise HandlerError("a publishable field with no box or page reached the gate")

        provenance = Provenance(
            field=str(entry["field"]),
            value=str(entry["value"]),
            page=int(entry["page"]),
            box=Box(*(float(value) for value in box)),
            language=language,
            comparison=tuple(field.comparison),
            # Read from the field's declared *type*, exactly as `evals/provenance/` does.
            # One rule, one place: if this handler decided self-checking by its own criterion,
            # the gate would behave differently in the estate than in the eval that proves it,
            # and the eval would stop being evidence about the deployed system.
            self_checking=field.type.value == "container_number",
        )
        check = verify(provenance, raster)
    except Exception as exc:
        return {
            **entry,
            "verdict": "refused",
            "layer": None,
            "check_reason": (
                f"the provenance check could not complete: {exc}. That is a refusal, not a "
                f"pass. A gate that publishes what it failed to check is not a gate"
            ),
        }

    return {
        **entry,
        "verdict": check.verdict.value,
        "layer": check.layer.value if check.layer else None,
        "check_reason": check.reason,
    }


def _s3():
    """The client, constructed on use — see `publish._s3` for why it is imported here."""
    import boto3  # noqa: PLC0415

    return boto3.client("s3")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value
