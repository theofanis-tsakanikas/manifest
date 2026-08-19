# ADR-0005 — The tier-0 engine, and why its output is a committed recording

**Status:** accepted · **Date:** 2026-08-09 · **Documentation verified:** 2026-08-09
**Amended 2026-08-19:** the premise in Context has been superseded; the decision is stronger for it.

> **This was reasoned from "there is no billed API to get them from" and that stopped being
> true.** The estate has been applied since 2026-08-10 and Textract read 2,336 pages on
> 2026-08-15. The decision survives on its better argument, which was always the second one: a
> threshold derived from a live call is a threshold nobody else can reproduce, and a recording
> made by a binary anyone can run is one they can. The recording is still the unit of evidence,
> and CI still derives every threshold from it rather than from a service.

## Context

Claims 1 and 2 need **real confidence scores and real geometry on really degraded pages**.
Decision 14, **as it stood on the date above**, deferred every apply — so there was no billed API
to get them from, and inventing them would be fabricating exactly the kind of result this
portfolio exists to argue against. Decision 16 therefore puts a local open-source engine at the bottom of the cascade,
running on the machine, on the real corpus.

`docs/AWS-CONSTRAINTS.md` then made that decision load-bearing for a second reason: Textract,
Bedrock Data Automation and Comprehend all support the same six languages, and **Greek and Dutch
are not among them**. For two of the three document languages in this scenario, the local engine
is not the cheap tier — it is the only one.

## Decision

### The engine: Tesseract 5, Apache License 2.0

| Criterion | Why it decided |
|---|---|
| **Per-word confidence and per-word geometry** | Claims 1 and 2 are unprovable without both. TSV output gives a confidence and a pixel box per word |
| **Licence** | Apache-2.0. Compatible with this repository's MIT, and no field-of-use restriction |
| **Greek and Dutch** | Trained data exists for both, which the managed services do not have |
| **Deterministic on CPU, offline** | No GPU, no model download at run time, no network. A claim that needs a GPU is a claim a reviewer cannot check |
| **A binary, not a Python framework** | The neural alternatives (docTR, EasyOCR, PaddleOCR) each pull a deep-learning runtime into a repository whose core may not import one, and their output varies with the runtime version in ways that are harder to pin than a binary's |

**Its confidence is badly calibrated, and that is a feature.** A well-calibrated engine would
make claim 1 a formality: threshold at the budget, done. Tesseract's `conf` is a
classifier-internal score on a 0–100 scale that is not a probability of correctness, which is
precisely the condition `docs/SCENARIO.md` names — *"a threshold on an uncalibrated score is a
magic number"* — and precisely what ADR-0002 exists to handle. The reliability curve will be
visibly wrong, and that is the result.

**What it does not give**, said now rather than discovered:

- **Handwriting.** Effectively nothing. The corpus's handwritten-correction pathology therefore
  produces abstentions by construction, and the README says so rather than implying the system
  reads handwriting. Textract documents handwriting support in English only, so the escalation
  path does not close the gap for Greek or Dutch either.
- **Layout understanding.** It reads words and lines. Key–value association, table structure and
  cross-page table continuation are *ours* to build over the normalised representation — which is
  the right place for them anyway, because that is the logic claims 2 and 4 are about.
- **Stability across versions.** Which is the rest of this ADR.

**The adapter shells out to the binary and parses TSV.** No Python wrapper package. Two reasons:
a wrapper is one more dependency whose version can change the output, and the adapter's job —
turn a process's stdout into the normalised representation — is exactly the thing that should be
visible in this repository rather than delegated. `core/` cannot import `subprocess` at all, so
the boundary is enforced rather than intended.

### Rendering: the corpus is generated at 300 DPI

Three constraints meet at one number. Tesseract's documentation recommends **at least 300 DPI**.
Textract and BDA both cap images at **10,000 pixels on a side** — A4 at 300 DPI is 2,480 × 3,508,
comfortably inside; at 1,200 DPI it would be refused. Both services document a **15-pixel
minimum character height**, which at 300 DPI is about a 4 pt character, so ordinary body text is
well clear of the floor and the corpus's deliberately tiny degraded text is legitimately below
it. 300 DPI satisfies all three, and the number is written down here so it is not re-chosen.

### The engine's output is a committed recording

**The problem.** A binary produces different confidences on different versions and platforms.
This machine has 5.5.2; a CI runner's package index has whatever it has. If CI re-ran the engine,
a threshold would move because a runner image was updated, claim 1 would go red for reasons that
have nothing to do with this repository, and within a month somebody would remove the check.

**The decision.** The engine runs **here**, over the real corpus, and its **normalised output** —
not its raw TSV — is committed to `recordings/ocr/` together with:

- the engine version string as the binary reports it
- the language data versions
- the corpus generator's seed and fingerprint
- a content digest over the recording itself

Every threshold in this repository is derived from that recording. The engine genuinely ran, on
genuinely degraded pages, and what it produced is retrievable, dated and diffable. That is a
stronger artefact than a live run nobody can reproduce.

**CI does two different things, and they must stay distinguishable.** It derives thresholds from
the recording — deterministic, no binary needed. And, separately, it runs the binary over a small
slice of the corpus and asserts **that the adapter still parses what the binary emits** — a
format check, not a confidence check. A parse failure and a threshold movement are different
defects, and a job that conflated them would report the wrong one.

### Regenerating the recording is a ceremony

It is the one act that can move every number on the scoreboard at once. `make ocr-record`:

1. re-runs the engine over the corpus and derives the thresholds the new output implies
2. **prints the movement of every threshold, per field: old, new, both `n`s, and the direction**
3. prints the new engine and language-data versions against the old
4. **refuses to overwrite** unless the shift is explicitly accepted
5. writes the acceptance — who, when, which engine version, which thresholds moved — beside the
   recording, where it is reviewed with the diff

An engine upgrade that improves a field is good news. An engine upgrade that moves a threshold
nobody looked at is claim 1 becoming decoration, and the difference between the two is entirely
whether somebody was shown the movement.

### Environment: the language data is not installed by default

This machine currently has `eng` only. Greek (`ell`) and Dutch (`nld`) trained data are a
separate install, and without them ADR-0004's language finding is true and unexercised. The
requirement is recorded in the corpus build instructions and asserted by a preflight check that
**fails rather than skips** when a language the corpus contains has no data — a skip here would
silently reduce the corpus to English and every claim with it.

### Fixture policy for the AWS adapters

They are written, and **all three have now been called** — Textract and the escalation model on
2026-08-15, Bedrock Data Automation on 2026-08-13. Their fixtures are nonetheless still
**authored from the documented response schema**, labelled as authored in the fixture file, in
the test name and in the README, and that is now a choice rather than a description of what has
happened. The two artefacts do different jobs: a fixture proves the mapping handles every
documented field *including the ones no call happened to return*, and a captured response proves
only what one call returned. What the calls produced lives in `recordings/`, where a real
confidence belongs. A captured response may replace an authored fixture — and when it does, the
`_note` changes in the same commit, because an authored fixture presented as captured is the
defect this policy exists to prevent, and it is a defect in **either** direction. Each adapter
carries a
schema-conformance test asserting the mapping handles every documented field of the response,
including the ones this system does not use, so that a future need for `Polygon` is a change to
the representation rather than a discovery about the adapter.

## Consequences

- `recordings/ocr/` is committed data, and it is large. It holds normalised output, not rasters;
  `corpus/rendered/` stays git-ignored and reproducible from the seed.
- A corpus change invalidates the recording. The two fingerprints are checked against each other,
  and a mismatch is a hard failure — a threshold derived from a recording of a different corpus
  is the worst kind of green.
- The ceremony has to run somewhere a person is watching. It is a `make` target and deliberately
  not a CI job: a ceremony nobody attends is a command.
- The engine's weakness at handwriting sets a floor on the abstention rate that `corpus/envelope.yaml`
  must account for, or the envelope will be tuned against a limitation rather than against the
  degradation it is meant to describe.

## Sources consulted, 2026-08-09

- [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract) — Apache License 2.0;
  version 5 stable; TSV, hOCR, ALTO and PAGE output formats.
- [Improving the quality of the output](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
  — "Tesseract works best on images which have a DPI of at least 300 dpi"; internal Otsu
  binarisation with Adaptive Otsu and Sauvola available since 5.0.0.
- [Textract set quotas](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) and
  [BDA prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html) —
  10,000-pixel resolution ceiling, 15-pixel minimum character height, six input languages,
  English-only handwriting (Textract).
