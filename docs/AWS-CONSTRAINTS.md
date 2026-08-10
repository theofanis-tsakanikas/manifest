# AWS service constraints, verified

**Verified against current AWS documentation on 2026-08-09.** Every figure below was read from
the page cited beside it on that date. Nothing here was measured — no call has been made to any
of these services from this repository, and none will be (`docs/DECISIONS.md` 14).

**The rule for this file:** a constraint goes in only with its source page and the date it was
read. A number that "sounds right" is deleted. When a figure decides a design, the decision is
named in the last column so a reader can find where it landed.

Watermark's lesson, carried over: *verify service constraints early.* Two of the findings below
would have arrived in Phase 2 otherwise, and one of them changes the shape of the cascade.

---

## The finding that changes the design: the managed stack does not speak two of the three languages

`docs/SCENARIO.md` puts Northbridge's documents in **English, Greek and Dutch**, with party
names additionally in Chinese and Arabic script. Read against the documentation:

| Service | Documented input languages | Greek | Dutch |
|---|---|---|---|
| **Amazon Textract** | English, French, German, Italian, Portuguese, Spanish | ✗ | ✗ |
| **Bedrock Data Automation** (documents) | English, German, Spanish, French, Italian, Portuguese | ✗ | ✗ |
| **Amazon Comprehend** (entities, key phrases) | de, en, es, it, pt, fr, ja, ko, hi, ar, zh, zh-TW | ✗ | ✗ |
| **Comprehend PII detection** | English and Spanish only | ✗ | ✗ |

Sources, all read 2026-08-09:
[Textract set quotas](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) ·
[BDA prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html) ·
[Comprehend supported languages](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html)

**Two of the three document languages in this scenario are outside every managed extraction
service in the intended stack**, and Greek is one of them — the language of one of the two
offices.

Three consequences, and none of them is a workaround:

1. **The local tier-0 engine stops being the cheap option and becomes the only option for those
   pages.** Decision 16 was argued on cost and on provability. It turns out to be load-bearing
   for coverage as well, which is a stronger argument than the one that was written down.
2. **Escalation must be language-aware, declared in a contract, not decided in code.** Sending a
   Greek page to a service that does not read Greek does not fail loudly — it returns a
   confident-looking result over a language the model was not trained on. The routing contract
   therefore declares, per language, which tiers are *eligible*, and a tier with no eligible
   engine for a language is an abstention rather than an attempt. ADR-0004.
3. **The escalation path for Greek and Dutch can only be a multilingual model on Bedrock**, not
   Textract and not BDA. That is a genuine architectural difference between tiers, and it means
   the cascade is not "cheap OCR, then better OCR" — it is a routing decision over engines with
   *different competences*, which is a more interesting problem and a more honest one.

Also documented, and relevant to a corpus that contains CJK party names:

- **Neither Textract nor BDA supports vertical text alignment** (the layout common in Japanese
  and Chinese). Horizontal CJK is a separate question from vertical CJK, and neither service
  lists Chinese as an input language for documents at all.
- **Comprehend does support `zh` and `ar` for entity detection**, which would have been the
  part of the scenario it was needed for — party names, not page text. **It was dropped
  anyway, 2026-08-10**, by decision 6's rule: entity recognition here has exactly one right
  answer per rule, so it is deterministic code in `core/entities.py`, and a service that reads
  neither Greek nor Dutch cannot serve the two languages the escalation tier exists for. A
  service in a header that cannot be pointed at its work is a CV keyword.

---

## Amazon Textract

Source: [Set quotas in Amazon Textract](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html),
read 2026-08-09. These are *set* quotas — they cannot be raised.

| Constraint | Documented value | Where it lands |
|---|---|---|
| Formats | JPEG, PNG, PDF, TIFF. JPEG 2000 inside PDF supported. **XFA-based PDFs not supported** | Corpus renders to PDF and PNG; XFA never appears |
| **Synchronous** | 10 MB in memory; **PDF and TIFF limited to 1 page** | A multi-page bill of lading cannot be read synchronously. The page-break table case is asynchronous by necessity |
| **Asynchronous** | JPEG/PNG 10 MB; PDF/TIFF **500 MB and 3,000 pages** | Comfortably above anything in this scenario |
| Image resolution | ≤ **10,000 pixels on all sides** | Caps the render DPI: an A4 page at 1200 DPI is 11,700 px and would be refused |
| **Minimum character height** | **15 pixels** — "at 150 DPI, this would be the same as 8 point font" | **Decides the corpus render DPI.** A degraded 6 pt footnote at 150 DPI is below the documented floor, and an abstention there is the service working, not failing |
| PDF page size | Max 40 inches and 9,000 points; no password-protected PDFs | Generator constraint |
| Rotation | All in-plane rotations supported, including 45° | The corpus's skew pathology is within scope — which means skew alone is *not* a reason to abstain, and the corpus must degrade harder than skew to produce real low-confidence pages |
| Handwriting | Supported, **English only** | The handwritten-correction pathology produces abstentions in Greek and Dutch by construction. Say so; do not present it as a capability |
| Queries | 15 per page synchronous, 30 asynchronous | Caps a query-based extraction strategy; a six-document contract set stays under it |
| Vertical text | **Not supported** | See above |

### Geometry — the normalised representation was the right call

Source: [BoundingBox](https://docs.aws.amazon.com/textract/latest/dg/API_BoundingBox.html) and
[Geometry](https://docs.aws.amazon.com/textract/latest/dg/API_Geometry.html), read 2026-08-09.

> "The `top` and `left` values returned are **ratios of the overall document page size**. […]
> the upper-left corner of the image is the origin (0,0)."

`src/manifest/core/geometry.py` had already chosen fractions of the page with a top-left origin,
for reasons internal to this repository — a stored pixel box is a fact about whichever raster
somebody happened to produce. The managed service uses the same convention, so the adapter for
it is a rename rather than a conversion, and **the tier-0 adapter is the one that has to divide**
(a per-word local reader reports pixels). `Box.from_pixels` exists for exactly that, and is the
only place in the codebase where a pixel measurement may become a stored coordinate.

`Geometry` also carries a fine-grained `Polygon` and a `RotationAngle` of 0/90/180/270 per word.
Neither is in the normalised representation today. Adding the polygon later is additive; adding
it now would be a field with one producer, which is how a "normalised" representation quietly
becomes one engine's schema.

---

## Bedrock Data Automation

Source: [Prerequisites for using BDA](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html),
read 2026-08-09.

| Constraint | Synchronous | Asynchronous |
|---|---|---|
| Pages per document | 10 | 20, or **3,000 with the splitter enabled** |
| File size | 50 MB | 500 MB (200 MB via console) |
| Formats | PDF, TIFF, JPEG, PNG | the same, plus DOCX |
| Languages | English, German, Spanish, French, Italian, Portuguese | same |

Same 15-pixel character floor, same 10,000-pixel resolution ceiling, same absence of vertical
text, same in-plane rotation support as Textract — which is itself worth recording: the two
services share their document-preprocessing constraints, so a page that is too degraded for one
is too degraded for the other, and **escalating from Textract to BDA on a resolution or
character-size failure buys nothing.** The escalation rule must therefore escalate on
*confidence*, never on a preprocessing rejection.

**No confidence, anywhere in the standard output.** Verified against the published
[standard output for documents](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-output-documents.html),
read 2026-08-10. The documented word entity is `{id, text, line_id, reading_order, page_index,
locations{page_index, bounding_box}}` — no score on the word, none on the line, none on the
element, none on the page.

This is the most consequential single fact in this file, and it was found late. It means:

| Consequence | Where it is handled |
|---|---|
| Nothing this service reads can publish on a score, because it has no score | `core/review.py` `Reason.UNSCORED` |
| It cannot contribute to claim 1 — a threshold needs a distribution of scores | `evals/calibration/` never sees it |
| Escalating to it is a decision to spend a human | `contracts/cascade/routing.yaml`, stated on the tier |
| The representation must carry *absence of a score* as its own state | `Word.confidence: float \| None` |

The alternative — mapping its words at 1.0 — would clear every derived threshold in this
repository, silently, on every page. Doctrine rule 3, in its most expensive form.

**Word-level granularity is off by default.** Default output reports lines; `text_words`
appears only when word granularity is requested. Splitting a line into words locally would
invent a box per word, and an invented box is a provenance record pointing at something nobody
measured, so the adapter refuses the response instead.

**Two things it gives that the per-page OCR service does not:**
`pages[].asset_metadata.rectified_image_width_pixels` / `..._height_pixels`, so the raster size
arrives in the response rather than being passed in by a caller who could pass a wrong one; and
`detected_page_number`, the number printed on the page, which is what a reviewer means by
"page 3".

DOCX is converted to PDF internally and "**page number mapping will not work for DOCX files**".
A document with no reliable page number cannot carry provenance under claim 2, so **DOCX is out
of scope for this system**, stated here rather than discovered later.

Blueprint limits that bound the contract layer: 100 leaf fields, 30 list leaf fields, 60-character
field names, 600-character field descriptions, 40 blueprints per project. The six document
contracts sit well inside these; a contract that grew past 100 fields would be a data-minimisation
problem before it was a service-limit problem.

---

## The model tier — Bedrock `Converse`

Source: [Converse API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html),
read 2026-08-10.

**A model reports no confidence either, and any it is asked for must be refused.** A model will
return `{"value": ..., "confidence": 0.97}` if a prompt asks for it. That number is not a
measured frequency over a labelled distribution; it is a token the prompt made likely, and it
would enter claim 1's derivation looking identical to a score that means something. The adapter
raises on it rather than ignoring it, so that a future prompt change fails at the moment it is
made instead of silently doing nothing.

**A model's coordinates are refused for the same reason.** Asked for a box, it returns a
plausible one. Provenance therefore comes from the tier-0 reading's measured geometry: the
proposed value is located among words whose boxes came from a reader that looked at pixels, and
a value that cannot be located gets no provenance and cannot publish — doctrine rule 7.

Two operational consequences recorded here rather than discovered later:

- `stopReason: "max_tokens"` means the reply is a *prefix*. JSON truncated after a complete
  field is still valid JSON with fewer fields, so it is refused on the flag, never on the parse.
- **Greek loses its accents in upper case.** A page printing `ΠΕΙΡΑΙΑΣ` and a model returning
  `Πειραιάς` are the same port, and case folding alone does not reconcile them —
  `ΠΕΙΡΑΙΑΣ`.casefold() is `πειραιασ` while `Πειραιάς`.casefold() keeps its `ά`. Locating
  therefore applies `Rule.DIACRITICS` as well as `Rule.CASE`. Without both, a correct reading
  arrives with no provenance and is queued: a right answer converted into review volume.

---

## Human review — Amazon A2I

`PLAN.md` said: *do not assume it exists; if it does not, the review queue is a small application
of our own, which is fine and possibly better.* It was right to say so.

Source: [AWS service availability updates](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-service-availability/),
read 2026-08-09. **Amazon Augmented AI (A2I) is listed among the SageMaker AI features moving to
maintenance, closing to new customers on 2026-07-30.** Existing customers continue; new ones
cannot start, and no new features are planned. Also on that list, and worth noting because two
of them appear in sibling projects: SageMaker Ground Truth, Clarify, Model Monitor, Debugger.

**So the review queue is ours.** Northbridge is a new customer in 2026; the service is not
available to it.

This is the better outcome for claim 5 and it should be said plainly rather than framed as
making the best of things. Claim 5 is not "documents reach a human" — it is that **the queue has
a declared finite capacity, that exceeding it fails the build, and that reviewer integrity is
measured**. Those are properties of a queue's *design*, and they are exactly what a managed
labelling service does not give you: it gives you a worker pool and a task UI. Building the queue
means the capacity model, the integrity metrics and the decision record are all in
`src/manifest/review/`, offline, testable, and attackable by `gate-proof` — which is the only
form in which claim 5 could have been proved anyway.

What is lost: a hosted worker UI, and the Ground Truth private-workforce plumbing. Neither was
ever going to be exercised in a repository that does not deploy.

---

## EMR Serverless

Source: [Understanding application behavior in EMR Serverless](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/app-behavior.html),
read 2026-08-09.

| Constraint | Documented value |
|---|---|
| Worker vCPU | 1, 2, 4, 8, 16 or 32 |
| Worker memory | 1 vCPU: 2–8 GB · 2 vCPU: 4–16 GB · 4 vCPU: 8–30 GB · 8 vCPU: 16–60 GB · 16 vCPU: 32–120 GB · 32 vCPU: exactly 60, 120 or 244 GB |
| Ephemeral storage | 20–200 GB per worker; **only storage above 20 GB is charged** |
| Auto-stop | Default **15 minutes** idle, then pre-initialised capacity is released |
| Spark memory overhead | 10% of container memory, minimum 384 MB — and a 32-vCPU job whose total falls outside 8 GB of a supported size is **rejected**, not resized |

The 32-vCPU rejection rule is the one worth writing down: it is a job that fails at submission
for a reason that reads like a configuration typo. Worker sizing therefore belongs in the batch
contract as declared data, not as Spark configuration scattered through a job script.

**The reprocessing claim does not rest on any of this.** Claim 7 is proved against the pure
planner and its ledger on a laptop (`CLAUDE.md`, claim 7); EMR Serverless is the adapter that
would execute a plan the planner produced. That split is what makes the claim checkable, and it
is also what makes these numbers a sizing input rather than a dependency.

---

## Redshift Serverless

Source: [Compute capacity for Amazon Redshift Serverless](https://docs.aws.amazon.com/redshift/latest/mgmt/serverless-capacity.html),
read 2026-08-09.

| Constraint | Documented value |
|---|---|
| RPU | 1 RPU = 16 GB memory |
| Base capacity | **Default 128 RPUs.** Settable to 4, then in units of 8 from 8 to 512 |
| **4-RPU availability** | Only in: US East (Ohio, N. Virginia), US West (N. California, Oregon), AP (Mumbai, Singapore, Sydney, Tokyo), **EU (Ireland), EU (Stockholm)** |
| 4-RPU limits | ≤ 32 TB managed storage, ≤ 100 columns per table recommended, 64 GB memory. **Once scaled above 4 RPUs it will not scale back down to 4** |
| Scaling steps | 4→8 in 4s; 8→512 in 8s; 512→1024 in 32s |

**Two findings that decide a design.**

First: **the default base capacity is 128 RPUs**, which is thirty-two times the minimum. A
workgroup created without setting `base_capacity` is a workgroup provisioned for a workload
nothing in this scenario has. The Terraform sets it explicitly, and a variable with no default
would be better than a default nobody read.

Second: **`eu-central-1` (Frankfurt) is not in the 4-RPU list.** The bootstrap layer's default
region is `eu-central-1`, so the minimum base capacity available to this estate as configured is
**8 RPUs, not 4**. Either the analytics layer moves to `eu-west-1` (Ireland), or the design
states 8 RPUs as its floor. This is exactly the class of fact that is discovered during a deploy
otherwise, and it is decided in Phase 4 when `infra/analytics/` is written — recorded here so
that it is decided rather than defaulted.

Also noted, because it appears in a sibling project's stack: **Redshift Python UDFs reach end of
support after 2026-06-30**. Nothing here plans to use one; recorded so nobody adds one.

---

## OpenSearch Serverless

Source: [Managing capacity limits for OpenSearch Serverless](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-scaling.html),
read 2026-08-09.

| Constraint | Documented value |
|---|---|
| OCU | 6 GiB memory plus corresponding vCPU, and S3 data transfer |
| **Minimum, redundancy enabled** | 1 OCU indexing (0.5 × 2) **+** 1 OCU search (0.5 × 2) |
| Minimum, redundancy disabled | 0.5 OCU indexing + 0.5 OCU search — development and testing only |
| **These OCUs always exist** | "even when there's no indexing or search activity" |
| Account default ceiling | 10 OCUs indexing, 10 search; configurable in multiples of 2 |
| Hot storage | 120 GiB of index data per OCU |

**The floor is always-on, and there is no zero.** A collection that is never queried still costs
its minimum OCUs, and the capacity setting is **account-level, not per-collection** — so a second
collection cannot be capped independently of the first.

`docs/DECISIONS.md` 5 already says a document-search surface is not a claim and is cut if it
costs time. This constraint sharpens that: it is not merely not-a-claim, it is the only component
in the estate with a standing cost that no usage pattern reduces to nothing. If it is built, it
is built last, it is capped at the account level, and its cost line in the model is a constant
rather than a per-page figure.

---

## What this file deliberately does not record: prices

No unit price appears above. `CLAUDE.md` requires every price to be cited *and dated at the time
it was read*, and the cost model is built in Phase 2 and completed in Phase 4. A price read today
and used in six weeks is a stale number carrying a date that makes it look fresh — which is worse
than no number, because it is checkable and wrong.

The pricing pages to read, and to date on the day they are read, are recorded here so that the
list is not reassembled from memory:

- Amazon Textract pricing — per 1,000 pages, per API and feature
- Bedrock Data Automation pricing — per page, by output type
- Amazon Bedrock pricing — per input and output token, by model
- Amazon Comprehend pricing — per unit of 100 characters
- EMR Serverless pricing — vCPU-hour, memory GB-hour, storage GB-hour above 20 GB **(the billing
  shape above is stated from the sizing documentation; the billing granularity and any minimum
  duration are NOT verified and must be read before any figure uses them)**
- Redshift Serverless pricing — per RPU-hour
- OpenSearch Serverless pricing — per OCU-hour

---

## The design decisions this file settles

| Finding | Decision | Where |
|---|---|---|
| Greek and Dutch unsupported by Textract, BDA and Comprehend | Tier 0 is the only engine for those pages; escalation is language-aware and declared in a contract; the escalation tier for them is a multilingual model | ADR-0004 |
| Bounding boxes are ratios of the page, origin top-left | Already the normalised representation; the tier-0 adapter divides, the managed adapters rename | ADR-0004, `core/geometry.py` |
| 15-pixel minimum character height at both services | Fixes the corpus render DPI, and makes an abstention on a 6 pt degraded footnote correct rather than a defect | ADR-0002, Phase 1 |
| 10,000-pixel resolution ceiling | Caps the render DPI from above | Phase 1 |
| In-plane rotation fully supported | Skew alone is not a reason to abstain; the corpus must degrade harder than skew | `corpus/envelope.yaml`, Phase 1 |
| Textract and BDA share their preprocessing limits | Escalate on confidence, never on a preprocessing rejection | ADR-0004 |
| DOCX loses page-number mapping in BDA | DOCX is out of scope — it cannot carry provenance | ADR-0003 |
| A2I closed to new customers 2026-07-30 | The review queue is ours; claim 5 was never provable through a managed worker pool anyway | ADR-0001 |
| EMR Serverless rejects mis-sized 32-vCPU jobs | Worker sizing is declared data in the batch contract | Phase 4 |
| Redshift Serverless defaults to 128 RPUs; 4 RPUs unavailable in `eu-central-1` | Base capacity is always set explicitly; the floor for this estate is 8 RPUs unless the analytics layer moves to `eu-west-1` | Phase 4 |
| OpenSearch Serverless has an always-on 2-OCU floor, capped account-wide | The search surface is built last or not at all; its cost line is a constant | Decision 5 |
