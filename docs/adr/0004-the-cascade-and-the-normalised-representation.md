# ADR-0004 — The cascade routes between engines with different competences

**Status:** accepted · **Date:** 2026-08-09 · **Documentation verified:** 2026-08-09

## Context

Decision 9 said: cheap engine first, escalate where confidence is low, and the saving is a
measured number. Decision 14 — nothing is ever applied to AWS — made the second half impossible,
and decision 9 has been rewritten. This ADR settles what the cascade *is* and what its eval is
able to prove.

Then `docs/AWS-CONSTRAINTS.md` changed the design. Verified 2026-08-09: **Amazon Textract,
Bedrock Data Automation and Amazon Comprehend all support the same six input languages —
English, German, Spanish, French, Italian, Portuguese.** The scenario's documents are in
**English, Greek and Dutch**. Two of the three languages, including the language of the Piraeus
office, are outside every managed extraction service in the intended stack.

That is not a cost problem. It is a coverage problem, and it means the cascade is not a quality
ladder — it is routing between engines that are *good at different things*.

## Decision

### The tiers

| Tier | Engine | Competence | Cost |
|---|---|---|---|
| **0** | The local reference engine (ADR-0005) | Every language it has data for, including Greek and Dutch. Real confidence, real geometry | Zero, and it actually runs |
| **1** | Managed OCR / document extraction | The six documented languages. Better on degraded print, tables and forms | Per page |
| **2** | A multilingual model with document input | Every language. The **only** escalation path for Greek and Dutch | Per token |

### Escalation is language-aware, and it is declared

`contracts/cascade/routing.yaml` declares, per language, which tiers are **eligible**. A page in
a language no higher tier supports has no escalation path, and the correct outcome is an
**abstention to the review queue, not an attempt**.

This is the rule that matters. Sending a Greek page to a service that does not read Greek does
not fail loudly — it returns a confident-looking result over a language the model was not
trained on, and that result then carries a confidence score into a threshold derived on the
assumption that the score means something. Failing to route is a bug; routing to an ineligible
engine is a fabricated result.

### Escalate on confidence, never on a preprocessing rejection

Textract and BDA share their preprocessing limits: 15-pixel minimum character height, 10,000-pixel
resolution ceiling, no vertical text, the same in-plane rotation support (verified 2026-08-09). A
page rejected by one for any of those reasons is rejected by the other for the same reason.
Escalation on a preprocessing failure is therefore a second bill for the same answer. The trigger
is the derived confidence threshold (ADR-0002) and nothing else.

A corollary worth writing down: **skew alone is not a reason to escalate.** Both services
document full support for in-plane rotation, up to 45°. A corpus whose degradation is mostly skew
would produce a cascade that never escalates and a claim that never bites.

### What the cascade eval proves, and where it stops

| Provable offline | Not available, and labelled so |
|---|---|
| The routing rule escalates the pages whose tier-0 confidence is below the derived threshold | What tier 1 or 2 would have read on those pages |
| The pages **kept** at tier 0 meet their fields' declared error budgets | Whether escalation actually repairs a field |
| The routing **distribution** over the corpus — what fraction lands in each tier, by language | Any end-to-end accuracy figure for the cascade |
| A cost **model**: that distribution × published, cited, dated unit prices | Any euro figure as a measurement |

The value of the escalated fraction is an **assumption**. It is named as one, and the cost model
shows its sensitivity to it rather than burying it: the README carries the cost under a range of
assumed escalation yields, not a single number derived from the most flattering one.

No sentence of the form *"accuracy held at X for Y% of the cost"* may appear in this repository.

### The normalised representation is the contract with the cloud

Every engine produces the same internal shape: pages, blocks, text, confidence, geometry. The
core never learns which engine produced a value, and `manifest.gates.core_purity` fails if an
engine is named anywhere in `core/` — in an import, a dictionary key or a comment.

Two decisions inside the shape, both settled by the documentation:

**Geometry is fractions of the page, top-left origin.** Textract's `BoundingBox` is documented as
"ratios of the overall document page size" with the origin at the upper-left, which is what
`core/geometry.py` had already chosen. So the managed adapters *rename*, and the **tier-0 adapter
divides** — a per-word local reader reports pixels. `Box.from_pixels` is the only place in the
codebase where a pixel measurement may become a stored coordinate.

**Confidence is carried as the engine emitted it, on a declared scale, and is never rescaled to
look comparable.** Two engines' 0.8 are not the same event. The representation records the value
and the engine's scale; the *comparison* happens only after calibration, per engine, against the
labelled set (ADR-0002). An adapter that normalised confidences into a common range would be
inventing the very thing claim 1 exists to derive.

What is deliberately **not** in the representation today: Textract's fine-grained `Polygon` and
its per-word `RotationAngle`. Adding them later is additive. Adding them now would be fields with
one producer, which is how a "normalised" representation quietly becomes one engine's schema.

### The AWS adapters are written, schema-tested, and never called

Their response mapping is tested against the **documented** response schema. Their fixtures are
**authored from that schema and labelled as authored**, in the file, in the test name and in the
README. They are never described as captured responses, because none was captured.

## Consequences

- The routing contract needs a language per page, so language detection is part of tier 0's
  output and is itself a value with a confidence — and a page whose language is uncertain routes
  by the conservative rule, not the likely one.
- Claim 7's cost model gains a dimension: cost by language, because the Greek and Dutch fraction
  can only escalate to the per-token tier. That is a more interesting cost model than a per-page
  one and it falls out of the constraint rather than being invented.
- Tier 0 must have language data for Greek and Dutch installed, which is an environment
  requirement recorded in ADR-0005 and in the corpus's build instructions.
- The corpus must contain Greek and Dutch documents in a realistic proportion, or the finding
  above is true and unexercised.
- The escalation cost of a control is now visible: ADR-0003's Layer B is a second tier-0 pass per
  published field, and it appears in the cost model as its own line.

## Sources consulted, 2026-08-09

- [Textract set quotas](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) —
  six input languages; 15-pixel character floor; 10,000-pixel resolution ceiling; in-plane
  rotation; no vertical text; sync limited to one page for PDF and TIFF.
- [Textract BoundingBox](https://docs.aws.amazon.com/textract/latest/dg/API_BoundingBox.html) —
  ratios of the page, top-left origin.
- [BDA prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html) —
  the same six languages and the same preprocessing limits.
- [Comprehend supported languages](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html)
  — no Greek, no Dutch; `zh` and `ar` supported for entity detection, which is where it is used.
