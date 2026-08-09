# Manifest

**A document intelligence platform for cross-border trade. Every field traces to a pixel.**

*AWS Textract · Bedrock Data Automation · Bedrock · Comprehend · EMR Serverless · Step
Functions · Iceberg on S3 · OpenSearch · Redshift · SageMaker · Terraform*

> **Manifest** — the trade document, and the word for *made evident*. Both meanings are the
> project.

---

> **Status: Phase 0 — foundations.** This README is a stub and says so. It will carry a
> scoreboard in which **every number is the output of a command in this repository**, run on a
> laptop with no AWS account; until a claim is provable, it does not appear here as though it
> were. There is no scoreboard yet because there is nothing yet to put in one, and a table of
> claims with no figures beside them is an advertisement.
>
> **Nothing in this repository has been applied to AWS, and nothing will be.** The estate is
> written, validated against real provider schemas and scanned clean, and left unapplied —
> including `infra/bootstrap/`. The posture is *ready to deploy, not deployed*. No screenshot,
> no wall-clock time and no euro figure here is a measurement, and none may be written as if
> it were. See [`docs/DECISIONS.md`](docs/DECISIONS.md) 14 to 16.

---

## The problem

A customs broker and freight forwarder receives commercial documents — bills of lading,
commercial invoices, packing lists, certificates of origin, customs declarations, arrival
notices. They arrive as scans of scans: skewed, stamped over the numbers, in three languages,
with tables that break across pages and handwritten corrections in the margin.

Three things come out of them: a structured record per document, agreement across documents,
and a tariff classification. And above all of it, the problem nobody demonstrates — what
happens when the extraction model improves and four million documents have to be processed
again.

The whole system is built around one boundary:

| Models own | Deterministic code owns |
|---|---|
| Reading characters off a degraded scan | Whether a value is confident enough to publish |
| Proposing which field a token belongs to | Whether the value actually appears where its provenance says it does |
| Proposing a tariff classification | Whether two documents agree |
| Suggesting that two parties are the same entity | Whether a human's decision was recorded, and whether the human was actually looking |

**A published field that cannot be located on a page is a build failure.**

---

## The seven claims

Each is meant to be checkable in CI, on a laptop, with no AWS account and no credentials. The
column on the right is what exists **today**, and it is deliberately mostly empty.

| # | Claim | Status |
|---|---|---|
| 1 | No field is published below its confidence threshold, and the threshold is *derived* from a declared error budget — not chosen | phase 2 |
| 2 | Every published field traces to a page, a box and a document version, checked against the page rather than against the record | phase 2 |
| 3 | Re-extraction is reproducible and versioned; an engine upgrade produces a new version with a diff, never a silent overwrite | phase 3 |
| 4 | Cross-document disagreement is surfaced, never smoothed | phase 3 |
| 5 | The human loop is real and measured — the review queue is a finite declared resource, and rubber-stamping is detected | phase 4 |
| 6 | Entity resolution is reversible: a merge can be un-merged with lineage intact | phase 3 |
| 7 | Bulk reprocessing is idempotent, and its cost is a **model** that says so | phase 4 |

The full statements, including what each one deliberately does **not** claim, are in
[`CLAUDE.md`](CLAUDE.md).

---

## Running it

```bash
make install     # venv + editable install
make test        # full suite, offline
make lint        # the exact command CI runs
make core-pure   # the core imports no cloud SDK, no engine, and names no engine
make gate-proof  # break every gate on purpose; each must be refused, for the right reason
make preflight   # everything that must be true before the estate is stood up
```

Requires Python 3.12+. No AWS account, no credentials, no network.

## Licence

MIT — see [LICENSE](LICENSE). Engineering rules live in [CLAUDE.md](CLAUDE.md); the four
phases in [PLAN.md](PLAN.md).
