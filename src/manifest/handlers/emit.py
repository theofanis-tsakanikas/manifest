"""Turning a span into something the runtime exports, and nothing more.

`manifest.observability.telemetry` builds spans as **data** and refuses the attributes that would
smuggle a counterparty's document text into a trace. It had no callers: the module was written,
tested, and emitted by nothing — the same "written and inert" shape this repository has now found
seven times, though a cheaper one than the others.

This is the thin adapter that closes it. It is separate from the handlers so that the decision
*what to record* stays in the pure module, where the forbidden-attribute check lives, and the
decision *how to ship it* stays here, where the runtime lives.

**Structured JSON on stdout, not an OTLP exporter.** The functions run in a managed runtime that
already ships stdout to a log group, and an exporter would add a dependency, a network path out
of a subnet that deliberately has none, and a failure mode where telemetry can break extraction.
A line of JSON is exported by the platform, is queryable, and cannot fail in a way that stops a
document being read. If a collector is wanted later, it reads the same lines.

**Emission never raises.** A span that could not be written is worth less than a document that
was: an observability failure that fails the extraction has inverted its own value. The one
exception is `TelemetryError`, which means the span carried something forbidden — that is a bug in
what was recorded, not a transport failure, and it is allowed to surface in tests. In the runtime
it is caught here, counted, and the document proceeds.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from manifest.observability.telemetry import Span, TelemetryError


def emit(span: Span) -> bool:
    """Write one span. Returns whether it was written; never raises in the runtime."""
    try:
        payload = {
            "manifest.span": span.name,
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent": span.parent,
            **span.attributes,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_plain))
    except TelemetryError:
        # The span carried something it should not. Recorded as a telemetry fault rather than
        # silently dropped, and it does not stop the document.
        print(
            json.dumps({"manifest.span": "telemetry.refused", "reason": "forbidden attribute"}),
            file=sys.stderr,
        )
        return False
    except Exception:
        return False
    return True


def _plain(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return repr(value)
