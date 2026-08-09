# Portfolio context — why this project exists and what it changes

Context a session inside this repository would otherwise not have. Not needed to write code,
but it decides several judgement calls. Read it once.

## The author

Theofanis Tsakanikas — AI data engineer, Athens, remote-only (EU/CET), targeting AWS AI data
engineering roles and running a consulting venture ("the trust layer") at **tsakanikas.io**.
AWS GenAI Developer (Professional), AWS Data Engineer (Associate), Databricks GenAI Engineer
and Data Engineer (Associate), HashiCorp Terraform (Associate); IAPP AIGP in progress. MEng
ECE, NTUA. The portfolio lives at `~/portfolio/projects/`.

**Conversation in Greek. Repository content in English.**

## What already exists

| Project | What it is |
|---|---|
| **FintelliGuard** | Enterprise RAG + compliance agent on Bedrock, 592 tests |
| **Attestor** | Multi-tenant regulated report factory on Bedrock AgentCore, 293 tests |
| **Watermark** | Real-time decision platform for an electricity network — Kinesis, Managed Flink, SageMaker Feature Store, Lake Formation. **In progress; Manifest starts only after it lands** |
| **Self-Healing Multi-Cloud Agents** | LangGraph supervisor/medic across four platforms, 380 tests |
| **Multi-Cloud Governance Platform** | One contract → Unity Catalog + Snowflake, 137 tests |
| **Fleet Risk Lakehouse** | Databricks medallion, GDPR Art. 9 column masks, 165 tests |
| **Real-Time Telemetry Pipeline** · **Contract-Driven Data Pipeline** | The two earliest, simplest builds |

**Attestor is the quality bar; Watermark is the current one.** The conventions — a claim
scoreboard whose numbers are command output, `gate-proof`, `preflight`, doctrine as numbered
rules, offline-first, an ADR per real decision — are what this project matches or beats. When
unsure about a convention, open `../attestor/` or `../watermark/` and copy the thinking.

**Two mistakes already made in this portfolio, worth not repeating.** In Watermark, a claim
was drafted that compared a function with itself and would have reported green forever — see
its claim 3. And an erasure claim was drafted that overstated what crypto-shredding can do to
a trained model. Both were caught by a session reading carefully and pushing back. Push back
the same way here: the equivalent traps are claim 2 and claim 4, and they are called out in
`PLAN.md`.

## The gap Manifest fills

After Watermark, the portfolio still has **no** work on dirty inputs, **no** OCR or document
AI, **no** extraction confidence or calibration, **no** human-in-the-loop at queue scale,
**no** entity resolution, **no** large-batch reprocessing, and **no** Redshift.

Every RAG system in this portfolio runs over a *clean* corpus — EUR-Lex text, engineering
standards. No company has a clean corpus. A reviewer who notices that is right to.

Manifest also adds a proposition none of the other builds gives: **"I work with data as it
arrives, not as I would like it to arrive."**

## Where it sits against the site

- **The AI Act does not apply here, and the README says so.** That is deliberate. The site's
  framework already argues that this evidence is worth producing regardless of whether a
  regulator is asking — Manifest is the demonstration. It widens the ICP beyond companies in
  AI Act panic, which is commercially the more useful half of the market.
- **Dimension 02 of the framework, from the input side.** Existing builds prove lineage and
  contracts on data that was already structured. This one proves quality control at the point
  where data is *manufactured* from documents — a different and harder half of the dimension.
- **The site's card count is already an open problem.** With Manifest the portfolio has nine
  builds. The editorial decision noted in `../watermark/docs/PORTFOLIO-CONTEXT.md` — three
  flagships in front, the rest in a compact index — becomes unavoidable rather than optional.
- **Numbers live in several places**: `job-application/CV.md`, `CV-detailed.md`,
  `portfolio-one-pager.md`, `tsakanikas-site/FRAMEWORK.md`, `tsakanikas-site/CLAUDE.md`, and
  the LinkedIn assets. Cheap to update, expensive to forget.

## What to do when Phase 4 is done

1. Public GitHub repository; README scoreboard as the front door.
2. CV, detailed CV and one-pager entries; update test totals and the platform count everywhere.
3. Row in the framework-mapping table in `tsakanikas-site/FRAMEWORK.md`.
4. Site card, evidence screenshots, 16:9 walkthrough via `tools/video/build_wide.py`, article
   via `tools/build_writing.py`.

## The wider plan

Watermark and Manifest are the two flagships meant to carry the portfolio, with the earliest
and simplest builds receding to a compact index or leaving the front line. Build accordingly:
the standard of proof here is what justifies retiring the older ones.
