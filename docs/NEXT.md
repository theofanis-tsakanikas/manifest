# What is left, written down on 2026-08-13 before the estate was torn down

Not a backlog. A list of the things this session **proved were missing** and did not finish, and
the things it finished whose *verification* the teardown interrupted. Everything here is
actionable from a cold start: each item says what is true today, what is not, and where to look.

The estate was destroyed at the end of the session. Nothing below needs it standing to begin —
the code changes are offline, and only the last section needs a deploy.

---

## Verified on the estate before the teardown, so nothing here is a guess

- **22/22 end to end**, including the two checks written this session: a genuine Greek page
  reaches the model tier, and the same bytes sent twice land nothing new.
- **All four cascade tiers called on one document**: `0 → 1 (Textract) → 2 (Bedrock Data
  Automation) → 3 (the model)`, with `reports_confidence` recorded per round. The document then
  published, landed and was indexed.
- **The warehouse holds rows from the lake**, and three marts return them:
  `quality_by_source` 1 row, `modelled_cost_per_client` 1 row, `review_queue_economics` 7 queued
  and 0 decided. The columns nothing produces come back NULL, visibly.
- **The bulk job ran twice**: 13 planned and 10 completed, then `skip 10, process 3`.

---

## 1. The last fix is committed and was never seen working

`core.lake` read `reader_tier` where `handlers/escalate.py` writes `tier`, so every rescued field
landed at tier 0 and `modelled_cost_per_client` reported **€0.00** for a document that had paid
for three billed reads. Fixed, tested offline, committed — and the teardown happened before a
document went through with the fix deployed.

**To close it:** deploy, send one English document, load the warehouse, and check that
`modelled_cost_per_client` reports tiers 1, 2 and 3 with a non-zero modelled cost.

## 2. `gold.declaration_line` has no source and the mart over it answers nothing

`duty_exposure_by_chapter` reads a table the loader deliberately leaves empty, and prints why: a
declaration line needs an HS code with a **declared value**, and this system produces
classification *proposals* that no human has decided. Loading a proposal as a declaration would
put a number nobody approved into a duty figure.

**The decision to make** is scope, not code: either the review path records a human decision that
the loader can join on, or the mart is declared as answering a question this system does not have
the inputs for. `contracts/analytics/acceptance.yaml` is where that gets written down either way.

## 3. `gold.review_item` has rows and no decisions, which is claim 5's denominator missing

The abstentions load; `reviewer`, `decision`, `seconds_on_task` and `agreed_with_model` are all
NULL because no human has decided anything on this estate. Doctrine rule 2 — *a human decision is
only evidence if the human was looking* — is measured offline in `evals/review/` against a
generated queue and has never been measured against a real one.

`handlers/classify.py` already writes every proposal to the evidence bucket for exactly this
reason, and **nothing reads those either**. Both halves of the comparison have to exist before the
agreement rate means anything.

## 4. 81 lake rows predate the schema and are excluded from every load

They were written before `document_type`, `language`, `reader_tier` and `published` were columns.
The loader excludes them and says how many, which is honest and is not permanent: they are still
in the lake, which is the record. A backfill, or a deliberate decision to leave them, is a small
piece of work that should not be silent.

## 5. The corpus-skip acceptance is still in force

`contracts/ci/acceptance.yaml` lets the deploy gate skip the corpus job. It was a time-saving
measure taken deliberately, it is dated `2026-08-12`, it expires `2026-09-12`, and its
`ends_when` says exactly how to remove it: delete the `with: run_corpus` block from `deploy.yml`
and delete the file. **This is the one temporary thing in the repository and it should go first.**

## 6. Tier 2 reports a confidence, and the contract now says what to do about it

`contracts/cascade/routing.yaml` used to declare that document automation "reports no confidence
anywhere, verified against the published standard-output schema". The first real call returned a
confidence on **every word**, varying 0.729 to 1.0. The contract is corrected and tier 2 still may
not publish — *not because there is no number, but because no threshold here is derived from that
number*.

**The open question** is whether to derive one. It would need a recording of tier 2 over the
labelled set, the way `recordings/ocr/` holds tier 0's, and that is claim 1's whole apparatus
pointed at a billed engine. Worth doing, worth costing first. Note also that its **line**-level
confidence is ~0.01 on lines whose words score above 0.99, so whatever that field measures it is
not the probability that the line is right.

## 7. Two smaller things

- **The `search_principals` variable** allows extra read-only principals on the index and nothing
  supplies one; a person wanting to query the collection has to be named. Fine as it is, worth a
  line in the README if search is ever demonstrated.
- **`docs/DECISIONS.md` 24** — *the failure this project produces most is a check reading the
  wrong thing* — gained five more instances today, three of them inside `check_deploy_path.py`.
  The two habits it names (parse the thing rather than matching its shape; give an exemption
  something that notices when its reason expires) are worth applying to the checks that have not
  been touched yet.
