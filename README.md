# Manifest

**A document intelligence platform for cross-border trade. Every field traces to a pixel.**

*AWS Textract · Bedrock · Bedrock Data Automation · Lambda · Step Functions · EMR Serverless ·
Iceberg on S3 · Athena · OpenSearch Serverless · Redshift Serverless · SageMaker · Terraform*

> **Manifest** — the trade document, and the word for *made evident*. Both meanings are the
> project.

---

> **Status: applied, verified end to end, and torn down.** `make preflight` runs **36 checks** —
> every claim below, every consistency invariant, `terraform validate` against real provider
> schemas across six layers, checkov at zero findings, and a check that the figures on this page
> are the ones the run produced. All of them pass, in the reader image the recording came from.
>
> **The estate has been deployed, verified end to end, and torn down — twice.** First on
> 2026-08-10 with the escalation tiers off (13 of 13 checks), and again on **2026-08-12 with them
> on: 14 of 14**, including the check that the cascade actually climbs. `scripts/e2e_verify.py`
> puts documents through the running pipeline and asserts what came out, with five edge cases
> that must each be refused by name.
>
> An earlier revision of this block said "not yet deployed" and "neither workflow has been
> dispatched". Both were true when written and false the moment the first apply succeeded, and a
> repository describing a posture it no longer holds is the same defect whichever way the error
> points.
>
> **A managed extraction engine has now been called, and the date matters.** On **2026-08-12** a
> bill of lading published 2 fields at tier 0 and abstained on 7, and those 7 went up to
> **Textract** — CloudTrail records `DetectDocumentText` against the `manifest-escalate` role at
> 06:40:39 EEST. That is the first billed read this project has performed. What it proves is that
> the **routing works**: the abstaining fields were sent up and the confident ones were not.
> It is **not** an accuracy figure for the escalated fraction — one document is not a
> measurement, and *"accuracy held at X for Y% of the cost"* remains unavailable here.
>
> No distributed job has run. There are no screenshots and no wall-clock figures. The one cost
> figure here is labelled **modelled** everywhere it appears and says what it is built from; it
> becomes a measurement when a run produces one, with that run's date beside it, and not before.
>
> Every claim below is scored **offline, with no AWS account**, and stayed that way through the
> deploy. A claim that needs a running estate to check is a claim you cannot reproduce.
> See [`docs/DECISIONS.md`](docs/DECISIONS.md) 14 to 16.

---

## The problem

A customs broker receives commercial documents — bills of lading, invoices, packing lists,
certificates of origin, customs declarations, arrival notices. They arrive as scans of scans:
skewed, stamped over the numbers, in three languages, with tables that break across pages and
handwritten corrections in the margin.

The whole system is built around one boundary:

| Models own | Deterministic code owns |
|---|---|
| Reading characters off a degraded scan | Whether a value is confident enough to publish |
| Proposing which field a token belongs to | Whether the value actually appears where its provenance says it does |
| Proposing a tariff classification | Whether two documents agree |
| Suggesting that two parties are the same entity | Whether a human's decision was recorded, and whether the human was actually looking |

**A published field that cannot be located on a page is a build failure.**

---

## The scoreboard

Produced by `make claims` on a laptop with no AWS account. Every figure is the output of a
command in this repository, not a summary of one.

| check | result |
|---|---|
| **claim 1** · derived thresholds | **4 derived**, 1 always-review by contract, **7 evidence-limited**, 26 quality-limited — with the reason named per field |
| **claim 2** · provenance | honest records **30/40 verified**; corrupted boxes **40/40**, **40/40**, **2/2** refused, each by the layer it should be |
| **claim 3** · reproducibility | **3,000/3,000** documents publish an identical version from the same input; **3,000/3,000** get a new version from a reader change |
| **claim 4** · reconciliation | **116/116** planted disagreements found, **0** false positives on the set that agrees |
| **claim 5** · the human loop | queue capacity **4,320 decisions/day** declared against **119,208** implied — the gate fires, and passes only on a named acceptance that expires 2027-02-09 |
| **claim 6** · reversible identity | 21 surface forms → 13 entities, **0** mixing two parties; un-merge re-points **every** downstream record |
| **claim 7** · scale and cost | first pass 3,000, re-run **0**, resume **exactly** the 1,500 remaining; **0.59 USD per 1,000 pages, modelled** |
| **injection** | **0 false positives** on 2,963 documents of ordinary trade prose; 4/4 planted strings recognised; the envelope refuses a forged delimiter rather than escaping it |
| **line-item totals** | **252/252** deliberately truncated tables caught — 211 by direction, 41 as totals the reader mangled |
| **classification** | 6/6 contested headings abstain; nothing publishes at any score |
| **claim 1 · the loop** | review evidence moves **4 fields out of always-review** — and the error budget is untouched in every one. A 100% agreement rate contributes **0 of 500** observations; two seconds on task contributes **0 of 100** |
| **what it buys** | publishing everything: **19.11% wrong**, 34 declared budgets missed. A hand-picked 0.85: **4.56%**, 25 missed. Derived: **0.41%**, **none missed** — at 19,606 items queued. The queue cost is printed beside the wrong-rate, always |
| **production drift** | the declared envelope fires on a −0.25 shift **and** on a +0.25 one; a 9-document window returns **undecided**, never *inside* |
| **out of distribution** | on 100 pages of **real photographed paper** nobody here designed (CC BY 4.0, `corpus/external/LICENCE.md`): ECE **0.0592** against **0.0371** on the generated corpus. The reader's confidences transport — which is the only answer to *"did you tune the generator until the claims passed?"* that does not come from the generator's author |
| **grounded classification** | a proposal must point at the nomenclature text it came from — claim 2's rule applied to text. 5 of 6 abstentions are **declared** contests, 1 is margin; a heading retrieval never surfaced is refused; nothing publishes at any score |
| `make gate-proof` | **51 refused, 0 accepted, 0 stale** |
| `terraform validate` | **6/6 layers** against real provider schemas |
| `checkov` | **676 passed, 0 findings** across six layers; every exception carries a written reason beside the resource |
| corpus reproduces | **3,000 documents** regenerate byte-identically from one seed |
| test suite | **402 passing**, offline, credential-free |

The last three rows are the ones worth reading first. A suite tells you the code does what it
does; `gate-proof` breaks 51 controls on purpose and requires the **named** gate to refuse
each one, for the right reason.

---

## The finding this project exists to produce

**Every field but four is `always-review`, and the harness says which of two things is wrong.**

`procedure_code` reads **489 of 489 correctly** above 0.9 confidence. Its 95% upper bound on
the error rate is still **0.61%**, against a declared budget of **0.10%**. No threshold fixes
that, because the reader is not wrong — **there is not enough labelled evidence to prove it
right**. Deriving one at that budget needs n≈2,995 at zero observed errors.

That is *evidence-limited*, and it has a different fix from *quality-limited*:
`country_of_origin` is **174 wrong in 536** above 0.9, which is over-confidence, and no amount
of extra data helps. The two have the same symptom — "no threshold" — and opposite answers, and
telling them apart is the most useful thing `evals/calibration/` produces.

It is only visible because the bound is computed properly. A point estimate would have reported
`procedure_code` at 0.0% error and derived a threshold, and the resulting number would have been
a claim about 489 documents dressed up as a claim about the field.

**And then claim 5 bites.** Those thresholds queue 91% of extracted fields — 83× the declared
capacity at the peak. `contracts/review/acceptance.yaml` accepts that overage **by name, with an
expiry**, and every run prints the finding, the cause and the response in full. What is *not*
done is the forbidden move: raising a threshold to reduce queue volume without changing the
error budget, which inverts the derivation and turns a derived number into a chosen one.

---

## The three things that make the claims hard to fake

**The corpus is generated, degraded, and its difficulty is declared.** 500 shipments, 3,000
documents, 3,255 pages in English, Greek and Dutch, rendered to PDF and then skewed, noised,
JPEG-recompressed, stamped over and bled through. `corpus/envelope.yaml` declares the intended
operating range — median confidence, the low-confidence tail, the abstention band per document
type — and a check goes red when the generator drifts out of it, in either direction. A corpus
degraded too gently makes every threshold trivially satisfiable; too hard and every claim is
scored on pages nobody could read. **Both failures report green.**

**A real reader actually runs.** The tier-0 engine is a local OCR binary, run here over the real
degraded corpus, producing genuine confidences and genuine geometry — 3,255 pages, 182,049
words, committed to `recordings/ocr/` with the engine version and a fingerprint. Every threshold
is derived from that recording, never from a live run, because a binary's confidences differ
between versions and a threshold that moved when a runner image changed would make claim 1 red
for reasons unrelated to this repository. `make ocr-record` refuses to overwrite it without
`ACCEPT=1` and prints what changed first.

**Claim 4's planting cannot see what will find it.** `corpus/plant.py` perturbs a *fact about a
shipment* — a weight, a container, a package count — and does not import the contract layer.
`scripts/check_planting_is_blind.py` reads the import graph and fails if it ever does. Without
that, "exactly N found, zero false positives" is one function agreeing with itself, reporting
green forever.

---

## What this repository will not claim

Stated here rather than left to be inferred, because each one is a sentence a reader might
otherwise supply for themselves.

**Claim 2 says a field is *where the record says it is*. It does not say the value is right.**
Layer B re-reads the crop through a different *segmentation* path, not a different classifier —
a `0`/`O` confusion reproduces on both passes. The gate also does **not** catch a box pointing
at an identical string elsewhere on the page, stated in ADR-0003 in advance, with a fixture
whose expected result is *not caught*.

**No accuracy figure exists for the cascade's upper tiers.** Pages have now been sent to tier 1
— seven fields of one document, on 2026-08-12 — and that changes the sentence without changing
the claim: **N=1 is not a measurement.** *"Accuracy held at X for Y% of the cost"* is still
unavailable here and still does not appear. What the run establishes is the *routing*: the
fields that abstained went up and the fields that cleared their thresholds did not. An accuracy
figure needs a labelled corpus put through the tier, with its N and its date, reported separately
from the offline eval rather than merged into it. **Tiers 2 and 3 have still never been called.**

**The cost is a model.** The routing distribution is measured over the recording; the tier-1
unit price is published, cited and dated. **Tier 2 carries no price at all** — it is charged per
token, no call has been made here to count tokens with, and a per-page equivalent invented for
the table would be exactly the fabricated number everything else avoids. Its volume is reported
and its cost is a sensitivity sweep.

**Entity resolution does not bridge scripts.** Four non-Latin surface forms resolve to
themselves. String similarity cannot connect 北方桥货运 to *Northbridge Forwarding B.V.*, and
nothing here pretends to. Near-matches become *candidates* for a human, never merges: reader
damage and two different companies sit in the same similarity band, and no threshold separates
them.

**The AWS fixtures are authored, never captured — and that is now a choice rather than a
circumstance.** Until 2026-08-12 no call had ever been made, so no response could have been
recorded. One has been made since, to Textract, and the fixtures are *still* authored: replacing
one is a deliberate act that changes that fixture's provenance in the same commit, and it has not
been done. What is forbidden either way is an authored fixture presented as captured. See
[`tests/extraction/fixtures/AUTHORED.md`](tests/extraction/fixtures/AUTHORED.md).

**The AI Act finding is narrow on purpose.** No high-risk classification arises — Annex III does
not list customs, and its nearest point is about natural persons crossing a border rather than
cargo. That is **not** "the AI Act does not apply": Art. 4 and Art. 5 bind regardless of risk
class and have since 2 February 2025. [`docs/REGULATORY.md`](docs/REGULATORY.md) carries the
verification date beside every citation.

---

## The finding that changed the architecture

Verified 2026-08-09 and recorded in [`docs/AWS-CONSTRAINTS.md`](docs/AWS-CONSTRAINTS.md):
**Textract, Bedrock Data Automation and Comprehend all document the same six input languages —
English, German, Spanish, French, Italian, Portuguese.** The scenario's documents are in
English, Greek and Dutch.

Two of the three languages, including the language of one of the two offices, are outside every
managed extraction service in the intended stack. That is a coverage problem, not a cost one,
and it makes the local tier-0 reader the *only* reader for those pages rather than the cheap
one. The cascade stops being a quality ladder and becomes routing between engines with
**different competences**, with eligibility declared per language in
[`contracts/cascade/routing.yaml`](contracts/cascade/routing.yaml) — because sending a Greek
page to a service that does not read Greek does not fail loudly. It returns a confident-looking
result over a language the model never saw.

The same session established that **Amazon A2I closed to new customers on 2026-07-30**, so the
review queue is ours to build. That is the better outcome: claim 5 is about a queue's declared
capacity, its failure mode and its integrity metrics, and a managed worker pool supplies none of
them.

---

## What is built, and what is not

The deploy path is complete and the system behind it is close to it. What remains open is
listed rather than left for a reader to find.

| Not built | Consequence, stated |
|---|---|
| **The public dataset** | The out-of-distribution honesty check. Every figure on the scoreboard is scored against a corpus this repository generated; until a real public document set is wired in, the only measurement against non-generated paper is the ISO 6346 check digit, which gives a **lower bound** on the error rate and nothing else |
| **The human decision as a training signal** | A proposal below threshold cannot publish without a recorded decision, and `evals/review` asserts it. The decision *feeding back* is not built: the HS proposer is a similarity ranker over declared headings, not a model that learns, so there is nothing for a decision to train |

Everything else on `PLAN.md` is ticked individually, with a harness behind it and — where it is
a control — a `gate-proof` mutation that breaks it.

Two smaller honesty notes that are not gaps but are easy to over-read:

**The classification figures are not a claim about production accuracy.** They are measured over
twelve headings chosen because this repository's own corpus falls under them. A tariff has five
thousand headings and a real classifier meets goods nobody described in advance. What is proved
is the *gate*: a contested heading abstains, and no score publishes anything.

That now covers a fitted model as well as the similarity ranker. The endpoint serves one, fitted
from 73 hand-written goods descriptions, and its held-out figure is a statement about that file —
it appears on no scoreboard here and the sentence "the classifier is N% accurate" is one this
project does not have. What the fit is worth reading for is that **the abstention gate refused
the first three attempts**: two because the model separated contested pairs the trade does not,
and the fix each time was the training set rather than the band.

**The batch job has not been run yet.** `pipelines/reprocess.py` is written, tested and defaults to a
dry run; claim 7 is proved against the pure planner it calls. The part that needs a cluster is
one function, and keeping it that size is what stops the untested region growing.

---

## Running it

```bash
make install       # venv + editable install
make test          # 348 tests, offline, under a minute
make claims        # every claim gate that exists
make gate-proof    # break 51 controls on purpose; each must be refused, for the right reason
make preflight     # all of it: correctness, consistency, deployability

make corpus        # regenerate 3,000 documents from one seed (~20 minutes)
make ocr-record    # the ceremony — refuses to overwrite without ACCEPT=1
```

Requires Python 3.12+ and, for the corpus, an OCR binary with English, Greek and Dutch language
data. No AWS account, no credentials, no network.

---

## Repository layout

| Path | Purpose |
|---|---|
| [`contracts/`](contracts/) | **The source of truth** — document types, agreement rules, the party model, review capacity, cascade routing. YAML, never imported by name |
| [`corpus/`](corpus/) | The generator, its ground truth, and `envelope.yaml` — the declared operating range |
| [`src/manifest/core/`](src/manifest/core/) | **Pure.** No cloud SDK, no engine, no clock, and no engine named anywhere — enforced by a gate and three mutations |
| [`src/manifest/extraction/`](src/manifest/extraction/) | `local/` runs; `aws/textract.py` has been called against the real service (2026-08-12); `bda.py` and `llm.py` are schema-tested and still uncalled |
| [`src/manifest/handlers/`](src/manifest/handlers/) | The three functions that run in the estate. Each one calls `core` and decides nothing itself |
| [`Dockerfile`](Dockerfile) | The tier-0 reader as an image — the same binary and language data that produced the recording, version asserted at build time |
| [`src/manifest/gates/`](src/manifest/gates/) | The acceptance gates, one per claim |
| [`recordings/ocr/`](recordings/ocr/) | The tier-0 reader's normalised output, with its version and fingerprint. Every threshold derives from here |
| [`evals/`](evals/) | The seven claim harnesses, labelled and credential-free |
| [`infra/`](infra/) | Six Terraform layers. `bootstrap/` **is applied** and stays applied; the other five were applied from `deploy.yml` on 2026-08-10, verified, and destroyed on 2026-08-11 |
| [`docs/adr/`](docs/adr/) | Five decisions, written before the code that assumes them |

---

## Licence

MIT — see [LICENSE](LICENSE). Engineering rules live in [CLAUDE.md](CLAUDE.md); the four phases
in [PLAN.md](PLAN.md).
