# What is left, written 2026-08-14, with the estate torn down

> **Superseded on 2026-08-19, and kept rather than edited.** This is a dated snapshot, in the same
> spirit as `docs/DECISIONS.md`: how a thing looked before it was fixed is the useful half, and a
> file rewritten in place destroys it. What is below was true on 14 August. Six of its statements
> are no longer:
>
> | Then | Now |
> |---|---|
> | 32/32 end to end | **34/34** — `harvest` and `watch` gained checks 26 and 27 |
> | `gold.declaration_line` is empty | **2 rows**, both `human_decided` — the mart joined on a version that could never carry the decision |
> | Only 5 of **40** fields publish | 5 of **36**; the 40 was a count of declarations, not of fields |
> | `gold.published_field` 196 | **106** on the run of 19 August |
> | The teardown left SageMaker's lineage behind | **Fixed** — 116 associations cut, 33 entities removed, and the permission that made it possible was granted on a statement that could not match it |
> | The reviewer-identity retention question is open | **Answered** under Art. 6(1)(f) in `docs/REGULATORY.md`; still not *implemented*, which is in the README's limits |
>
> For the current state, read the README's **What this does not do** and `CHANGELOG.md`. This file
> is history.

Not a backlog. What this repository **showed on a running estate**, and what it did not — the
second half being the part that goes missing when somebody is pleased with the first.

Every item here says what is true, not what is planned. Where a thing is unfinished it says
which fact makes it unfinished, so tomorrow starts by reading rather than by remembering.

---

## Shown on the estate, so none of it is a guess

- **32/32 end to end**, four times over, on a corpus of six document types in three languages.
- **Claim 4, both halves.** `SHP00001` reconciles with zero disagreements; `SHP00002` produces
  exactly the one the generator planted, found by rules that never read the planting.
- **Claim 5 as a loop.** 35 abstentions decided; each approval publishes a superseding version
  that lands in the lake; `manifest-harvest` turned those decisions into **34 observations over
  14 fields**; `scripts/feedback_movement.py` printed every field's N before and after with the
  error budget unchanged beside it, and wrote nothing.
- **Claim 6 reversed.** Two spellings of one party merged, the merge undone, every reference
  re-pointed.
- **Claim 7 idempotent, in three runs.** `process 23` → `20 published, 3 refused` →
  `skip 20, process 3` → `skip 23, nothing to do`. A reader upgrade plans all 23 again.
- **The drift watch found real drift.** `abstention_rate 0.638` against a declared band of
  `[0.002, 0.20]`, delivered to the alerts topic. It is right, and it is the same fact claim 1
  reports from the other side: 30 of 40 fields are quality-limited and 5 publish.
- **The warehouse holds rows.** `gold.published_field` 196, `gold.page_read` 29,
  `gold.review_item` 125 with 12 carrying a recorded decision.

---

## 1. `gold.declaration_line` is empty, and the reason is now one field

This was *"the classifier produces proposals nobody decided"* and it no longer is: `hs_code` is
decided, published and in the lake. The line is refused because a declaration line needs five
fields on one version and **`declaration_date` abstained and nobody decided it**.

That is the loader working — a declaration without its date is not a declaration line, and
defaulting a date is doctrine rule 3 — and it is a gap in the *harness*, which approves the
fields the reconciliation rules compare, the party names and the classification, and not the
ones a duty line needs.

**To close it:** add the declaration line's fields to what `_approve_what_the_shipment_checks_need`
decides, exactly as `_classification_fields()` was added today. One deploy to verify, one to load.

## 2. Only 5 of 40 fields publish, and that is the ceiling on everything else

`make claims` says it plainly: 5 thresholds derived, 1 always-review by contract, 4
evidence-limited, **30 quality-limited**. The drift watch reports the same thing as a 63.8%
abstention rate against a 20% band.

Quality-limited means real errors survive at high confidence — over-confidence rather than thin
evidence — and the answer is a better reader on those pages, which is what the cascade is for.
Tier 1 cannot help yet because **no threshold is derived from Textract's confidences**.

**To close it:** a recording of Textract over the same labelled set, in `recordings/textract/`,
and thresholds derived from it by the apparatus that already exists for tier 0. Cost it first —
it is a few thousand billed pages — and it is the first place this repository could honestly
write *measured* about a billed engine, with an N and a date.

## 3. The reviewer's identity is in an analytics table and the retention question is open

`review_queue_economics` groups by `reviewer` and must: doctrine rule 2 is about *a* reviewer at
100%, which an aggregate cannot see. What is owed is a retention class for an operator identity
in `docs/REGULATORY.md`. `contracts/analytics/acceptance.yaml` names the question and does not
answer it.

## 4. Tier 2 reports a confidence and nothing is derived from it

Document automation returns a confidence on every word, 0.729 to 1.0, and may not publish — not
because there is no number but because no threshold here comes from that number. Same shape as
item 2, and the same apparatus would close it. Note its **line**-level confidence is ~0.01 on
lines whose words score above 0.99, so whatever that field measures, it is not the probability
that the line is right.

## 5. Lake rows that predate the schema are excluded from every load

Written before `document_type`, `language`, `reader_tier` and `published` were columns. The
loader excludes them and says how many. Still in the lake, which is the record. A backfill, or a
deliberate decision to leave them, is small work that should not be silent.

## 6. The corpus-skip acceptance is still in force, by its own terms

`contracts/ci/acceptance.yaml` lets the deploy gate skip the corpus job. Its `ends_when` is *a
cycle brought up, verified and torn down without a defect being found* — and today found eleven,
so it still holds honestly. **The next clean cycle is when it goes.**

## 7. What the deploy still cannot tell you

`check_plan_destroys.py` refuses a plan that **deletes**; it does not refuse one that **replaces**
a data-bearing resource, and a replaced collection loses its contents. The control for that is
`prevent_destroy` on the resources that hold data, and nothing has it.

## 8 · The teardown left SageMaker's lineage behind, and the sweep was right to fail

All five layers destroyed cleanly. `scripts/estate_sweep.py` then failed, and it should have:
SageMaker writes **lineage entities** — 50 actions, 50 contexts, artifacts — automatically when
an endpoint is deployed, and deletes none of them when the endpoint goes. One log group,
`/aws/sagemaker/Endpoints/manifest-hs-classifier`, survives with them.

They are free, and that is not the point. *A create path with no delete path is how an estate
gets left standing* is this repository's own sentence, and it applies to a resource created
indirectly exactly as it applies to the Bedrock Data Automation project `destroy.yml` already
deletes by hand.

**To close it:** the same shape as the BDA step — list by prefix, delete, and exit zero when
there is nothing named to remove.

**Verified by CLI after the run:** 0 Lambda functions, 0 state machines, 0 DynamoDB tables, 0
queues, 0 OpenSearch collections, 0 Redshift workgroups, 0 EMR applications, 0 SageMaker
endpoints, 0 VPCs, 0 ECR repositories, 0 NAT gateways. The one surviving VPC endpoint belongs to
**Attestor**, not to this project, and is a free S3 gateway endpoint. The only Manifest buckets
left are `manifest-tfstate` and its access log — bootstrap, which is deliberately never
destroyed.

---

## What today changed, and what it cost

Three promises the repository made and did not keep are now kept: `core/feedback.py`,
`core/drift.py` and `core/lineitems.py` had no caller in the running system — proved offline,
never asked — and `scripts/check_the_map_matches_the_ground.py` now fails CI when a fourth
appears. `CLAUDE.md`'s own tree named four packages that do not exist.

**Eleven defects were found and fixed**, and the ratio is worth keeping: five were caught by this
repository's own offline checks before anything was deployed — a function calling SNS with no VPC
endpoint, a layer that stopped being self-contained, a variable passed invisibly, a shell
variable nobody assigned, a warehouse table the column check could not read — and six needed the
estate to say no: an enum member written from memory, a property passed to `replace`, an
immutable image tag on a re-deploy of the same commit, the same tag rule in a second image the
first fix did not touch, a review marker accumulating in the identity thresholds are keyed by,
and a join on a version that can never carry the decision.

Two of those are the same mistake made twice — **a name written from memory instead of read** —
and one is `docs/DECISIONS.md` 24 in its purest form: a fix applied where the symptom appeared
rather than to every place with that shape, when there were exactly two.
