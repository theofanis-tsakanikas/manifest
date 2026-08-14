# Manifest — where it stands

*2026-08-14. Written after the estate was brought up, verified end to end and torn down. Every
figure below was produced by a command in this repository, and the second half of the document
is the part that is not finished.*

---

## 1 · What the system is

A customs broker receives six kinds of commercial document as scans of scans — skewed, stamped
over the numbers, in three languages, with tables that break across pages. Three things come
out, and the third is the one nobody demonstrates.

```mermaid
flowchart LR
  DOC["Six document types<br/>EN · EL · NL"] --> SYS["Manifest"]
  SYS --> R1["A structured record<br/>every field traced to a pixel"]
  SYS --> R2["Agreement across documents<br/>the disagreement is the signal"]
  SYS --> R3["A tariff proposal<br/>always decided by a human"]
  SYS --> R4["Re-extraction at scale<br/>when the reader improves"]
  style R4 fill:#fff4e5,stroke:#e6a23c
```

The boundary the whole design rests on: **models read, deterministic code decides.**

```mermaid
flowchart TB
  subgraph M["Models propose"]
    M1["read characters off<br/>a degraded scan"]
    M2["suggest which field<br/>a token belongs to"]
    M3["propose a tariff class"]
  end
  subgraph C["Code decides"]
    C1["is it confident enough<br/>to publish"]
    C2["is the value where its<br/>provenance says it is"]
    C3["do two documents agree"]
    C4["was the human<br/>actually looking"]
  end
  M --> C
  style C fill:#eef6ff,stroke:#4a7fb5
```

---

## 2 · What runs, and where

Thirteen functions in the estate, six Terraform layers, one pure core that imports no cloud SDK
and names no engine. A document's whole path:

```mermaid
flowchart TB
  UP["document arrives<br/>in the landing bucket"] --> T0["read_tier0<br/>local OCR, free, deterministic"]
  T0 --> PUB["publish<br/>threshold per field, from a<br/>committed recording"]
  PUB --> ESC{"anything<br/>abstained?"}
  ESC -->|yes| CAS["escalate<br/>tier 1 Textract → 2 BDA → 3 model"]
  CAS --> PUB
  ESC -->|no more| ANY{"anything<br/>publishable?"}
  ANY -->|yes| GATE["provenance_gate<br/>ink · re-read · check digit"]
  ANY -->|no| ABS["publish the record anyway<br/>stating that all abstained"]
  GATE --> REC["records bucket<br/>versioned, never overwritten"]
  ABS --> REC
  REC --> LAKE["land → Iceberg"]
  REC --> Q["review queue<br/>declared finite capacity"]
  Q --> DEC["decide<br/>a human, recorded"]
  DEC --> REC
  style Q fill:#fff4e5,stroke:#e6a23c
  style DEC fill:#e8f6ec,stroke:#3f9153
```

Everything downstream reads the records bucket, never the reader:

```mermaid
flowchart LR
  REC["records bucket"] --> LAKE["Iceberg on S3"]
  REC --> IDX["OpenSearch<br/>published values only"]
  LAKE --> WH["Redshift<br/>four marts"]
  DEC["decisions<br/>DynamoDB"] --> WH
  DEC --> HARV["harvest<br/>decisions → observations"]
  REC --> WATCH["watch<br/>daily, against the<br/>declared envelope"]
  LAKE --> BULK["EMR Serverless<br/>bulk re-extraction"]
```

---

## 3 · The seven claims, and what each one showed

```mermaid
flowchart LR
  C1["1 · thresholds derived<br/>from error budgets"]:::part
  C2["2 · every field traces<br/>to a pixel"]:::done
  C3["3 · re-extraction<br/>reproducible, versioned"]:::done
  C4["4 · disagreement surfaced<br/>never smoothed"]:::done
  C5["5 · the human loop<br/>real and measured"]:::done
  C6["6 · entity resolution<br/>reversible"]:::done
  C7["7 · bulk reprocessing<br/>idempotent"]:::done
  classDef done fill:#e8f6ec,stroke:#3f9153
  classDef part fill:#fff4e5,stroke:#e6a23c
```

| # | What was shown, on the estate |
|---|---|
| **1** | 5 thresholds derived, 1 always-review by contract, **4 evidence-limited, 30 quality-limited** — every figure with its N. The one claim that is only half green, and §5 says why |
| **2** | Every published box carries ink; a crop re-read agrees; a check digit that contradicts itself refuses. 5 container numbers recomputed, none provably wrong |
| **3** | Same bytes twice land nothing new. A correction writes a **new version beside the old**, both retrievable, the lake recording which replaced which |
| **4** | `SHP00001` **zero** disagreements · `SHP00002` **exactly the one** the generator planted, found by rules that never read the planting |
| **5** | 35 abstentions decided; each approval publishes a superseding version; the decisions reach the warehouse where the agreement rate is computed |
| **6** | Two spellings of one party merged, the merge undone, **every reference re-pointed** |
| **7** | `process 23` → `20 published, 3 refused` → `skip 20, process 3` → `skip 23, nothing to do` |

**Claim 1 is a loop, not a photograph**, and today it closed:

```mermaid
flowchart LR
  A["a reviewer corrects<br/>a field"] --> B["decide<br/>records it, with the<br/>confidence at queue time"]
  B --> C["harvest<br/>34 observations, 14 fields"]
  C --> D["N grows"]
  D --> E["derive(budget)"]
  E --> F["threshold"]
  X["error budget"] -.->|"declared, from the domain"| E
  A -.->|"NO ARROW"| X
  linkStyle 5 stroke:#c0392b,stroke-dasharray:4
  style X fill:#fdecea,stroke:#c0392b
```

The arrow that does not exist is the whole safety argument: corrections move **N**, never a
budget. `gate-proof` plants that arrow and requires the named gate to refuse it.

---

## 4 · What the offline evidence is

Provable on a laptop, with no AWS account and no credentials:

| Command | Result |
|---|---|
| `make test` | **498 passing**, under a minute |
| `make gate-proof` | **54 refused, 0 accepted, 0 stale** — each gate broken on purpose, the *named* gate must refuse |
| `checkov` | **718 passed, 0 findings** across six layers |
| `make claims` | every contract, the pure core, the corpus envelope, and the seven claim gates |
| `scripts/e2e_verify.py` | **32/32** against a live estate |

---

## 5 · What is **not** done

This is the half that matters. Each item names the fact that makes it unfinished.

```mermaid
flowchart TB
  G1["Only 5 of 40 fields publish"]:::big
  G1 --> G1a["30 fields quality-limited:<br/>real errors survive at high confidence"]
  G1a --> G1b["the cascade is the answer —<br/>and tier 1 cannot help yet"]
  G1b --> G1c["NEEDED: a Textract recording over<br/>the same labelled set, and thresholds<br/>derived from it"]
  style G1c fill:#eef6ff,stroke:#4a7fb5
  classDef big fill:#fdecea,stroke:#c0392b
```

**1 · The publish rate is the ceiling on everything else.** The drift watch reports the same
fact from the other side: a 63.8% abstention rate against a declared band of 20%. Not a bug —
the system telling the truth about itself. Closing it means deriving thresholds for a billed
tier, by the apparatus that already exists for tier 0. Cost it first; it would also be the first
place this repository could honestly write *measured* about a billed engine, with an N and a date.

**2 · `gold.declaration_line` is empty, and it is now one field.** This used to be *"the
classifier produces proposals nobody decided"*; `hs_code` is now decided, published and in the
lake. The line is refused because a declaration line needs five fields on one version and
`declaration_date` abstained and nobody decided it. The loader is right to refuse a half line;
the harness simply does not decide that field yet.

**3 · No accuracy figure exists for any billed tier, and none may be invented.** What the
cascade proves is two things and stops: the routing sends low-confidence pages up, and the pages
it *keeps* at tier 0 meet their error budgets. Cost is a **model** — measured routing multiplied
by published prices — and says so wherever it appears.

**4 · Scale is asserted, not demonstrated.** The planner is pure and proved on a laptop, which
is where that proof belongs. The cluster ran **23 documents**, not four million. No load test, no
concurrency, no failure injection.

**5 · The reviewer's identity is in an analytics table and its retention class is unwritten.**
The question is named in the acceptance and not answered.

**6 · The corpus is synthetic**, declared, with its operating envelope in `corpus/envelope.yaml`
and a test that goes red when the generator leaves it. Every figure here is a statement about a
distribution this repository authored, and says so.

---

## 6 · What today cost, and what it bought

Three promises the repository made and did not keep: `core/feedback.py`, `core/drift.py` and
`core/lineitems.py` had **no caller in the running system** — proved offline, never asked. All
three now run, and a new check fails CI when a fourth appears.

Eleven defects were found and fixed. The ratio is the interesting part:

```mermaid
flowchart LR
  A["5 caught by this repository's<br/>own checks, before any deploy"]:::good
  B["6 needed the estate<br/>to say no"]:::warn
  classDef good fill:#e8f6ec,stroke:#3f9153
  classDef warn fill:#fff4e5,stroke:#e6a23c
```

Caught offline: a function calling SNS with no VPC endpoint (it would have hung, not failed); a
Terraform layer that stopped being self-contained; a variable passed invisibly; a shell variable
nobody assigned; a warehouse table the column check could not read — and it said so instead of
passing.

Found by the estate: an enum member written from memory; a property passed to `replace`; an
immutable image tag on a re-deploy of the same commit; **the same tag rule in a second image the
first fix did not touch**; a review marker accumulating in the identity thresholds are keyed by;
and a join on a version that can never carry the decision.

Two of those are one mistake made twice — a name written from memory instead of read — and one
is the repository's own decision 24 in its purest form: a fix applied where the symptom appeared
rather than to every place with that shape, when there were exactly two.

---

## 7 · Where it goes

```mermaid
flowchart LR
  N1["tomorrow<br/>decide declaration_date,<br/>the duty mart answers"]:::near
  N2["next<br/>a Textract recording,<br/>tier 1 thresholds"]:::mid
  N3["then<br/>the publish rate rises and<br/>the queue becomes servable"]:::far
  N4["and only then<br/>a measured cost figure,<br/>with its N and its date"]:::far
  N1 --> N2 --> N3 --> N4
  classDef near fill:#e8f6ec,stroke:#3f9153
  classDef mid fill:#fff4e5,stroke:#e6a23c
  classDef far fill:#eef6ff,stroke:#4a7fb5
```

The estate is torn down. Nothing is running and nothing is billing. Every claim above was scored
offline or against an estate that no longer exists, which is the point: a claim that needs a
running system to check is a claim nobody can reproduce.
