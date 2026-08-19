# Changelog

What changed, and — more usefully — what each change was found by. Dates are the day the work
landed on `main`.

The format is loosely [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There are no
releases: this is a reference implementation, and the useful unit is the finding rather than the
version.

---

## 2026-08-19

### Fixed

- **Lineage is a graph, and a node with edges will not delete.** With the permission finally in
  place, all thirty-three SageMaker lineage deletions came back
  `ValidationException: Cannot delete entity with associations`. `scripts/sagemaker_lineage.py`
  now enumerates associations from the project's own entities outward, in both directions, and
  cuts 116 edges before deleting 33 nodes. The `artifact/*` reach in the new IAM statement is
  stated rather than hidden: an edge's far end is keyed by an S3 URI and carries no name IAM can
  pattern-match, so the constraint lives in the caller.
- **The repository said nothing had ever been applied, and everything had.** Nine sentences across
  five files — including the deploy form a human reads every time they stand the estate up.
  `docs/DECISIONS.md` 14 is superseded in three places rather than rewritten.

### Added

- **`scripts/check_policy_actions_can_match.py`** — every action in the deploy policy must sit in a
  statement whose resources could match it. 397 actions across 42 statements. Genuine
  cross-service grants are declared in `contracts/deploy/policy_services.yaml` with the reason,
  because widening a statement to `["*"]` to silence a lint trades a lint failure for a real
  over-grant. Mutation 56 in `gate-proof` relocates a verb into a DynamoDB-scoped statement; only
  this check refuses it.
- **A README that follows the portfolio standard**, with a banner, three badge rows, a Mermaid
  architecture diagram, and every claim sitting under the image that proves it.

### Known, and named rather than fixed

- A document that abstains on every field never reaches the lake; its branch ends at
  `PublishTheAbstentions → QueueForReview`. Found by querying the deployed estate.
- `provenance_verified` conflates "the gate refused" with "the gate does not apply".
- The warehouse loads before any document exists, so a fresh estate's marts are empty until a
  second deploy.
- Nothing gates the reader image's base-OS CVEs.

---

## 2026-08-18

### Fixed

- **Four IAM verbs were granted on a DynamoDB table, and SageMaker is not one.** The teardown tore
  down all five layers, reported success, and left thirty-three lineage entities standing. The
  permissions existed in the file, spelled correctly and reviewed, inside a statement scoped to
  SQS, DynamoDB and Step Functions. `terraform validate` cannot see it because the document is
  well-formed; checkov cannot see it because it asks whether a statement is too broad.
- **A refused listing was reported as an empty one.** `sagemaker_lineage.py` caught every
  exception, printed to stderr and returned zero — so the step went green having looked at
  nothing. The three listings are now collected independently, because one missing verb used to
  blind the other two.
- **`estate_sweep.py` printed one line per KMS key it could not describe** — several hundred lines
  of opaque identifiers, under which the one real finding was buried.

---

## 2026-08-15

### Added

- **Tier 1 was called for the first time**: 2,336 eligible pages, 127,142 words, recorded to
  `recordings/textract/` with the reader version and a corpus fingerprint. Three of its 35 fields
  derive a usable threshold; four derived 0.000 and are floored to always-review, because a
  threshold of zero separates nothing.
- **`scripts/check_every_gate_runs.py`** — the largest finding of the week. CI was running nine of
  fifteen harnesses, and claim 2 ran in no workflow at all. Three hand-maintained lists of "what
  proves this repository" had drifted, and nothing compared them to the directory.
- `handlers/harvest.py` and `handlers/watch.py`, so the feedback loop and the drift envelope have
  callers in the running system rather than only offline proofs.

### Changed

- The CI corpus job generated the corpus twice and hit its timeout mid-render. One generation plus
  `git diff --exit-code` took the job from 60+ minutes to 28.

---

## 2026-08-10

### Added

- **The first deploy.** `deploy.yml` dispatched against `main`; `foundation`, `extraction` and
  `lakehouse` applied; documents went through the deployed pipeline; `destroy.yml` took it down.
  The sentences "never applied" and "never dispatched" left the repository the same day.

### Fixed

- **No Linux distribution ships tesseract 5.5.2.** Every threshold had been derived on the
  author's laptop from a Homebrew build the deployed image could never run. The fix was not a
  different base image — it was `record.yml`, a dispatch-only ceremony that runs the recording
  inside the image the estate uses, commits nothing, and prints the movement of every threshold
  per field, old against new, with N.
