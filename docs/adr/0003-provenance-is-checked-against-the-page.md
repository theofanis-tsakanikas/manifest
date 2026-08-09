# ADR-0003 — Provenance is checked against the page, and the check names what it cannot catch

**Status:** accepted · **Date:** 2026-08-09

## Context

Watermark nearly shipped a parity harness that compared one function with itself. It would have
reported green forever. `PLAN.md` names claim 2 as the same trap in a different costume, and the
first draft of the fix walked into it: *"the gate re-reads the crop at the recorded coordinates
and checks the value is there."*

Re-reading a crop with the same engine in the same configuration is a deterministic function
replayed on a subset of its own input. It returns the same token by construction. It is not
worthless — it catches a box pointing at the wrong place, the wrong page, a coordinate-system
error, a rounding error, a post-processing transformation — but it cannot catch a confident
misread, which is the failure the claim *sounds* like it is about.

So this ADR does two things. It defines independence in layers of declared and unequal strength.
And it states, in advance and on the face of the README, **what the gate does not catch** —
because a gate that names its residual is worth more than one that implies it has none.

**The claim, stated exactly:** claim 2 is that a published field is **where the record says it
is**. It is not that the value is **right**. Correctness is claim 1's business — the derived
threshold — and the human loop's. Conflating the two is how a locational check gets sold as a
correctness check, and it is the single most likely way this project could mislead a reader.

## Decision

### Three layers, in increasing dependence on a reader

**Layer A — ink is present where the record says a value is. No reader at all.**

Crop the page at the recorded box, padded (`Box.padded`, ADR-0004's crop rule). From the pixels
alone: binarise, and compute foreground coverage, connected-component count, and the ratio of
the ink's own hull to the recorded box.

Three refusals, none of which involves recognising anything:

- the crop is **blank** — coverage below a declared floor
- the ink's hull **does not fill** the recorded box — a hull taken over the wrong span of words
- the crop is **saturated** — coverage above a declared ceiling, which is a box on a stamp, a
  rule line or a black border rather than on text

This layer is independent of every recognition engine that exists. It is also the weakest in
what it can distinguish: it says *something is there*, not *what*.

**Layer B — the crop, re-read through a different recognition path.**

Re-read the padded crop with the engine in **single-unit mode** — a crop with no page, no layout
analysis, no line finding, no neighbours. The full-page pass that produced the value ran page
segmentation and resolved the word in the context of its line and block; this pass does none of
that. Two genuinely different code paths inside one binary, and a disagreement between them is
possible, which is what makes agreement mean something.

Declared honestly, and repeated in the README: **this is independence from the segmentation and
layout path, not from the character classifier.** A `0`/`O` confusion made by the shared
classifier reproduces on both passes. What Layer B adds over Layer A is real and bounded:

- a box pointing at a *neighbouring* row or column, where ink is present and the value differs
- a **post-processing transformation** that changed the value between the extracted block and
  the published field — a strip, a normalisation, a currency reinterpretation. This is the
  common real defect and nothing else in the system looks for it

**Layer C — arithmetic. Absolute, where it applies.**

A field that can check itself is checked against itself, and a failure is proof of a wrong read
independent of every layer above. Two apply here: the ISO 6346 container check digit, and a
document total against the sum of its lines.

The container digit is mod-11: **a failure proves the read is wrong; a pass proves nothing**,
and roughly one corruption in eleven passes (`docs/SCENARIO.md`). It refuses values; it never
confirms them, and it is never used as a label.

Layer C covers a handful of fields. That is stated rather than obscured by putting it first.

### What the gate does not catch, decided in advance

| Not caught | Why | Where it is caught instead |
|---|---|---|
| A box on an **identical string elsewhere on the same page** — `ROTTERDAM` as port of loading, `ROTTERDAM` as port of discharge | Every layer passes. The value genuinely is at those coordinates | Nowhere in claim 2. It is a field-assignment defect, and it belongs to extraction and to the human shown the crop |
| A **confident misread the classifier reproduces** | Layer B shares the classifier | Claim 1's threshold, and the review queue |
| A value **correct on the page and wrong in the world** — a supplier's own typo | Not a property of this system | Claim 4, cross-document reconciliation |

The first of these gets a **fixture whose expected result is "not caught"**, committed with the
others. A limitation that is measured is a limitation; a limitation that is only written down is
a hope. If a future change makes it catchable, that fixture is what will notice.

### The fixture family: four ways a box is wrong, and they fail differently

Committed in Phase 2 with the gate, because "the recorded box is deliberately wrong" is not one
test:

1. **Box on whitespace** — margin or gutter. Layer A refuses. The easy one, and the one a naive
   implementation passes by accident because there is nothing there to re-read either.
2. **Box shifted by half a line** — ink present, wrong ink. Layer A passes; **Layer B refuses**.
   This is the case that decides whether Layer B was worth building.
3. **Box on the right value on the wrong page** — the coordinates are perfect, the page index is
   off by one. Both layers refuse *if and only if* the verifier resolves the page from the
   record rather than from the caller. A test asserts the resolution path.
4. **Box on an identical string elsewhere** — expected **not** caught. See above.

### Independence is enforced by a gate, not intended

`scripts/check_provenance_paths_are_independent.py`, arriving in Phase 2 with the verifier: the
verification module may import the normalised representation, `core/geometry.py` and the raster
adapter, and **may not import the field-assembly module that produced the record**. The check
reads the import graph.

It ships with its mutation in the same commit — make the verifier call the extractor's own
field assembler and require the named gate to refuse it. Without that gate, "independent" is a
sentence in a document, and the first refactor that notices the duplication deletes the claim.

### What "document version" means, so it cannot become a timestamp

A published field's provenance is `(document version, page, box)`. The document version is
**derived from content**: a digest over the source bytes, the engine version and the contract
version. Never a clock, never a counter — `core/` cannot read a clock at all, which is what
makes claim 3's "same document, same version, identical record" mechanical rather than a matter
of remembering.

### DOCX is out of scope

`docs/AWS-CONSTRAINTS.md`: Bedrock Data Automation converts DOCX to PDF internally and
documents that "page number mapping will not work for DOCX files". A document whose page number
is unreliable cannot carry provenance under this ADR. Stated here rather than discovered when a
record cannot be located.

## Consequences

- Layers A and B need the raster. The **core stays pure**: it computes crop rectangles, compares
  values and decides refusals; an adapter opens the image and hands back pixel statistics. The
  boundary is `core/geometry.py` on one side and an image adapter on the other.
- Layer A's floors and ceilings are declared data, tuned against the corpus envelope, not
  constants in code. A coverage floor chosen to make a fixture pass is the magic number again.
- Layer B costs a second engine pass per published field. On 55,000 pages a day that is a real
  cost and it goes in the cost model as its own line — a control that is quietly expensive gets
  disabled on a busy afternoon.
- Value comparison needs a declared normalisation per field type (whitespace, case, thousands
  separators, currency symbols). That normalisation is itself a place to hide a disagreement, so
  it is data in the contract, it is the *same* normalisation the extraction path uses, and the
  fact that it is shared is stated — it is the one thing the two paths have in common.
- The README carries the residual table above, not a footnote pointing at it.

## Sources consulted, 2026-08-09

- [Textract BoundingBox](https://docs.aws.amazon.com/textract/latest/dg/API_BoundingBox.html) —
  coordinates are ratios of the page with a top-left origin, matching the normalised
  representation.
- [BDA prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html) —
  DOCX page-number mapping does not work.
- `docs/SCENARIO.md` — ISO 6346 as a falsifier rather than ground truth.
