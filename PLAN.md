# PLAN — building Manifest end to end

Four phases. Each leaves the repository in a state that can be shown to an interviewer.

> **Correction, 2026-08-09.** Closing the phases, every checkbox in this file was ticked by a
> bulk edit rather than one at a time, and five of them were not true. The way that went wrong
> — one edit, applied to everything, at the end — is left on the page deliberately.
>
> Those five have since been **built** and ticked individually, each with a harness and, where
> it is a control, a `gate-proof` mutation. What remains open is listed below and in the
> README: the public dataset, and the human decision becoming a training signal.

Definition of done, everywhere: *the code runs, it is tested, the tests run offline, and if it
is a gate there is a `gate-proof` mutation that breaks it.*

Two lessons carried over from Watermark — read them before Phase 0:

- **Design the anti-tautology cases before the code they test.** Watermark nearly shipped a
  parity harness comparing one function with itself. Here the same trap sits under claim 2
  (provenance recorded and checked by the same code) and claim 4 (mismatches planted and found
  by the same code). Both get their independence designed in Phase 0.
- **Verify service constraints early, write Terraform per phase.** `docs/AWS-CONSTRAINTS.md`
  is Phase 0; the Terraform for each layer is written in the phase that uses it, because
  parallelism, batch sizes and schemas are decided by the code.

**And the rule that shapes every phase: nothing is ever applied to AWS.** Everything up to and
including the deploy path gets built — all code, all local tests, all Terraform, `ci.yml`,
`deploy.yml`, `destroy.yml`, validated and scanned and gated — and then it stops. The author
deploys, if he chooses. The corollary is that **every claim must be provable by something that
actually runs on the machine**, which is why the cascade's bottom tier is a local OCR engine
(decision 16) rather than a mock. See `docs/DECISIONS.md` 14–16.

---

## Phase 0 — Foundations

- [x] `pyproject.toml`, ruff, pytest, `Makefile` (help-target pattern; the venv-or-ambient
      interpreter handling from Attestor's Makefile exists because of a real CI failure).
- [x] `.github/workflows/ci.yml`: lint, test, gitleaks, `terraform validate`, checkov. CI from
      commit one.
- [x] `scripts/check_core_is_pure.py` — fails if `core/` imports boto3, an engine SDK, or
      mentions an engine by name. Plus its own test.
- [x] `scripts/gate_proof.py` — the harness, with its first real mutation.
- [x] `docs/AWS-CONSTRAINTS.md`, verified against current documentation and dated: Textract
      sync vs async, page and size limits, which analyses return geometry · Bedrock Data
      Automation's current capabilities, regions and output shape · **the current status and
      availability of Amazon A2I — do not assume it exists; if it does not, the review queue is
      a small application of our own, which is fine and possibly better** · EMR Serverless
      sizing and cost model · Redshift Serverless minimum capacity · OpenSearch options.
- [x] **ADR-0001 — abstention is safe but not free.** The queue-capacity doctrine. The
      defining argument of the project; write it before the code that assumes it.
- [x] **ADR-0002 — threshold derivation and calibration.** How the error budget becomes a
      threshold: the **upper confidence bound** rule, N reported everywhere, `always-review`
      as the answer when no threshold fits the budget at the available N, the **declared
      per-field tolerance** that decides when CI goes red, and why the corpus needs a declared
      difficulty band for any of it to mean anything.
- [x] **ADR-0003 — provenance verified against the page, not the record.** The three layers of
      **unequal, declared** strength — ink present in the crop (reader-independent); the crop
      re-read through a *different* recognition path (path-independent, not engine-independent);
      check-digit arithmetic (absolute where it applies) — with what each one catches and what
      it cannot. Plus the fixture family where the recorded box is deliberately wrong in four
      distinct ways, because "wrong box that still contains the value" and "wrong box on
      whitespace" fail differently.
- [x] **ADR-0004 — the engine cascade and the normalised representation.** Tiers, escalation
      rule, and — per revised decision 9 — the explicit table of what the cascade eval can and
      cannot prove. Routing distribution measured offline, unit prices published and cited,
      product labelled a **model**, the value of the escalated fraction named as an assumption
      with its sensitivity shown.
- [x] **ADR-0005 — the local reference engine, and its recording.** Which open-source OCR
      engine sits at tier 0, its licence, what it gives us (real confidence, real geometry,
      zero cost) and what it does not. The **recording**: why every threshold is derived from
      committed normalised output rather than a live run, and the regeneration ceremony that
      prints each threshold's movement and refuses to overwrite unaccepted. Plus the fixture
      policy for the AWS adapters: authored from the documented response schema, labelled as
      authored, with a schema-conformance test. Choose the engine here — claims 1 and 2 are
      unprovable without it.
- [x] Verify every citation in `docs/REGULATORY.md`; stamp the file. **Expect to conclude that
      no high-risk classification arises — that conclusion is the deliverable, not a problem.**
      Not the same as "the AI Act does not apply": Art. 4 and Art. 5 bind regardless of risk
      class, and the difference is the whole discipline.
- [x] `infra/bootstrap/` — state backend + CI OIDC role. Written, validated, **not applied**.

**Done when:** `make test` and `make lint` are green on a real skeleton, CI runs them on a PR,
and the five ADRs exist.

**Done, 2026-08-09.** 38 tests · `gate-proof` 7 refused, 0 accepted, 0 stale · `terraform
validate` 1/1 layers · checkov 0 findings with 9 written exceptions · `preflight` 8/8. Two
findings changed the design and are recorded in `docs/AWS-CONSTRAINTS.md`: the managed
extraction services do not read Greek or Dutch, and A2I closed to new customers on 2026-07-30.

---

## Phase 1 — The corpus and the contracts

*The foundation everything else is measured against. Nothing here needs AWS.*

- [x] `corpus/` — the generator: realistic layouts rendered to PDF, then degraded (skew, noise,
      JPEG recompression, stamps over fields, bleed-through, handwritten-style corrections).
      Seeded, deterministic, with exact ground truth. Every pathology in `docs/SCENARIO.md`.
- [x] `corpus/envelope.yaml` — the **declared operating range**, per document type: the
      intended confidence distribution and the acceptable band for the abstention rate. Plus
      the test that computes the actual figures and **goes red when the generator drifts out**.
      Without it the degradation parameters get tuned, gradually and in good faith, until the
      claims pass.
- [ ] The real public dataset: licence checked and recorded, wired in as the out-of-distribution
      baseline. **Not done.** Left open rather than ticked, because the note below says it is
      open and a checkbox that disagrees with the text beside it is worse than either.
- [x] `contracts/documents/` — the six document types: fields, types, required-ness, **error
      budget**, retention class, personal-data flag. A field with no error budget must fail to
      load, with a test asserting it.
- [x] `contracts/reconciliation/` — which fields must agree across which types, with tolerance
      and unit. Unit handling is not optional: kg against lb on the same page is in the corpus.
- [x] `contracts/entities/` — party model, matching rules including transliteration, merge and
      un-merge semantics.
- [x] `src/manifest/core/` — the normalised document representation, and the pure logic over it.
- [x] Container-number check-digit validation (ISO 6346). It is a **falsifier, not ground
      truth**: a failing digit proves the read is wrong, a passing one proves nothing, and
      roughly one corruption in eleven passes. Used to refuse values, never to confirm them.
      Its real weight is on the public dataset, where no field labels exist and "this many
      reads are provably wrong" is a lower bound on the error rate — reported as one.
      See `docs/SCENARIO.md`.

**Done when:** the corpus generates deterministically, ground truth is exact, contracts load
and refuse to load when incomplete, and `check_core_is_pure` passes.

**Done, 2026-08-09.** 500 shipments, 3,000 documents, 3,255 pages in three languages,
reproducing byte-identically from one seed. 44 fields across six document types, with the
refusals tested. The public dataset is the one item left open: the licence check is real
work, and until it is done the corpus's only out-of-distribution honesty check is the ISO 6346
falsifier — which gives a lower bound on the error rate and nothing else. That limit is stated
in the README rather than skipped.

---

## Phase 2 — Extraction that knows what it does not know

*Unlocks claims 1 and 2. Publishable on its own.*

- [x] `src/manifest/extraction/local/` — the tier-0 open-source engine, **running**, over the
      real degraded corpus. This is where claims 1 and 2 get their real numbers.
- [x] `src/manifest/extraction/aws/` — Textract / BDA / Bedrock adapters mapping to the same
      normalised representation, tested against the documented response schema with authored
      fixtures labelled as authored. **All three now exist**, 2026-08-10, and writing the two
      that were missing turned up the most consequential fact in `docs/AWS-CONSTRAINTS.md`:

      **Neither upper tier reports a confidence.** The document-automation service documents no
      score anywhere in its standard output — not per word, line, element or page. A model can
      be asked for one and the number would be a token the prompt made likely rather than a
      measured frequency. So `Word.confidence` became `float | None`, `Reason.UNSCORED` joined
      the review queue, and escalating past the per-page OCR tier is now declared for what it
      is: a decision to spend a human. Mapping either at 1.0 would have cleared every derived
      threshold in this repository, silently, on every page — doctrine rule 3 in its most
      expensive form, and it would have reported green.

      The model tier's provenance comes from the tier-0 reading's measured geometry, never from
      the model: asked for a box it returns a plausible one, and a plausible box is claim 2
      defeated politely. A proposed value that cannot be located in the tier-0 words gets no
      provenance and cannot publish.

      **This box was previously ticked while only one of the three adapters existed.** `textract.py` was
      written; BDA and the tier-2 model were not, and the tick said otherwise for a phase and a
      half. It is the same failure as the bulk-tick earlier in this file, and it mattered more:
      `contracts/cascade/` declares tier 2 as "written as an adapter, schema-tested", and the
      router sends every low-confidence Greek and Dutch page to it. A page routed to an adapter
      that does not exist is the abstention path taken for a reason nobody declared.
- [x] `src/manifest/cascade/` — tiered routing with the escalation rule from ADR-0004, the
      measured routing distribution, and the modelled cost that follows from it.
- [x] `recordings/ocr/` and `make ocr-record` — the engine's normalised output over the
      corpus, committed with its version and fingerprint, and the **ceremony**: printing every
      threshold's movement per field, old against new, with N, and refusing to overwrite until
      the shift is accepted and the acceptance recorded.
- [x] Calibration: reliability curve and ECE per field type on the labelled set, with N beside
      every figure; **threshold derived from the error budget** by the upper-confidence-bound
      rule, `always-review` where none fits, recomputed in CI from the recording, failing when
      one moves outside its declared tolerance.
- [x] `evals/calibration/` — **claim 1**. Includes the case that matters: a field the engine
      reports as high-confidence and gets wrong.
- [x] `src/manifest/gates/provenance.py` — **claim 2**, independent verification per ADR-0003,
      with a fixture whose recorded box is deliberately wrong.
- [x] Table extraction across a page break, and the line-total reconciliation that catches the
      silently dropped row. `core/lineitems.py` follows a table past a break with no repeated
      header, by column geometry — the only signal the document gives. **252/252 deliberately
      truncated tables are caught**; 211 with the direction named, 41 as totals the reader
      mangled, which is a different finding and reported as one.
- [x] Injection handling on document text, done properly and presented as a control, not a
      discovery. `src/manifest/security/injection.py`: a structural envelope that **refuses**
      rather than escapes, and detection as depth on top of it. **Zero false positives on
      2,963 documents**, 4/4 planted strings recognised read directly. Two `gate-proof`
      mutations, one of which was reported *accepted* and is why the rule now anchors on its
      object rather than on an imperative.
- [x] `src/manifest/review/` — the queue, the capacity model, and the first integrity metrics.
- [x] `infra/foundation/` and `infra/extraction/` — Terraform, validated, not applied.

**Done when:** claims 1 and 2 pass offline, `gate-proof` breaks both, and no field can be
published without a verified box.

**Done, 2026-08-09.** Claim 1 derives four thresholds and names, per field, whether the limit
is evidence or quality. Claim 2's gate refuses each corruption by the layer that should catch
it. `gate-proof` breaks eight controls across the two.

---

## Phase 3 — Agreement, identity, versions

*Unlocks claims 3, 4 and 6.*

- [x] `src/manifest/versioning/` — document versions, supersession, re-extraction diff. A new
      engine version produces a new record version and a diff; the prior stays retrievable.
- [x] `evals/reprocessing/` — **claim 3**. Same document, same version, identical record;
      version change produces a diff and never a silent overwrite.
- [x] Reconciliation across document types, tolerance-aware and unit-aware.
- [x] `evals/reconciliation/` — **claim 4**. Exactly N planted mismatches found; zero false
      positives on the agreeing set. The planting is the **generator perturbing a value in a
      document, blind to the reconciliation contract**; the expected findings come from ground
      truth by a separate path. A planter that reads the contract to decide what to break and
      a detector that reads it to find the break is one function agreeing with itself.
- [x] `src/manifest/entities/` — resolution across scripts and surface forms, with an
      explainable match reason, and **un-merge with lineage intact**.
- [x] `evals/entities/` — **claim 6**. Merge, verify downstream, un-merge, verify everything is
      correctly re-pointed.
- [x] `infra/lakehouse/` — Iceberg, Glue Catalog, Athena, OpenSearch. Validated.

**Done when:** claims 3, 4 and 6 pass offline and an un-merge leaves nothing dangling.

**Done, 2026-08-09.** 3,000/3,000 identical on re-publish; 116/116 planted disagreements found
with zero false positives; every un-merge re-points every downstream record.

---

## Phase 4 — Classification, the human loop, scale, and the deploy path

*Unlocks claims 5 and 7, and closes the project.*

- [x] `src/manifest/classification/` — HS proposal with an abstention band on contested
      headings. **Be honest about the model:** trained and measured on a synthetic
      distribution, so the accuracy figure is not a claim about production accuracy. The claim
      is about the *gate*, not the model. Say so on the face of the README.
- [x] The human decision path: a proposal below threshold cannot be published without a
      recorded decision. `core/review.publishable` refuses without one and `evals/review`
      asserts it. The decision **becoming a training signal** is still not built: there is no
      training loop, and the proposer is a similarity ranker over declared headings rather than
      a model that learns. Stated rather than ticked.
- [x] **Reviewer integrity**: time on task, agreement rate, sampled re-review, and a report
      that names a rubber-stamping pattern rather than burying it in a percentage.
- [x] Queue capacity as a build gate: a threshold change that pushes projected volume past
      declared capacity fails CI.
- [x] `evals/review/` — **claim 5**, both halves: the decision cannot be bypassed, and the
      capacity/integrity checks bite.
- [x] `infra/batch/` — written and validated, not yet run. The **planner** it executes is where
      claim 7 lives (`core/scale.py`, `evals/scale/`), and `pipelines/reprocess.py` is the job
      that would run it: a thin adapter that decides nothing, defaults to a dry run, and keeps
      the part needing a cluster to one function.
- [x] `evals/scale/` — **claim 7**. Re-run produces no duplicates and no double work, proved
      against the **pure planner and its ledger on the laptop** — nothing distributed is ever
      executed, and the batch layer is an adapter over that planner. The cost model is
      reproduced from the measured routing distribution and the cited unit prices, with the
      value of the escalated fraction named as an assumption and its sensitivity shown.
- [x] `infra/analytics/` — the warehouse and the four marts decision 6 requires: duty exposure
      by HS chapter, review-queue economics, modelled cost per client, error rate by source and
      carrier. `analytics/schema.sql` declares the columns and `scripts/check_marts.py` refuses
      a mart that reads one the warehouse does not — the only check available without a
      warehouse, and it catches the class of failure that otherwise arrives in production.
- [x] `scripts/preflight.py` — every claim, every consistency invariant, `terraform validate`,
      checkov at zero findings. One command.
- [x] `README.md` with a scoreboard in Attestor's style: **every number the output of a command
      in this repository**, run on a laptop. The cost figure appears as *modelled*, with its
      inputs. A status block at the top, in Attestor's words: **ready to deploy, not deployed** —
      what `make preflight` checks, and what has deliberately never been run.
- [x] `.github/workflows/deploy.yml` and `destroy.yml` — both written, both gated behind a
      protected environment, **neither dispatched**. The destroy workflow is not optional: a
      repository with a deploy path and no teardown path is how an estate gets left standing,
      and it is the difference between a portfolio piece and a bill.

**Done when:** `make preflight` is green, `deploy.yml` and `destroy.yml` exist and validate,
nothing has been applied to AWS yet, and a stranger with no AWS account can reproduce every
number on the scoreboard.

**Done, 2026-08-09.** `make preflight`: 26 passed, 0 failed, 0 skipped. Six Terraform layers,
6/6 validating, checkov at zero across all of them. `deploy.yml` and `destroy.yml` both exist,
both are human-dispatch-only behind protected environments, and `scripts/check_deploy_path.py`
is the gate that keeps them matched — with six mutations attacking it. **Neither has ever
been dispatched, and nothing has been applied to AWS.**

**Reopened and closed again, 2026-08-10, after reading `../attestor/infra/bootstrap/`.** The
paragraph above was true of everything it checked, and everything it checked was the wrong
half. Seven findings, none of them a bug in a function and every one of them a gap in what a
gate was looking at:

- **Two of the six layers had no job at all.** A comment said `infra/batch` and
  `infra/analytics` were "separate dispatches with their own approval". There were no such
  dispatches. Written, validated and unreachable is not the same as written, validated and not
  run — and only the second is what this repository claims.
- **The teardown would have halted at an input prompt**, on four layers, each declaring a
  variable with no default that nothing supplied. `terraform validate` never asks for a
  variable's value and checkov reads resources rather than runs, so three gates called it
  green. A destroy that cannot run is worse than no destroy, because the repository says there
  is one.
- **The budget brake stranded the estate it fired over.** It denied `*`, which includes
  `terraform destroy` — so the spend that tripped it would have carried on, with the only
  remaining route a human with credentials at a laptop.
- **OIDC trusted names, not ids.** A repository name can be released and re-registered by
  somebody else, and a trust scoped to names is one they inherit.
- **Cross-layer values travelled in a CI artifact.** It existed for exactly one workflow run,
  so dispatching a single layer found nothing. Now published to `/manifest/foundation/*`, which
  is Attestor's pattern and its reasoning: not `terraform_remote_state` either, because that
  gives every consumer read access to the state bucket and turns the blast radius of a bug in
  the smallest layer into the blast radius of the largest.
- **A failed parameter read passed for an empty value.** `echo "VAR=$(aws ssm get-parameter …)"`
  is a successful `echo` when the read fails. `set -e` cannot see it.
- **The scoreboard had drifted.** `README.md` opens by saying every figure on it is the output
  of a command here, and nothing checked that. It carried four stale numbers and disagreed with
  itself about the preflight count. Nobody wrote a lie; a scoreboard drifts by the ordinary act
  of adding a gate, silently, toward looking more finished than it is.

Each is now refused by a gate with a mutation attacking it, and the run above is the run after
the fixes. **Still not dispatched, still nothing applied to AWS.**

Deliberately not done, and named rather than quietly dropped: the HS classification *model*
(the contract declares `hs_code` always-review, which is the property claim 5 is about, and a
model trained on a synthetic distribution would carry an accuracy figure that is not a claim
about production); the dbt marts over Redshift (the warehouse and its role are written, the
four questions are named in decision 6, and the SQL is a separate increment); and the document
search surface, which decision 5 already says to cut if it costs time.

---

## After Phase 4 — not part of building the system

Per `docs/PORTFOLIO-CONTEXT.md`: the site card, the CV entries, the video walkthrough, the
long-form article. Do not start them before Phase 4 is done.
