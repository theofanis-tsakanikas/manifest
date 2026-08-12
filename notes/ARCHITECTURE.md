# Manifest — what it does, and where each service sits

*Written 2026-08-11, after the first estate was deployed, verified end to end and torn down.
Its purpose is to agree the shape **before** the upper tiers of the cascade are built.*

---

## In a paragraph

A customs broker receives commercial documents — bills of lading, invoices, packing lists,
certificates of origin, customs declarations, arrival notices — as scans of scans: skewed,
stamped over the numbers, in three languages, with tables that break across pages. Manifest
turns each one into **a structured record where every published field points at a rectangle on
a page**, checks that the documents in a shipment **agree with each other**, and proposes a
**tariff classification** that a human decides. The engineering claim is not that the reading is
accurate. It is that the system knows what it does not know: a field whose confidence falls
below a threshold *derived from that field's declared error budget* is never published, it is
sent to a person — and the queue that receives it has a declared capacity, so a system that
abstains too often fails its own build rather than quietly drowning its reviewers.

---

## 1 · What happens to one document

```mermaid
flowchart TB
    A["📄 arrives · S3 landing<br/><i>incoming/{lang}/{type}/{id}.pdf</i>"] --> B{"prefix<br/>matches?"}
    B -- no --> BX["nothing runs<br/><i>the cheapest refusal</i>"]
    B -- yes --> D["**Read** · tier 0<br/>OCR in a Lambda container<br/><i>words · scores · boxes</i>"]

    D -- error --> RF["**ReadingFailed**<br/><i>a corrupt file<br/>or a missing language</i>"]
    D --> E["**Extract & threshold**<br/><i>contracts say which fields exist<br/>recordings say the thresholds</i>"]
    E -- error --> EF["**ExtractionFailed**<br/><i>a contract or an artefact<br/>that does not match</i>"]
    E --> F{"any field above<br/>its threshold?"}

    F -- no --> Q["**Review queue** · SQS<br/><i>a human decides</i>"]
    F -- yes --> G["**Provenance gate**<br/><i>re-crop the box · re-read it<br/>check the digit arithmetic</i>"]

    G --> H{"every published<br/>field verified?"}
    H -- no --> Q
    H -- yes --> I{"anything<br/>abstained?"}

    I -- yes --> Q2["queue the abstentions"]
    Q2 --> J["**Publish** · S3 records<br/><i>keyed by document + version</i>"]
    I -- no --> J
    Q2 -.-> Q
    J --> K["Iceberg · Glue · Athena<br/>OpenSearch · Redshift marts"]

    style D fill:#e8f4ff
    style G fill:#fff4e6
    style Q fill:#ffe8e8
    style J fill:#e8ffe8
    style RF fill:#f0e0e0
    style EF fill:#f0e0e0
```

**Three things worth noticing.** A document can publish *and* queue at the same time — eight
fields to the record, four to a human — and getting that wrong is how the estate first shipped.
And nothing reaches the record without passing the provenance gate, which checks the box against
the **page**, not against the record that produced it.

---

## 2 · The boundary the whole system is built around

```mermaid
flowchart LR
    subgraph M["🤖 Models propose"]
        M1["read characters off<br/>a degraded scan"]
        M2["which field a<br/>token belongs to"]
        M3["a tariff<br/>classification"]
        M4["two parties might<br/>be the same entity"]
    end

    subgraph D["⚖️ Deterministic code decides"]
        D1["whether a value is<br/>confident enough to publish"]
        D2["whether the value is<br/>actually where it claims"]
        D3["whether two documents<br/>agree"]
        D4["whether a human decided,<br/>and whether they were looking"]
    end

    M1 --> D1
    M2 --> D2
    M3 --> D4
    M4 --> D3

    style M fill:#f0f0ff
    style D fill:#f0fff0
```

The four pairs are **a sample, not the list**: `src/manifest/core/` holds sixteen modules and
every one of them sits in the right-hand column. The cleanest example is not in the diagram —
`checkdigit.py`, which refuses an ISO 6346 container number that disagrees with **its own
arithmetic**. No confidence, no threshold: either the check digit works out or it does not.

Everything on the right is pure Python in `src/manifest/core/`, importing no cloud SDK and no
model library. That is what lets every claim be checked on a laptop with no AWS account — and
it is why swapping Textract for a local reader is an adapter change and nothing else.

---

## 3 · The cascade — and the fact that decides its economics

```mermaid
flowchart LR
    P["page"] --> T0["**Tier 0** · local OCR<br/>every language · €0<br/>✅ reports a score"]
    T0 --> Q0{"below the<br/>threshold?"}
    Q0 -- no --> PUB["publish"]
    Q0 -- yes --> LANG{"language?"}

    LANG -- "en de es fr it pt" --> T1["**Tier 1** · Textract<br/>degraded print, tables<br/>✅ reports a score"]
    LANG -- "el · nl" --> T3

    T1 --> Q1{"still<br/>below?"}
    Q1 -- no --> PUB
    Q1 -- yes --> T2["**Tier 2** · BDA<br/>reading order, tables<br/>❌ no score, anywhere"]
    T2 --> T3["**Tier 3** · Bedrock<br/>the only path for el · nl<br/>❌ no score, and refuses<br/>to invent one"]
    T2 --> HU
    T3 --> HU["**human**<br/>Reason.UNSCORED"]

    style T0 fill:#e8ffe8
    style T1 fill:#e8f4ff
    style T2 fill:#fff0e6
    style T3 fill:#ffe8e8
```

**Only tiers 0 and 1 report a confidence.** So escalating past tier 1 is not "buying a better
reading" — it is **a decision to spend a human**: the page gets read better and arrives with no
score to publish it on. That is not a defect in the routing; it is what those services are, and
it is why the review capacity model is costed against it rather than against an assumption.

**And Greek and Dutch skip tiers 1 and 2 entirely.** Textract, Bedrock Data Automation and
Comprehend all document the same six input languages, and neither Greek nor Dutch is among them
(`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09). For two of this scenario's three languages,
the local reader is not the cheap option — **it is the only one**.

---

## 4 · Where the AWS services actually sit

| Service | Its job | Status today |
|---|---|---|
| **S3** | landing zone, records, renders, Iceberg lake | ✅ running |
| **Lambda** (container) | the tier-0 reader and the provenance gate | ✅ running |
| **Step Functions** | one execution per document; the escalation lives here | ✅ running, **escalation states dispatched 2026-08-12** |
| **SQS + DynamoDB** | the review queue and the recorded decisions | ✅ running |
| **ECR** | the reader image — 3.7 GB, two functions run *from* it | ✅ running |
| **Textract** | tier 1, for the six documented languages | ✅ **called 2026-08-12** — 7 fields of 1 document |
| **Bedrock Data Automation** | tier 2, reading order and tables | ✅ **wired**, ⚠️ never called — no language routes to it |
| **Bedrock** | tier 3, the only escalation for Greek and Dutch | ✅ **wired**, ⚠️ never called |
| **Glue · Athena · Iceberg** | the record lake and its catalogue | ⚠️ created, **never fed** |
| **OpenSearch** | search over *published records*, never raw document text | ⚠️ created, **never fed** |
| **Redshift** | duty exposure by HS chapter, queue economics, cost per client | ⚠️ opt-in; **6 of 55 mart columns have a source** |
| **EMR Serverless** | bulk re-extraction when the reader improves | ⚠️ opt-in, never applied |
| **SageMaker** | opt-in only | ⚠️ never applied |

---

## 5 · What was proved, and what was not

**Proved on the deployed estate, 2026-08-12**, by `scripts/e2e_verify.py` — **14 of 14 checks**,
with the escalation tiers on. A bill of lading arrived, was read into 53 words with genuine
confidences from 0.179 to 0.969 and a box for each, two fields cleared their thresholds and
seven abstained, **those seven went up to Textract**, the gate verified both published fields
**by cropping the page and reading it again**, the record was written keyed by document and
version, and all seven abstentions reached the queue. Five edge cases were refused by name.

The run on 2026-08-11 was the same thing with the tiers off, 13 of 13. Two of the five edge
cases now pass a stricter assertion than they did then: *"the execution reached a terminal
state"* is every state an execution can reach, so a document that sailed through and published a
record passed under the name of the check written to catch it. They assert the absence of a
published record instead.

**And the warehouse is a schema over nothing.** The lakehouse and the analytics layer both
applied cleanly; neither has ever held a row. Nothing writes the Iceberg table — the pipeline
publishes JSON records to S3 and no step converts them. Of the 55 columns the four marts read,
**6 exist in the lake, 15 are derivable from it, and 34 have no source anywhere** — carrier and
client id are commercial facts a document pipeline never sees; the review loop's decisions live
in DynamoDB and nothing exports them. `scripts/check_warehouse_is_fed.py` holds that count and
`contracts/analytics/acceptance.yaml` names every one, because a mart reading an invented column
returns a number with the shape of an answer.

**Not proved, and the reason is the same for all of it:** no managed extraction engine has ever
been called. There is no accuracy figure for the escalated fraction and there cannot be one
until a page goes up a tier. No distributed job has run. Every cost figure is **modelled** from
published prices, never measured.

---

## 6 · What we build next, and why it is the point

```mermaid
flowchart LR
    N1["**Escalation state**<br/>in the state machine"] --> N2["route on lowest_confidence<br/>+ language eligibility"]
    N2 --> N3["call Textract · BDA · Bedrock<br/><i>the first billed calls this project makes</i>"]
    N3 --> N4["a page that escalates,<br/>with a date and an N"]
    N4 --> N5["**the cascade stops being<br/>a design and becomes a measurement**"]

    R1["**Redshift**<br/>apply the analytics layer"] --> R2["duty exposure by HS chapter<br/>queue economics<br/>cost per client"]

    style N3 fill:#ffe8e8
    style N5 fill:#e8ffe8
```

**As of 2026-08-11 the escalation is written and unrun.** `src/manifest/handlers/escalate.py`
routes each abstaining field through `core.cascade.route`, calls the tier that field's language
makes eligible, and re-thresholds only what came back with a score. It is off by default behind
`enable_escalation_tiers`: with the flag down the states are not in the machine, the function
does not exist, and no grant to Textract or Bedrock exists anywhere in the account.

**Superseded on 2026-08-12, which is why the paragraph above is left standing rather than
rewritten.** It said the sentence worth having was *"a page escalated on this date"*, and that
the distance to it was a few cents of Textract. It was — and the few cents bought four defects
that no offline check had found, each of which would have made the escalation silently useless:
`publish` returned an outcome with no language, `escalate` read the tier-0 pointer one level
short and handed S3 an empty bucket name, its execution role could not attach to the VPC, and a
`Box` is not iterable.

A page escalated on **2026-08-12**: seven fields of one bill of lading, to tier 1, recorded in
CloudTrail as `DetectDocumentText` against the `manifest-escalate` role at 06:40:39 EEST. What
that buys is the *routing* — the seven that abstained went up, the two that cleared their
thresholds did not. What it does not buy is the other half of the sentence: **there is still no
"here is what it cost"**, because two calls are not a bill, and no accuracy figure, because one
document is not a measurement.

Redshift is the same argument for the analytics half: `contracts/` declares the marts and
`scripts/check_marts.py` proves they only read columns the warehouse has, offline. Standing the
workgroup up for an hour turns that from a design into an answered question — and it is cheaper than this note first claimed: an idle workgroup bills no
compute at all (`docs/AWS-CONSTRAINTS.md`, read 2026-08-11). Eight RPUs for ten minutes of
marts is well under a euro. What does bill continuously is a transaction nobody closed.
