# Decisions locked before the first line of code

Settled in planning. Not open for re-litigation at the start of a session; open for revision
with a reason, and a revision becomes an ADR under `docs/adr/`.

## Scope

**1 · Three outputs stay:** structured record per document, cross-document reconciliation, HS
classification with a human loop. Reconciliation is the one that looks like plumbing and is
the one a domain expert will recognise as the real job.

**2 · The name is `Manifest`.** The trade document, and *made evident*. Tagline: *"every field
traces to a pixel."*

**3 · AWS only.** Same reasoning as Watermark.

**4 · Sanctions and denied-party screening are out of scope.** It is a real obligation for
this operator and it belongs to FintelliGuard's problem space. Two projects in one portfolio
doing financial-crime screening blurs both. Say in the README that it is out of scope and why
— an omission that is named reads as judgement; an omission that is silent reads as an
oversight.

**5 · No RAG surface as a headline.** A document-search surface over the corpus is useful and
cheap to add at the end, but it is not a claim and it is not the point. Three projects already
cover retrieval. If it costs time, cut it.

**6 · Redshift is in scope here, and it has to earn it.** It answers questions a single
document cannot: duty exposure by HS chapter, review-queue economics, extraction cost per
client, error rate by document source and by carrier. If those marts are not built, drop
Redshift rather than keeping it as a CV keyword.

## Method and technology

**7 · The confidence threshold is derived, never chosen — and the derivation states its own
uncertainty.** Computed from a declared per-field error budget against the labelled set, with
calibration measured (reliability curve, expected calibration error). This is the difference
between engineering and vibes, and it is the single strongest idea in the project. Design it
in Phase 0, before any extraction code exists.

Three things the original wording left open, settled here because each one is a way the idea
degrades into the thing it replaced:

- **The threshold is the lowest score whose *upper confidence bound* on the error rate fits
  the budget.** A point estimate from forty examples is a number with a confidence interval
  wider than the decision it is making. N is printed beside every threshold.
- **A field with too little evidence is `always-review`, never a high number.** If no
  threshold fits the budget at the available N, that is the answer, and it is declared. A
  0.999 nobody can justify is the magic number back in a better costume.
- **"CI fails when it moves" needs a declared tolerance**, or the first extra document turns
  the build red for no reason and somebody deletes the check. The tolerance is per field, it
  is data, and moving it is a reviewed change.

And the frame around all of it: every one of these figures is a statement about the
distribution *this repository generated*. The public dataset is the honesty check, and where
it has no field-level ground truth, the check is what it is and says so. ADR-0002.

**8 · Provenance is verified against the page, not against the record.** The gate re-reads the
crop at the recorded coordinates and confirms the value is there. Asking the extractor whether
it extracted correctly is Watermark's parity tautology in a different costume — the same trap,
noticed once already, must not be walked into twice.

**And re-reading the crop with the same engine in the same configuration is that same trap.**
It is a deterministic function replayed on a subset of its own input: it returns the same
token by construction. It is not worthless — it catches a box pointing at the wrong place, the
wrong page, the wrong instance of a repeated string, a coordinate-system error, a
post-processing transformation — but it cannot catch a confident misread, which is the failure
the claim sounds like it is about.

So independence is defined in **three layers of unequal and declared strength**, and the
README states which layer catches what. ADR-0003 specifies them.

**9 · A cascade, not a single engine — and it claims only what it can show.** *Revised
2026-08-09; the original wording said "the saving is a measured number (accuracy held at X,
cost at Y% of single-engine)", which decision 14 had already made impossible.*

Cheap engine first; escalate to the expensive one only where confidence is low. Which engines
fill which tier is decided in Phase 0 against current documentation.

The correction is about what the cascade eval is *able* to prove. The upper tiers are never
called, so **there is no accuracy figure for the pages that escalate and there cannot be one**.
Any sentence of the form "accuracy held at X for Y% of the cost" is unavailable here, and
writing it would be exactly the fabricated result this repository exists to argue against.

What the eval proves, and where it stops:

| Provable offline | Not available, and labelled so |
|---|---|
| The routing rule escalates the pages whose tier-0 confidence is below the derived threshold | What tier 1 or 2 would have read on those pages |
| The pages **kept** at tier 0 meet their fields' declared error budgets | Whether escalation actually repairs a field |
| The routing **distribution** over the corpus — what fraction lands in each tier | Any end-to-end accuracy number for the cascade as a whole |
| A cost **model**: that distribution × published, cited, dated unit prices | Any euro figure as a measurement |

The value of the escalated fraction is therefore an **assumption**, it is named as one, and the
cost model shows its sensitivity to that assumption rather than burying it. That is a weaker
claim than the original wording and it is the true one; a reader who works with these services
will know the difference immediately, and the weaker claim is the one that survives them.

The engineering argument is unaffected. Routing pages to the cheapest engine that can meet a
declared error budget, and being able to say which pages those are and why, is the work —
whether or not a bill was ever generated to prove it.

**10 · One normalised document representation.** Every engine produces the same internal shape
— blocks with text, confidence, page, geometry. The core never learns which engine produced a
value, and a test fails if an engine name appears in `core/`.

**11 · Human review is measured, not assumed.** Declared queue capacity; a threshold that
exceeds it fails the build. Reviewer integrity — time on task, agreement rate, sampled
re-review — is a first-class metric. **This is the part of human-in-the-loop that everybody
specifies and nobody builds**, and it is why claim 5 is worth more than it looks.

**12 · Bulk reprocessing on EMR Serverless.** Idempotent, resumable, cost-metered, with a
per-document diff. Human decisions already made on unchanged fields survive the reprocessing;
decisions on fields that *did* change are re-queued.

**13 · The corpus is synthetic-and-pathological plus a real public dataset.** The generator is
part of the argument. The public dataset is the honesty check — the same role real VED
telemetry plays in Fleet Risk. Verify the licence before committing anything.

**14 · Nothing is ever applied to AWS. The author deploys, if and when he chooses.**

The session builds everything up to and including the deploy path, and stops there: all code,
all local tests, all Terraform, `ci.yml`, `deploy.yml` and `destroy.yml` — validated, scanned,
gated behind a protected environment, **never dispatched**. `infra/bootstrap/` is not applied
either, despite being the one layer whose design permits a laptop apply. Posture: **ready to
deploy, not deployed**, exactly as Attestor and as Watermark's revised decision 15.

What this forbids, because these are the ways it gets softened by accident:

- No screenshot or console capture from a real estate. There is not one.
- No wall-clock time and no euro figure presented as measured. The €150 ceiling in `CLAUDE.md`
  is a design constraint and never a result.
- No sentence of the form "the estate was stood up and destroyed". What is claimed is
  `terraform validate` against real provider schemas and checkov at zero findings, with the
  limits of that stated: green means every attribute exists, not that every value is accepted.
- No "measured cost per 1,000 pages". See decision 15.

**15 · The cost figure is a model, and it says so.** Tier-routing distribution measured over
the corpus × published unit prices, each price cited with the pricing page and the date read.
Written as *modelled* everywhere it appears — README, CV, site. A modelled number that
announces itself is a stronger artefact than a measured one nobody can reproduce, and it is
the only honest option for a repository that has never called a billed API.

**16 · A local open-source OCR engine is the bottom tier of the cascade, and it actually
runs.** This is the decision that makes the project possible at zero cost, and it was nearly
missed. Claims 1 and 2 need real confidence scores and real geometry on really degraded pages.
Without a billed API call there is no other source for them, and inventing them would be
fabricating a result — precisely what this portfolio exists to argue against.

So: the local engine runs on the machine, on the real corpus, producing genuine confidences
that genuine calibration is computed from. The AWS engines sit above it as adapters behind the
same normalised representation, with their response mapping tested against the **documented**
schema. Their fixtures are *authored from that schema and labelled as authored* — never
described as captured responses.

The framing is not "we could not afford the real thing". It is that a local engine and a
managed service being interchangeable behind one representation is the strongest available
evidence that the abstraction is real. Choose the engine in Phase 0 with its licence checked.

**17 · `gate-proof` from the first gate, in the same commit.**

**18 · The repository is English. The conversation is Greek.**

**19 · Every threshold is derived from a committed engine recording, and regenerating it is a
ceremony.** The tier-0 engine is a binary; its confidences differ across versions and
platforms. If CI re-ran it, thresholds would move because a runner image changed, claim 1
would go red for reasons unrelated to this repository, and the check would be disabled within
a month. So the engine runs on the author's machine over the real corpus, and its **normalised
output** is committed to `recordings/ocr/` with the engine version and a fingerprint. CI
derives thresholds from the recording, and separately asserts that the adapter still parses
what the binary emits — the two failures are different and must stay distinguishable.

Regenerating the recording is the one act that can move every number on the scoreboard at
once, so it may not be quiet. `make ocr-record` prints the **movement of every threshold, per
field, old against new, with N**, and refuses to overwrite until the shift is explicitly
accepted and the acceptance recorded. This is decision 16's engine kept honest: it runs, and
what it produced is retrievable and dated.

**20 · The corpus declares its operating envelope, and drifting out of it fails the build.**
The generator decides whether any number here means anything. Too gentle and every confidence
sits at the top of the range, the reliability curve is flat, ECE measures nothing and every
threshold is trivially met. Too harsh and everything abstains and the queue is 100%. Both
report green.

`corpus/envelope.yaml` therefore declares, per document type, the intended confidence
distribution and the acceptable band for the abstention rate. A test computes the actual
figures from the recording and goes red when the generator leaves the band. Without it, the
degradation parameters get tuned — not dishonestly, just gradually — until the claims pass.

## Deliberately deferred, or out of scope

- **Live capture and screenshots — out of scope, not deferred.** See decision 14.
- The video walkthrough — after Phase 4, from the local run.
- Site and CV integration — see `docs/PORTFOLIO-CONTEXT.md`.
- Any Readiness Framework worked example — after the system exists, never as a design target.
