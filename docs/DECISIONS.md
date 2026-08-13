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

*Outcome, 2026-08-10: built, and still not a claim.* It indexes published records — never raw
document text, which is counterparty-authored and is treated as data everywhere else in this
system. It appears on no scoreboard.

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

The correction is about what the cascade eval is *able* to prove **offline, which is where it
is proved and where it stays proved**. The eval calls no upper tier, so **there is no accuracy
figure for the pages that escalate and there cannot be one from the eval**. Any sentence of the
form "accuracy held at X for Y% of the cost" is unavailable here, and writing it before a run
produced it would be exactly the fabricated result this repository exists to argue against.

Decision 14's revision does not soften this. When the estate runs, a real measurement of the
escalated fraction becomes possible — and it is then a figure with a date, an N and a run
behind it, reported separately from the offline eval rather than merged into it. The eval's
job is to be reproducible by a stranger with no account; that job does not change.

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

**14 · This system is built to run on AWS. The first deploy ran on 2026-08-10, and what that
did and did not license is stated below.** *(Revised twice on 2026-08-10 — once before the
first apply and once after it. Both earlier texts are kept, because how each went wrong is the
useful part.)*

**Status: applied.** `deploy.yml` was dispatched against `main`, and `foundation`, `extraction`
and `lakehouse` applied. `batch` and `analytics` were left off — they are the estate's cost and
they stand up only on an explicit input. Documents were put through the deployed pipeline. The
estate was then torn down through `destroy.yml`.

What that changed, exactly, and nothing more:

- The sentences "never applied" and "never dispatched" are gone from this repository, because
  they were false the moment the first apply succeeded. A repository that describes a posture
  it no longer holds is the overclaim this project spends its life removing, and the direction
  of the error does not excuse it.
- Statements about the AWS **estate** — that the layers apply, that the trust policy holds,
  that the teardown takes what the deploy made — are now statements about something that
  happened, and they say when.

What it did **not** change, and these are the ones a reader should check first:

- **Textract has now been called; Bedrock Data Automation and the LLM extractor have not.**
  Superseded on **2026-08-12**, deliberately and by dispatch: the estate was applied with
  `enable_escalation_tiers=true`, one bill of lading abstained on seven fields, and those seven
  went up to tier 1. CloudTrail records `DetectDocumentText` against the `manifest-escalate`
  role at 06:40:39 EEST. `bda.py` and `llm.py` remain adapters against documented schemas,
  exercised against authored fixtures, never called.

  **The accuracy sentence does not move with it.** There is still no accuracy figure for the
  escalated fraction, because one document is not a measurement. What the run established is
  that the routing climbs — the abstaining fields were sent up, the confident ones were not —
  and that is a different claim, stated as one.
- **No distributed job has ever executed.** `batch` was never applied. Claim 7 is proved by the
  pure planner and its ledger, on a laptop, and says so.
- **Every cost figure is still modelled.** A bill existing does not make a number measured; the
  number would have to have been measured. Decision 15 is unchanged.
- **Every claim is still scored offline.** Not one of the seven moved into the estate.

The order is: build everything up to and including the deploy path — all code, all local
tests, all Terraform, the compute that executes the pipeline, `ci.yml`, `deploy.yml` and
`destroy.yml`, validated and scanned and scoped to the environment their trust names — and **then**
deploy, deliberately, as a separate act. `infra/bootstrap/` applies from a laptop first;
everything above it applies from CI.

What was written before the first apply, kept as it stood:

- No screenshot or console capture from a real estate. There is not one yet.
- No wall-clock time and no euro figure presented as measured. The €150 ceiling in `CLAUDE.md`
  is a design constraint, and it stays a constraint until a bill exists to quote.
- No sentence of the form "the estate was stood up and destroyed" before one was. What is
  claimed today is `terraform validate` against real provider schemas and checkov at zero
  findings, with the limits of that stated: green means every attribute exists, not that every
  value is accepted.
- No "measured cost per 1,000 pages" until something measured it. See decision 15.

What holds *permanently*, and does not relax on the day of the first apply:

- **Every claim stays scored offline.** The seven claim harnesses run with no credentials, on
  a laptop, forever. A claim that needs a running estate to check is a claim a reader cannot
  reproduce, and an unreproducible claim is worth less than no claim.
- **The core stays engine-free.** Deploying does not entitle `core/` to import a client.
- **A figure changes category only with evidence.** "Modelled" becomes "measured" when a run
  produced it, and carries that run's date and N. Never because the estate exists now.

**What the original wording cost, recorded because it is the more useful half of this entry.**
The first version of this decision read *"Nothing is ever applied to AWS"*, and that sentence
was a misreading — the author's instruction was **build the code and do not deploy yet**, a
statement about sequence, and it was written down as a permanent posture. The cost was not
cosmetic:

- The tier-2 and Bedrock Data Automation adapters were never written, because an adapter that
  will never be called is hard to justify writing. `contracts/cascade/` declared them written
  anyway, and the router sent Greek and Dutch pages to them.
- No compute was written at all. The Step Functions machine called Textract directly and named
  one Lambda that did not exist, so the pipeline had no step that ran this project's own logic.
- `ReadAtTierZero` called Textract, because the local reader had nowhere to run in AWS —
  which quietly deleted the cascade's entire reason for existing.

A rule that says *never* stops work that a rule saying *not yet* would have required. That is
the lesson, and it generalises past this project.

**15 · The cost figure is a model, and it says so.** Tier-routing distribution measured over
the corpus × published unit prices, each price cited with the pricing page and the date read.
Written as *modelled* everywhere it appears — README, CV, site. A modelled number that
announces itself is a stronger artefact than a measured one nobody can reproduce.

It stays modelled after 2026-08-12, and the reason is worth stating rather than assuming: a
billed API *has* now been called, twice, for seven fields of one document. Two Textract calls
are not a bill and they are not a routing distribution. The figure becomes a measurement when a
corpus goes through a standing estate and an invoice exists to quote, with its date — not when
the first call succeeds.

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

**21 · The corpus is generated inside the reader image, for the same reason the recording is.**
*(Added 2026-08-11, after the first deploy.)*

`corpus/sheet.py` took the first font it found on the machine, from a list of three. On the
author's laptop that is Arial Unicode. On a Linux runner it was DejaVu — which covers Latin and
Greek and **not CJK** — so the thirteen Chinese characters in the corpus's party names rendered
as empty boxes.

Box geometry comes from font metrics. A different font is therefore a different ground truth,
and the generator already said so, in an error message written for exactly this case. That
error had never fired, because `--check` compared the runner's regenerated corpus against the
`corpus.json` the runner had just written — a tautology, in the one place the property was
supposed to be proved. Nothing else ran the check at all: it was in `make claims` and in no
workflow.

The first honest comparison in this project's life was `scripts/ocr_merge.py` refusing to
merge, because the shards had recorded a corpus whose fingerprint was not the committed one.

So the corpus, the reader binary and the reading now come from one image. One font, one
version, one set of boxes, on a laptop and in the estate alike. Three consequences, stated
rather than discovered:

- The committed ground truth is the **image's**, not the author's laptop's. Every offline
  figure re-derives against it.
- `corpus-check` on a macOS laptop now fails, correctly. That machine cannot produce this
  corpus, and a check that passed there would be the same lie in the other direction. It runs
  in CI, inside the image, as a job of its own.
- `register_fonts(strict=False)` exists for `tests/corpus/` and nowhere else, because those
  tests assert structural properties — a table breaking across a page, every placement carrying
  a box — that no font decides, and a suite that only runs inside a container is a suite a
  reader cannot run.

**What it cost to find, recorded because the pattern repeats.** This is the same defect as
decision 19's, one layer down: an artefact whose identity depended on which machine made it,
with a check that could not see the difference. The reader version was found by a deployed
Lambda failing on a missing threshold artefact. The font was found by a merge refusing. Neither
was found by anything designed to look.

**22 · Tier 3 points at the model the account holds an agreement for, and the reason is written
here because the value is not in the repository.**
*(Added 2026-08-12, when the first Greek page reached tier 3.)*

`escalation_model_arn` lives in `infra/bootstrap/terraform.tfvars`, which `.gitignore` refuses
because the same file carries an alert address that is personal data. So the one deployment
decision a reader would most want to check — *which model reads the documents* — is invisible to
anyone reading the repository, and the comment beside it is invisible with it. That is the whole
reason for this entry.

**What happened.** The tier was pointed at `eu.anthropic.claude-sonnet-5`. The first Greek
packing list to reach it was refused: *"anthropic.claude-sonnet-5 is not available for this
account"*. `get-foundation-model-availability` reports the agreement as `NOT_AVAILABLE` for
Sonnet 5 and for every Claude 4 variant, and `AVAILABLE` for `anthropic.claude-haiku-4-5`.
Accepting a model agreement is a commercial act on the account rather than a deployment step,
and it is not one an automated agent should perform on somebody's behalf. The tier points at
what the account already holds.

**What did not change.** The profile is still an `eu.*` one, and its six underlying models were
checked on the day: `eu-north-1`, `eu-west-3`, `eu-south-1`, `eu-south-2`, `eu-west-1`,
`eu-central-1`, and nothing else. `docs/REGULATORY.md` says document text is processed in the
EU, and that sentence has to stay true when the model changes — which is exactly the check a
model swap invites somebody to skip.

**Two facts about the failure, worth more than the fix.** The refusal arrived only after IAM was
correct — the previous attempt failed on a policy that named the inference profile and not the
six foundation models it routes to, and *that* error named `eu-north-1`, a Region this estate
does not deploy into. Two different refusals, both reading as "AccessDenied on Bedrock", with
different causes and different fixes. And neither was reachable from any offline check: an
account's model agreements are not in the configuration.

**23 · The bulk job starts the pipeline; it does not carry the reader.**
*(Added 2026-08-13, when the executor-side read was written.)*

`pipelines/reprocess.py` had one unimplemented function — the thing an executor does with a
document — and the obvious implementation was to call the reader on the cluster. It is the wrong
one, for a reason that is the whole of claim 1's foundation.

Every threshold in this repository is derived from a recording made by **one build of one
binary**: `tesseract 5.5.0`, from Debian, asserted at image build time and checked against the
recording's own `reader_version` in CI. EMR Serverless custom images are built on Amazon Linux
2023, which carries no tesseract in its repositories at all — the same constraint that sent the
reader image to Debian in the first place (`docs/AWS-CONSTRAINTS.md`). Reaching one on the
cluster means compiling it there: a second build, with a different leptonica and different
flags, producing confidences that are *close* to the recording's and not the recording's. The
extraction handler looks its thresholds up by the reader's exact identity, so the failure would
not be wrong numbers — it would be a job that could not find an artefact, three layers from the
cause. Decision 19 is the same defect at a smaller scale and it cost a deploy to find.

So the executors start the per-document state machine and wait for the version it publishes. The
estate has one reader, in one image, and everything that reads goes through it.

**What that costs, stated rather than buried.** An executor slot spends its life polling Step
Functions instead of computing, which is a poor use of Spark by the usual measure. It is the
right use here: the unit of work is a page of OCR behind a Lambda, so what is being distributed
is the coordination and the ledger, not the arithmetic. If the reader ever becomes a library
that runs anywhere — a pure-Python engine, or a container the cluster can host without rebuilding
the binary — this decision is the one to revisit, and the thing to check first is whether the
recording still reproduces.

**And two defects found on the way, both of the same shape.** `--dry-run` was `store_true` with
`default=True`, so no spelling of the argument executed anything: a control that reads as a
deliberate safety and is not one. `ec2:DeleteVpcEndpoints` was scoped by a tag the OpenSearch
service cannot set on the endpoint it creates for us, so the condition excluded it permanently —
an estate that could not be torn down, behind an object nothing in this repository declared.
Neither was visible offline, and both were found by the estate refusing something.

**24 · The failure this project produces most is a check reading the wrong thing.**
*(Added 2026-08-13, after the third one in a day.)*

Not a decision so much as a finding, recorded because it has now happened often enough to be a
category rather than a run of bad luck. Every one of these reported green, and every one was
found by something else failing:

- `preflight` passed a second `-q` to pytest, which suppressed the summary line the README's
  test-count check parsed. The check had never once run.
- The edge-case assertions accepted any terminal state, so a document that sailed through and
  published a record read as *refused* — the exact failure they existed to detect, passing under
  the name of the check written to detect it.
- `enable_classifier` was exempted from the both-ways plan check because no trained artefact
  existed. True when written; untrue for a day before a policy document with no `count` reached
  a deploy — a defect that check would have caught the moment the exemption came off.
- The service-reachability check greps the handlers for a client call, and a docstring
  *describing that pattern* matched it. It reported a handler calling a service named `service`.
- The publication check matched `published = {` at a fixed indent, so rewriting one layer's local
  as a `merge` made every key invisible and three layers appeared to require a variable nothing
  supplied.

- A new model artefact was uploaded, the model resource was replaced, and the endpoint was never
  updated — SageMaker points at a configuration *by name* and the name had not changed. The fix
  for a 500 was written, committed, deployed and applied green, and the endpoint answered with
  the identical traceback from the identical old code.
- `_await_execution` looks back sixty seconds so a run started just before an upload is not
  missed, which means it can return the *previous* execution. Check 10 found that and fixed it
  with an `exclude` parameter; check 12, written afterwards, repeated it exactly — because the
  parameter is opt-in. It reported "9 rows before, 9 after, and the step wrote 9": three facts
  that cannot all be true, assembled from two different runs.

The shape is always the same: **the check's input stopped being what the check believed it was,
and nothing about that is visible from the check's own result.** A failing check names its
problem. A check whose input vanished names nothing, and reads exactly like success.

Two habits come out of it, and they are cheap:

**Parse the thing, do not match its shape.** `ast` for Python, the whole block for HCL. A regex
over source is a check that also tests the formatting, and formatting changes for reasons that
have nothing to do with the property.

**An exemption carries an expiry or a reason that can be re-read.** Every one above that was an
exemption had a reason that was true when written. None of them had anything that would notice
when it stopped being true — which is doctrine rule 6 applied to tooling instead of findings.

**And a fix to a shared helper belongs in the helper.** `exclude` was added as an optional
parameter, so the next caller got the old behaviour by default and reproduced the defect the
parameter exists to prevent. A safe default that has to be asked for is a safe default nobody
gets.

---

## Deliberately deferred, or out of scope

- **Live capture and screenshots — out of scope, not deferred.** See decision 14.
- The video walkthrough — after Phase 4, from the local run.
- Site and CV integration — see `docs/PORTFOLIO-CONTEXT.md`.
- Any Readiness Framework worked example — after the system exists, never as a design target.
