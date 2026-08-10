"""The code that runs in the estate.

Everything here is an **adapter**: it may import boto3, it may read the environment, it may
touch the network. `manifest.core` may do none of those, and `manifest.gates.core_purity`
enforces the boundary — which is what makes the seven claims checkable on a laptop while the
same functions decide what publishes in the estate.

Three handlers, and the division between them is the pipeline's whole shape:

- **`read_tier0`** — rasterise, then read with the local reference reader. The only handler
  that needs the OCR binary, so the only one that ships as a container image.
- **`publish`** — extract fields, apply the derived thresholds, publish or queue. This is the
  project's own logic and it runs on plain Python.
- **`provenance_gate`** — check each publishable field's box against the page. It needs the
  raster a second time, so it shares `read_tier0`'s image.

**The order is not negotiable and the state machine cannot express it any other way.** The
provenance gate sits between deciding to publish and publishing, so that "a published field
that cannot be located on a page is a build failure" is a property of the pipeline rather than
of a report somebody reads afterwards.

**A handler never decides.** Each one calls into `core` or `gates` and returns what it was
told. A threshold comparison written here, or an `except` that lets a refusal through, would be
the whole argument of this repository undone in a file nobody re-reads.
"""
