"""The escalation resolves the pointer at the path the state machine actually puts it.

**The shape is not this test's opinion. It is read out of `infra/extraction/pipeline.tf`.**

`ExtractAndThreshold` has always passed `"reading.$": "$.tier0.reading.reading"` — that
JSONPath *is* the declaration of where tier 0's pointer lives, because `ReadAtTierZero` stores
its whole payload under `$.tier0.reading`. `escalate` read `$.tier0.reading`, found a payload
with no `bucket`, and called S3 with the empty string: `ParamValidationError: Invalid bucket
name ""`, on an estate where every other part of the escalation was correct.

A test that hard-coded the nesting would have encoded whichever reading its author had, which is
how the mistake was made in the first place. So the event here is *built from the JSONPath in
the Terraform*: if somebody rewires the machine, this fails until the handler agrees with it, and
if somebody rewrites the handler, it fails until the machine does.

What it does not test is that the object at that path is a valid reading — `_reading` does that,
against the real constructors. This is only about the address.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from manifest.handlers import escalate

PIPELINE = Path(__file__).resolve().parents[2] / "infra/extraction/pipeline.tf"

POINTER = {"bucket": "manifest-records-000000000000", "key": "readings/tesseract-5.5.0/SHP1.json"}


def _declared_path() -> list[str]:
    """The JSONPath the machine uses for the tier-0 reading pointer, as its segments.

    Nested paths only. `ReadAtTierZero` also spells `"reading.$": "$.Payload"` — that is its
    `ResultSelector`, naming the thing being *stored*, not where a later state finds it. Matching
    the first `reading.$` in the file finds that one, which is how this test first read the
    machine as putting the pointer at `$.Payload` and reported the handler wrong for agreeing
    with the other spelling.
    """
    text = PIPELINE.read_text(encoding="utf-8")
    found = re.search(r'"reading\.\$"\s*:\s*"\$\.(\w+(?:\.\w+)+)"', text)
    assert found, (
        "infra/extraction/pipeline.tf no longer passes the tier-0 reading by a `$.`-path this "
        "test can read. The path is the contract between the machine and the handler; if it has "
        "moved, this test must be taught the new spelling rather than deleted"
    )
    return found.group(1).split(".")


def _event_shaped_by_the_machine(path: list[str]) -> dict[str, Any]:
    """`$` as the Escalate state receives it: the pointer nested at the declared path.

    Everything else `read_tier0` returns is included at the level above the pointer, because
    that is exactly what made the bug possible — the level above is also a dict, also plausible,
    and simply has no bucket.
    """
    node: Any = POINTER
    for segment in reversed(path[1:]):
        node = {segment: node, "document_id": "SHP1", "pages_written": 1}
    return {path[0]: node}


def test_the_handler_finds_the_pointer_where_the_machine_puts_it() -> None:
    path = _declared_path()
    event = _event_shaped_by_the_machine(path)
    assert escalate.reading_pointer(event) == POINTER, (
        f"the machine passes the reading pointer at $.{'.'.join(path)} and the handler resolved "
        f"something else. One level short is a dict with no bucket, and boto3 reports it as an "
        f"invalid bucket name rather than as a missing pointer"
    )


def test_a_direct_invocation_still_resolves() -> None:
    """`publish`'s envelope, which is what a hand-run invocation looks like."""
    assert escalate.reading_pointer({"reading": POINTER}) == POINTER


def test_a_payload_that_carries_no_pointer_resolves_to_nothing() -> None:
    """Refused rather than passed on as empty strings.

    The empty bucket is the whole failure: `{}.get("bucket", "")` is a value, it reaches boto3,
    and the error names a bucket regex instead of a missing reading.
    """
    assert escalate.reading_pointer({"tier0": {"reading": {"document_id": "SHP1"}}}) == {}
    with pytest.raises(escalate.HandlerError, match="pointer"):
        escalate._reading({"tier0": {"reading": {"document_id": "SHP1"}}})
