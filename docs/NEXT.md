# What is left, as of 2026-08-14, with the estate standing

Not a backlog. A list of what this repository has **shown on a running estate**, and what it has
not — kept because the second half is the part that goes missing when somebody is pleased with
the first.

The estate is up: five layers, all optional features on. Everything below is either verified
against it and says so, or is named as unverified and says why.

---

## Shown on the estate, so none of it is a guess

- **32/32 end to end**, including the five checks that had no estate path at all until today:
  six document types through the pipeline, a two-page document read on both pages, a
  check-digit refusal, cross-document reconciliation, entity resolution with an un-merge, and a
  recorded human decision publishing a superseding version.
- **Claim 4, both halves.** `SHP00001` reconciles with **zero** disagreements; `SHP00002`
  produces **exactly** the one the generator planted, found by rules that never read the
  planting.
- **Claim 5 is a loop, not a photograph.** 33 abstentions decided by a reviewer, each one
  publishing a new version that supersedes rather than edits — and `gold.review_item` now holds
  70 rows with 11 carrying a recorded decision, so `review_queue_economics` has a numerator.
- **Claim 6 reversed.** Two spellings of one party merged; the merge undone with every
  reference re-pointed and the removed entity kept as lineage.
- **Claim 7 idempotent, in three runs.** `process 23` → `20 published, 3 refused` →
  `skip 20, process 3` → `0 published, 3 refused` → `skip 23, nothing to do`. A reader upgrade
  plans all 23 again, which is the property a refusal must not outlive.
- **All four cascade tiers**, with tier 3 called on a genuine Greek page for 7 fields, and
  nothing published on a tier that reports no confidence.
- **The warehouse loads and three marts return rows.** `gold.published_field` 89,
  `gold.page_read` 14, `gold.review_item` 70. `gold.declaration_line` is 0 **deliberately**, and
  the loader prints why.

---

## 1. `gold.declaration_line` has no source, and that is a scope decision nobody has made

`duty_exposure_by_chapter` reads a table the loader leaves empty: a declaration line needs an HS
code with a **declared value**, and this system produces classification *proposals* that no human
has decided. Loading a proposal as a declaration would put a number nobody approved into a duty
figure.

The document review loop now records decisions; the *classification* one does not.
`handlers/classify.py` writes every proposal to the evidence bucket and nothing reads them.

**The decision to make** is scope: either a classification decision path is built — the same
shape as `handlers/decide.py`, against a proposal rather than a field — or the mart is declared
as answering a question this system lacks the inputs for. `contracts/analytics/acceptance.yaml`
is where it gets written down either way.

## 2. The reviewer's identity is in an analytics table and the retention question is unanswered

`review_queue_economics` groups by `reviewer`, and it must: doctrine rule 2 is about *a*
reviewer whose agreement rate is 100%, and an aggregate over everybody cannot see one. So the
column is loaded.

What is owed is a retention class for an operator identity in `docs/REGULATORY.md`. The
acceptance names the question rather than answering it, which is honest and is not finished.

## 3. Tier 2 reports a confidence and no threshold is derived from it

`contracts/cascade/routing.yaml` is corrected — document automation returns a confidence on
every word, 0.729 to 1.0 — and tier 2 still may not publish, *not because there is no number but
because no threshold here is derived from that number*.

Deriving one needs a recording of tier 2 over the labelled set, the way `recordings/ocr/` holds
tier 0's. That is claim 1's whole apparatus pointed at a billed engine: worth doing, worth
costing first. Note its **line**-level confidence is ~0.01 on lines whose words score above
0.99, so whatever that field measures, it is not the probability that the line is right.

## 4. Lake rows that predate the schema are excluded from every load

They were written before `document_type`, `language`, `reader_tier` and `published` were
columns. The loader excludes them and says how many. Still in the lake, which is the record. A
backfill, or a deliberate decision to leave them, is small work that should not be silent.

## 5. The corpus-skip acceptance is still in force, by its own terms

`contracts/ci/acceptance.yaml` lets the deploy gate skip the corpus job. Its `ends_when` is *the
estate has been brought up, verified end to end and torn down without a defect being found in
the process* — and defects were found in this one, so it still holds honestly. **The next clean
cycle is when it goes**: delete the `with: run_corpus` block from `deploy.yml` and delete the
file. It is the one temporary thing in the repository.

## 6. What the deploy still cannot tell you

The `accept_destroys` guard refuses a plan that **deletes**. It does not refuse a plan that
**replaces** a data-bearing resource, and a replaced collection loses its contents. The scope is
stated in `scripts/check_plan_destroys.py`; the control for the rest is `prevent_destroy` on the
resources that hold data, and nothing has it yet.
