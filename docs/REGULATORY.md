# Regulatory posture

> **Status: verified against source text on 2026-08-09.** Every obligation below names an
> article of a named instrument and the date the text was read. Where a passage is quoted, it
> was read as quoted; where it is paraphrased, it says so and the verbatim quote must be taken
> from the consolidated text before it is written into a README.

**Rule: an obligation goes in only if it traces to a named article of a named instrument, and
only after the text has been checked. If it cannot be traced, delete it.**

---

## The finding that shapes this project: no high-risk classification arises

An earlier draft said "the AI Act mostly does not apply". That is wrong in a direction that
matters, and it is the kind of wrongness this portfolio exists to catch: some of the Act's
obligations bind regardless of risk class. What the argument supports is narrower and still the
point.

### The classification argument

**Article 6(2) and Annex III.** Annex III lists eight areas
([Annex III](https://artificialintelligenceact.eu/annex/3/), read 2026-08-09):

1. Biometrics · 2. Critical infrastructure · 3. Education and vocational training ·
4. Employment, workers management and access to self-employment · 5. Access to and enjoyment of
essential private services and essential public services and benefits · 6. Law enforcement ·
7. Migration, asylum and border control management · 8. Administration of justice and democratic
processes

**Customs, trade documentation and tariff classification are not among them.** Point 7 is the
nearest neighbour and reading it closes the question rather than opening it: every one of its
four subpoints is about a *natural person* — polygraphs; assessing "a risk … posed by a natural
person who intends to enter or who has entered into the territory of a Member State"; examining
"applications for asylum, visa or residence permits"; and "detecting, recognising or identifying
natural persons". Manifest reads documents about **goods**. The nearest listed use is about
people crossing a border, not about cargo crossing one.

**Article 6(1) and Annex I.** High-risk also arises where an AI system is a safety component of,
or is itself, a product covered by the Union harmonisation legislation in Annex I. Customs
brokerage software is not such a product and is not a safety component of one.

**So: no high-risk classification arises.** The README says it in those words.

### And it is a live statement, not a future one

**Article 113** ([read 2026-08-09](https://artificialintelligenceact.eu/article/113/)), verbatim:

> It shall apply from 2 August 2026.
> However:
> (a) Chapters I and II shall apply from 2 February 2025;
> (b) Chapter III Section 4, Chapter V, Chapter VII and Chapter XII and Article 78 shall apply
> from 2 August 2025, with the exception of Article 101;
> (c) Article 6(1) and the corresponding obligations in this Regulation shall apply from
> 2 August 2027.

As of today the Regulation's main body — including the Annex III high-risk regime under
Article 6(2) — is in application. The Annex I limb under Article 6(1) is not, until 2027. The
classification argument above therefore concerns a regime that is **currently in force**, which
is worth more than an argument about one that is not.

### Why this is a feature

Three projects in this portfolio have now asked "is this high-risk?" and got three different
answers — yes (Watermark's curtailment path), no but GDPR Art. 22 bites harder (Watermark's
anomaly path), and no (here). A trust layer that only appears when a regulation forces it is a
compliance product. The argument this repository makes is the one the site already makes: *an
enterprise security questionnaire and an investor's technical due diligence ask for the same
evidence, and they arrive years before a regulator does.*

Do not manufacture a high-risk angle to make the project sound weightier — a reader who knows
Annex III will notice. Equally, do not round "not high-risk" up to "out of scope": a reader who
knows Article 4 will notice that, and it is the cheaper mistake to make and the more
embarrassing one to be caught at.

---

## What binds regardless of risk class

| Provision | In application since | What it means here |
|---|---|---|
| **Art. 4 — AI literacy** | 2 Feb 2025 (Ch. I) | Binds **providers *and* deployers**. Northbridge is a deployer, so it binds |
| **Art. 5 — prohibited practices** | 2 Feb 2025 (Ch. II) | Binds everyone. Checked: nothing here engages it |
| **Art. 50 — transparency** | 2 Aug 2026 (Ch. IV) | Probably not triggered. Satisfied anyway, cheaply — see below |
| **Ch. V — GPAI** | 2 Aug 2025 | Obligations fall on **providers of GPAI models**. Northbridge is neither |

**Article 4**, verbatim ([read 2026-08-09](https://artificialintelligenceact.eu/article/4/)):

> Providers and deployers of AI systems shall take measures to ensure, to their best extent, a
> sufficient level of AI literacy of their staff and other persons dealing with the operation
> and use of AI systems on their behalf …

This lands on the review queue, and it lands usefully. ADR-0001 measures whether a reviewer was
plausibly looking; Article 4 is the obligation to make sure they are equipped to look. The two
are the same requirement approached from opposite ends, and the repository's answer to both is
the same artefact: a reviewer is shown the crop, the confidence, and what the system is
uncertain about — not a yes/no button over a value with no context.

**Article 5.** The prohibited practices concern social scoring, exploitation of vulnerability,
untargeted facial-image scraping, emotion inference in the workplace and education, biometric
categorisation by protected characteristic, and real-time remote biometric identification for
law enforcement. Manifest extracts declared fields from commercial documents about goods.
Nothing engages. Worth checking rather than assuming — but the checked answer is no.

**Article 50.** Paragraph 1 concerns systems intended to interact directly with natural persons
and carries an exception where it is obvious to a reasonably informed person. Paragraph 2
concerns providers of systems generating synthetic audio, image, video or text. Paragraph 4
concerns deployers publishing generated or manipulated content.

The review surface is an internal tool used by trade professionals, and the obligation is on
providers of systems intended to interact directly with natural persons — so paragraph 1 is
probably not triggered. It is satisfied regardless, because the design already requires it: a
model proposal is displayed as a model proposal, with its confidence, beside the crop it came
from. A control that costs nothing and closes a question is worth having whether or not the
question was live.

One case worth naming because it looks like a trigger and is not: **the corpus generator
produces synthetic documents.** They are not Article 50(2) output — the generator is
deterministic rendering code, not an AI system generating synthetic content — and they are
labelled synthetic everywhere they appear regardless.

**Chapter V.** If a general-purpose model is used for the tier-2 extractor (ADR-0004 — for Greek
and Dutch it is the only escalation path), Northbridge is a **deployer of an AI system** built on
that model, not a provider of the model and not a provider placing a GPAI-based system on the
market. Chapter V's obligations — documentation, copyright policy, training-data summary,
systemic-risk duties — fall on the model provider. Nothing in Chapter V lands on Northbridge as
described here.

---

## What does apply

| Instrument | Article | What it lands on |
|---|---|---|
| **Union Customs Code** — Reg. (EU) 952/2013 | **Art. 15** | The person lodging a declaration is responsible for the accuracy and completeness of the information in it, and for the authenticity, accuracy and validity of supporting documents. Automation does not move that responsibility. Claims 1, 2 and 5 |
| UCC | **Art. 39(b)** | AEO status requires demonstrating a high level of control over operations and the flow of goods, through a system of managing commercial and transport records that allows appropriate customs controls. **This is the real audit** — a customs authority inspecting the record system, not an AI regulator. The whole versioning and diff design |
| UCC | **Art. 51** | Retention of documents and information for **at least three years**, counted from the end of the year in which the relevant declaration was accepted or the procedure discharged, and extended where a correction or legal proceedings are in play |
| **GDPR** — Reg. (EU) 2016/679 | **Art. 5(1)(c)** | "adequate, relevant and limited to what is necessary in relation to the purposes for which they are processed". **The strongest control here**: extract only what the contract declares. The cheapest way not to mishandle personal data is never to extract it |
| GDPR | **Art. 5(1)(e)** | Storage limitation — kept in identifiable form no longer than necessary. In tension with UCC Art. 51, resolved below |
| GDPR | **Art. 17(3)(b)** | The erasure exemption. See below |
| **EU sanctions regulations** | — | **Deliberately out of scope** (`docs/DECISIONS.md` 4). It is FintelliGuard's territory and two projects doing financial-crime screening would blur both |

> UCC Articles 15, 39 and 51 were read in the consolidated text
> ([CELEX 02013R0952-20221212](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02013R0952-20221212),
> 2026-08-09) and are **paraphrased above**. Before any of them is quoted in a README, take the
> verbatim wording from that text.

---

## The tension, resolved precisely

GDPR Article 17 gives a data subject the right to erasure. UCC Article 51 obliges retention.
They point in opposite directions, and the resolution is not "whichever we implemented first".

**Article 17(3)**, verbatim ([read 2026-08-09](https://gdpr-info.eu/art-17-gdpr/)):

> Paragraphs 1 and 2 shall not apply **to the extent that** processing is necessary: … (b) for
> compliance with a legal obligation which requires processing by Union or Member State law to
> which the controller is subject …

Three words carry the design: **to the extent that**. The exemption is not a blanket. It covers
the processing the obligation requires, and no more.

So the honest answer has four parts, and each is a build requirement rather than a paragraph:

1. **Minimise at extraction.** A field never extracted needs no exemption. Art. 5(1)(c) is
   therefore the control that does the most work, and it is enforced by the document contract:
   a field not declared is not extracted.
2. **Keep what Art. 51 requires**, for as long as it requires, and no longer — which makes the
   retention class a *per-field* property in the contract, not a per-document one.
3. **Erase what falls outside the obligation.** Reviewer-integrity metrics, derived enrichment,
   entity-resolution working data: none of it is a customs record. The exemption does not reach
   them and they are not kept behind it.
4. **Record the refusal and its basis.** Where erasure is refused, the refusal, the article, the
   retention class and the expiry are recorded and are answerable to the data subject.

Watermark's claim 6 is that erasure is *complete*. Manifest's equivalent is that erasure is
**correctly refused, with a stated basis, and only to the extent the obligation requires** — and
that everything outside that extent is actually erased. Two projects, two opposite correct
answers, both defensible.

**Not yet settled, and flagged rather than assumed:** UCC Art. 51 sets a floor of three years and
national law may set longer periods, and Northbridge operates in two Member States. The retention
class in the contract is therefore **data with a jurisdiction**, and this repository does not
state a Dutch or Greek retention period until one has been read from the national instrument.
Writing "three years" as *the* answer would be a traced-sounding number that is only a floor.

---

## Verification log

| Read | Source | Date |
|---|---|---|
| AI Act Art. 4, Art. 113, Annex III (headings and point 7), Art. 50 | artificialintelligenceact.eu, reproducing OJ L, 2024/1689 | 2026-08-09 |
| GDPR Art. 5(1)(c), 5(1)(e), 17(3) | gdpr-info.eu, reproducing Reg. (EU) 2016/679 | 2026-08-09 |
| UCC Art. 15, 39, 51 — **paraphrased, not quoted** | EUR-Lex consolidated CELEX 02013R0952-20221212 | 2026-08-09 |

Re-verify before Phase 4's README, and stamp again. A citation with an old date is a citation
that says how long ago somebody checked, which is the useful thing about it.
