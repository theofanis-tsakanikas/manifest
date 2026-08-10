# CLAUDE.md — Manifest

**A document intelligence platform for cross-border trade. Every field traces to a pixel.**

AWS-native: Textract · Bedrock Data Automation · Bedrock · human review · Lambda ·
Step Functions · EMR Serverless · Iceberg on S3 · OpenSearch · Redshift · SageMaker · Terraform

*Each of these has a job below. A service named here that cannot be pointed at the work it does
is a CV keyword, and decision 6 already says what to do with those: drop it rather than keep
it. **Comprehend was dropped by that rule** — its work is entity recognition, this system does
that in deterministic code because there is exactly one right answer, and it reads neither
Greek nor Dutch (`docs/AWS-CONSTRAINTS.md`), which are the two languages the escalation tier
exists for.*

> **Manifest** — the trade document, and the word for *made evident*. Both meanings are the
> project.

---

## Read this first, every session

| File | What it holds |
|---|---|
| `PLAN.md` | The four phases, task by task, with the definition of done for each |
| `docs/SCENARIO.md` | The domain — document types, volumes, the pathologies that must be handled |
| `docs/REGULATORY.md` | The legal posture. **The answer here is mostly "the AI Act does not apply", and that is the point** |
| `docs/DECISIONS.md` | Decisions locked before the first line of code, and why |
| `docs/PORTFOLIO-CONTEXT.md` | How this fits the site, the CV and the Readiness Framework |

**Language:** repository content in **English**. Conversation with the author in **Greek**.

---

## What this system does (the mental model)

A customs broker and freight forwarder receives commercial documents — bills of lading,
commercial invoices, packing lists, certificates of origin, customs declarations, arrival
notices. They arrive as scans of scans: skewed, stamped over the numbers, in three languages,
with tables that break across pages and handwritten corrections in the margin.

Three things come out of them:

| Output | The actual difficulty |
|---|---|
| **A structured record per document** | Extraction confidence. A field below its threshold does not get a default — it goes to a human |
| **Agreement across documents** | The invoice, the bill of lading and the packing list **must agree**. The disagreement is the signal, not the noise |
| **A tariff classification (HS code)** | Thousands of classes, genuinely ambiguous. A wrong classification is quantifiable financial and legal exposure |

And above all of it, the problem nobody demonstrates: **what happens when the extraction
model improves and four million documents have to be processed again.**

### The boundary this system is built around

| Models own | Deterministic code owns |
|---|---|
| Reading characters off a degraded scan | Whether a value is confident enough to publish |
| Proposing which field a token belongs to | Whether the value actually appears where its provenance says it does |
| Proposing a tariff classification | Whether two documents agree |
| Suggesting that two parties are the same entity | Whether a human's decision was recorded, and whether the human was actually looking |

**A published field that cannot be located on a page is a build failure.** That is the
project, in one sentence.

---

## The seven claims

Provable **in CI, on a laptop, with no AWS account and no credentials**.

| # | Claim | Proved by |
|---|---|---|
| **1** | **No field is published below its confidence threshold — and the threshold is *derived*, not chosen.** It is the **lowest score whose upper confidence bound on the published-and-wrong rate still fits the field's declared error budget**, computed against the labelled set with calibration measured (reliability curve, ECE) and **N printed beside every figure**. Where no threshold fits the budget at the available N, the field is declared **always-review** — never a 0.999 that means nothing. CI recomputes every threshold from a committed engine recording and fails when one moves outside its declared tolerance. Every figure is a statement about a distribution this repository authored, and says so. | `evals/calibration/` |
| **2** | **Every published field traces to a page, a bounding box and a document version — and the box is checked against the page, not against the record that produced it.** Three checks of **declared and unequal strength**: the crop carries ink where the record says a value is (independent of every reader); the crop re-read through a *different* recognition path agrees with the published value (independent of the path that produced it, not of the engine); a check-digit field that disagrees with its own arithmetic is refused outright. Re-reading a crop with the same engine in the same configuration is a deterministic function replayed on a subset of its input — it catches a wrong box and cannot catch a confident misread, and the README says which is which. | `src/manifest/gates/provenance.py` |
| **3** | **Re-extraction is reproducible and versioned.** Same document + same engine version = identical record. An engine upgrade produces a **new version with a diff**, never a silent overwrite, and the prior version stays retrievable. | `evals/reprocessing/` |
| **4** | **Cross-document disagreement is surfaced, never smoothed.** On a corpus with N planted mismatches, exactly N are found, with zero false positives on the set that agrees. The planting is the generator perturbing a value in a document, **blind to the reconciliation contract**; the expected findings are derived from ground truth by a separate path. A planter that reads the contract to decide what to break and a detector that reads the same contract to find it is one function agreeing with itself. The zero-false-positive figure is a statement about a set this repository authored. | `evals/reconciliation/` |
| **5** | **The human loop is real, and measured.** A classification below threshold cannot be published without a recorded human decision — *and* the review queue is a declared finite resource: if the threshold pushes volume past capacity, the **build fails**, and rubber-stamping is detected and reported. | `evals/review/` |
| **6** | **Entity resolution is reversible.** A merge of two parties can be un-merged with lineage intact and every downstream record correctly re-pointed. | `evals/entities/` |
| **7** | **Bulk reprocessing is idempotent, and its cost is modelled honestly.** Re-running a reprocessing job produces no duplicates and no double work — proved by the **pure planner and its ledger, running on a laptop** — which is where the proof belongs, because a claim that needs a cluster to check is a claim nobody can reproduce. The batch layer is an adapter over that planner, and the planner is what the adapter executes. Cost is a **model** until a bill exists: the routing distribution across cascade tiers is *measured* on the corpus, multiplied by *published* unit prices. It is labelled a model everywhere it appears, and it may only be called a measurement once a real run has produced one, with its date. | `evals/scale/` |

**Claim 1 is the one that separates this from a demo. Claim 5 is the one nobody builds.**

**And claim 1 is a loop, not a photograph.** A reviewer's correction is the highest-quality label
this system will ever see — a human looked at the page and said what it said — and
`src/manifest/core/feedback.py` turns it into an observation so that a field can *leave*
always-review as N grows. The rule that keeps it honest is one arrow that does not exist:
corrections move **N**, never an error budget. A budget that relaxed under queue pressure would
be ADR-0001's forbidden move wearing a feedback loop, and `gate-proof` plants exactly that.

An approval from a reviewer who agrees with everything is **not** evidence and is excluded and
counted, because admitting it lowers the observed error rate on evidence nobody produced by
looking at anything. That is doctrine rule 2 with a number attached.

`make gate-proof` breaks each gate on purpose and requires the *named* gate to refuse it, for
the right reason. Attestor's three rules apply: green first; a non-zero exit is not evidence;
a mutation whose target has moved is STALE, not passed.

---

## The doctrine

1. **Abstention is the safe state — and abstention is not free.** Sending a field to a human
   is correct. Sending 40% of fields to humans is a system that does not work, and the humans
   will start approving without looking. **The review queue has a declared capacity, and
   exceeding it is a failure of the system, not of the reviewers.**
2. **A human decision is only evidence if the human was looking.** Time on task, agreement
   rate with the model, and sampled re-review are measured. A reviewer whose agreement rate
   with the model is 100% is not a control; they are a rubber stamp with a login.
3. **A default is a lie with a plausible shape — and so is a threshold nothing supports.** No
   field is ever filled with a modal value, a zero, or "the usual". Missing is missing, and it
   is stated. The same rule binds the thresholds: a field with too few labelled examples to
   derive one that fits its error budget is declared **always-review**, with its N on the face
   of the report. A 0.999 written because 0.999 sounds safe is a default wearing a decimal
   point.
4. **A correction never erases what was previously published.** New version, diff, both
   retrievable — same rule as Watermark's restatement.
5. **Nothing approves itself.** No model, no pipeline, no service principal may raise a
   confidence threshold, clear a mismatch or approve a classification.
6. **Exceptions expire.** On expiry the finding returns and CI goes red.
7. **One door has no key: a field with no provenance cannot be overridden into existence.**
   If the system cannot point to where a value came from on the page, nobody — including an
   approver — has the information the approval would be about. Having exactly one unopenable
   door is what keeps the other six honest.

---

## Non-negotiable engineering rules

**Engine-free core.** Extraction post-processing, confidence handling, threshold derivation,
reconciliation, entity resolution and every gate live in `src/manifest/core/` as pure
functions over plain data structures, importing **no boto3, no Textract client, no model
SDK**. The AWS callers are thin adapters that produce a normalised document representation.
This is the only way claims 1–6 run on a laptop.

**A normalised document representation is the contract with the cloud.** Textract, Bedrock
Data Automation and any LLM extractor all produce the same internal shape — blocks with text,
confidence, page and geometry. The core never learns which engine produced a value. Swapping
engines must be an adapter change and nothing else, and there is a test that fails if an
engine name appears anywhere in `core/`.

**Nothing is applied to AWS yet — and "yet" is the whole of the word.** This system is built
to run on AWS. That is the point of it, and every service in the header above is there to do a
job rather than to be listed. What is deferred is the *timing* of the first apply, not the
apply: the estate is written, formatted, validated against real provider schemas and scanned
to zero findings, and it stays unapplied — including `infra/bootstrap/` — until the author
dispatches it deliberately. The `deploy` and `destroy` workflows are written and gated behind
a protected environment, and **have not been dispatched**.

Until that day, and only until then: no screenshot from a real console, no wall-clock figure,
no euro figure stated as measured. Every claim in this repository is scored offline, and stays
scored offline afterwards, because a claim that needs a running estate to check is a claim
nobody can reproduce.

**An earlier revision of this file said "nothing is *ever* applied" and treated that as the
posture.** It was a misreading of a sequencing instruction as a permanent stance, and it cost
the project real components: adapters that were never written because they were never going to
be called, and a pipeline with no compute in it because no compute was going to run. The rule
is *not yet*. Anything written on the assumption of *never* is a defect.

**Offline is the default, and there is a real engine behind it.** This is the rule that saves
the project. Claims 1 and 2 need *actual confidence scores on actually degraded pages* —
invented ones would be fabricated results, and waiting for an AWS account to get real ones
would make every claim unreproducible by anybody else. So the cascade's bottom tier is a
**local open-source OCR engine**: free, deterministic, and running on the genuine corpus, on a
laptop and in the deployed estate alike, from the same container image.

This is not a compromise. It is the strongest available proof that the normalised
representation is real: if a local engine and a managed service are interchangeable behind it,
the abstraction holds. Adapters for the AWS engines are written and their response mapping is
tested against the **documented** schema; the fixtures are authored from that schema and
marked as authored. When the estate is applied, a recorded real response may replace an
authored fixture — and the fixture's provenance changes with it, in the same commit. What is
forbidden is an authored fixture *presented* as captured, in either direction.

**The engine recording is the unit of evidence, and regenerating it is a ceremony.** The
tier-0 engine is a binary, and a binary produces different confidences on different versions
and different platforms. If CI re-ran it, a threshold would move because a runner image was
updated, and claim 1 would spend its life going red for reasons that have nothing to do with
this repository. So the engine runs **here**, over the real corpus, and its normalised output
is committed to `recordings/ocr/` with the engine version and a fingerprint. CI derives every
threshold from the recording, and separately asserts that the adapter still parses what the
binary emits.

Regenerating the recording is therefore the one act that can silently move every number on
the scoreboard, and it is not allowed to be quiet. `make ocr-record` **prints the movement of
every threshold, per field, old against new, with N** — and refuses to overwrite until the
shift is explicitly accepted and the acceptance recorded. An engine upgrade that improves a
field is good news; an engine upgrade that moves a threshold nobody looked at is claim 1
becoming decoration.

**The envelope binds the traffic too, not only the generator.** A document system does not fail
with an error — it fails when a new scanning supplier ships 150 dpi, confidences drift down,
abstention doubles, and every gate still passes. Every threshold here is a statement of the form
*"at this score, on documents like the ones we measured"*; when the documents stop being like
that, the threshold does not become wrong, it becomes **unsupported**, and goes on publishing.
`src/manifest/core/drift.py` applies the same declared bands to arriving windows and reports a
finding — never an adjustment. It fires in both directions: a reader suddenly *confident* has
usually stopped seeing something, and that is the direction nobody watches.

**The corpus declares its operating envelope, and a generator that leaves it fails the
build.** The generator decides whether any number in this repository means anything. Degrade
too gently and every confidence sits at the top of the range, the reliability curve is a flat
line, ECE is measuring nothing and every threshold is trivially satisfiable. Degrade too
hard and everything abstains, the review queue is 100% and claims 1, 2, 4 and 5 are all
scored against a corpus nobody could read. Both failures report green.

So the target operating range is **declared data, not a note in an ADR**:
`corpus/envelope.yaml` states the intended confidence distribution and the acceptable band
for the abstention rate, per document type, and a test computes the actual figures from the
recording and **goes red when the generator drifts out of the band**. A corpus whose
difficulty nobody declared is a corpus tuned, unconsciously, until the claims passed.

**Untrusted documents are untrusted input.** A commercial invoice is a document a
counterparty wrote. Text inside it reaching an extraction prompt is indirect prompt
injection with money attached. Extraction prompts treat document text as data, structurally.
*This control already exists in Attestor — implement it properly, do not present it as novel.*

**IaC only · bootstrap local, everything else CI · no long-lived keys · state isolated per
layer · deterministic first · fail closed on correctness, fail open on enrichment · every
gate is attacked · done = runs + tested.** Same as Attestor and Watermark; if a rule here
seems to conflict with one of theirs, theirs is probably right and this file is wrong.

**Every regulatory and cost claim is traced or deleted.** Article, instrument, date for
legal statements. For cost: a measured figure or an explicitly labelled extrapolation. Never
a number that sounds right.

---

## Repository layout

```
manifest/
├── contracts/                  # THE SOURCE OF TRUTH — YAML, data, never imported by name
│   ├── documents/              #   one per document type: fields, types, required-ness, error budget
│   ├── reconciliation/         #   which fields must agree across which document types, with tolerance
│   └── entities/               #   party model, matching rules, merge/unmerge semantics
├── corpus/                     # the generator + labelled ground truth + committed fixtures
│   └── envelope.yaml           #   the declared operating range; a generator that drifts out fails CI
├── src/manifest/
│   ├── core/                   # PURE: normalisation, confidence, thresholds, reconciliation, ER, gates
│   ├── extraction/             # engine adapters → the normalised representation
│   │                           #   local/  the open-source OCR engine — the one that actually runs
│   │                           #   aws/    Textract · BDA · Bedrock — schema-tested; called once deployed
│   ├── cascade/                # engine routing: cheap first, escalate on low confidence
│   ├── classification/         # HS code proposal + the human decision path
│   ├── entities/               # entity resolution, merge/unmerge, lineage
│   ├── review/                 # the queue, capacity model, reviewer-integrity metrics
│   ├── versioning/             # document versions, re-extraction diffs, supersession
│   ├── handlers/               # the three functions that run in the estate — read_tier0,
│   │                           #   publish, provenance_gate. Adapters: they may import boto3,
│   │                           #   they call core, and they decide nothing themselves
│   ├── gates/                  # one module per claim
│   └── observability/          # OTEL spans, cost per document and per 1,000 pages
├── evals/                      # the seven claim harnesses — labelled, credential-free
├── recordings/                 # golden outputs; every published record reproduces exactly
│   └── ocr/                    #   the tier-0 engine's normalised output over the corpus, with its
│                               #   version and fingerprint. Every threshold is derived from this,
│                               #   never from a live run. Regenerating it is a ceremony, not a command
├── infra/
│   ├── bootstrap/              # LOCAL apply only
│   ├── foundation/             # VPC, KMS, S3 zones, budget guard, TTL reaper
│   ├── extraction/             # Textract/BDA wiring, Step Functions, review queue
│   ├── lakehouse/              # Iceberg, Glue Catalog, Athena, OpenSearch
│   ├── batch/                  # EMR Serverless for bulk reprocessing
│   └── analytics/              # Redshift marts
├── pipelines/                  # ingestion, reprocessing, dbt models
├── Dockerfile                  # the tier-0 reader as an image: the same binary and language data
│                               #   that produced recordings/ocr/, with the version asserted at
│                               #   build time. Shared by read_tier0 and provenance_gate
├── scripts/                    # gate_proof.py, preflight.py, check_core_is_pure.py, tf_validate.py
├── docs/                       # adr/ · SCENARIO · REGULATORY · DECISIONS · PORTFOLIO-CONTEXT · AWS-CONSTRAINTS · DAY-ONE
└── .github/workflows/          # ci.yml (every PR) · deploy.yml · destroy.yml
                                #   deploy and destroy are gated behind a protected environment
                                #   and have not been dispatched yet. Both must exist and both
                                #   must be validated — a repository with a deploy path and no
                                #   destroy path is how an estate gets left standing.
```

---

## The contract layer

**A document contract** declares each field: type, required-ness, the **error budget** it
carries (the acceptable rate of published-and-wrong), its retention class and whether it is
personal data. The confidence threshold is *derived* from the error budget against the
labelled set — it is never written in the contract by hand. A field with no error budget
cannot load.

**A reconciliation contract** declares which fields must agree across which document types,
with tolerance and unit. "Total on the invoice equals sum of line values" and "gross weight
on the bill of lading equals gross weight on the packing list within 0.5%" are data, not code.

**An entity contract** declares the party model and the matching rules, including
transliteration handling, and the semantics of merge and un-merge.

Changing a field's type, error budget or extraction semantics is a **re-extraction event**:
it requires a version bump and CI demands the diff report.

---

## Cost controls — always active

**Nothing is applied, so the real cost of this repository is zero.** These controls are
therefore *design* constraints, written and validated so that the estate is safe on the day
somebody does apply it — not budget management for a running system.

- Every resource tagged `manifest:expires-at`, with a scheduled reaper. An AWS Budget action
  disables the deploy role at its threshold. Both written, both validated, neither exercised.
- **The expensive things are per-page: Textract, Bedrock Data Automation, LLM extraction.**
  Volume is the cost driver, so volume is a design parameter — the cascade in
  `src/manifest/cascade/` exists to keep the expensive engine off pages that do not need it.
- **What the cascade's saving actually is:** the tier-routing distribution over the corpus is
  measured offline; unit prices come from published pricing pages, cited and dated. The
  product is a **cost model**. Say "modelled" every single time. A modelled figure that says
  so is worth more than a measured one nobody can reproduce, and infinitely more than a
  measured-sounding one that was never measured.
- **What the cascade cannot claim, and must never imply — until it can.** Before the first
  apply the upper tiers have not been called, so there is no accuracy figure for the fraction
  that escalates and there cannot be one.
  *"Accuracy held at X for Y% of the cost"* is unavailable here until a real run measures it,
  and a sentence that sounds like it is the single easiest way to make this repository dishonest.
  What the cascade eval proves is two things and stops: the routing rule sends the low-confidence
  pages up, and the pages it **keeps** at tier 0 meet their fields' error budgets. The value
  of the escalated fraction is an assumption, it is labelled one, and the sensitivity of the
  cost model to it is shown rather than hidden. After a real run the assumption may be replaced
  by a measurement — with its N, its date, and the run it came from.
- The **under €150** figure is a design ceiling, in this file. It may appear in the README as
  a *ceiling*; it may not appear as a *result* until a bill exists to quote.

---

## Git workflow

Conventional Commits: `<type>(<scope>): <description>`
Types: `feat | fix | infra | docs | refactor | test | chore`
Scopes: `contracts | core | extraction | cascade | classification | entities | review | versioning | gates | infra | ci | evals | corpus`

---

## Before any change — checklist

- Which of the seven claims does this serve?
- Is there exactly one correct answer here? Then it is code, not a model.
- Can it be validated with no AWS account? If not, why not?
- If it is a gate: is there a `gate-proof` mutation that breaks it?
- If it publishes a field: where is its provenance, and does the independent check pass?
- If it adds human review: what does it cost the queue, and does capacity still hold?
- If it states a threshold: derived from what, at what N, and inside which declared tolerance?
- If it touches the generator: does `corpus/envelope.yaml` still hold?
- If it states a cost: measured, or extrapolated and labelled as such?
