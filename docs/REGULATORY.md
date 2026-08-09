# Regulatory posture

> **Status: argued, not yet verified against source text.** Everything below is reasoning to
> check, not a finding. The first task of Phase 0 is to verify each citation against the
> consolidated texts and stamp this file with a date — the same discipline as
> `tsakanikas-site/tools/dimensions.py` and `../watermark/docs/REGULATORY.md`.

**Rule: an obligation goes in only if it traces to a named article of a named instrument, and
only after the text has been checked. If it cannot be traced, delete it.**

---

## The finding that shapes this project: no high-risk classification arises

**The precise claim, and not a looser one.** An earlier draft of this file said "the AI Act
mostly does not apply". That is wrong in a direction that matters, and it is the kind of
wrongness this portfolio exists to catch: the Act's **prohibitions** and its **AI-literacy
obligation** bind every provider and deployer regardless of risk class, so "does not apply"
is a claim the file cannot support. What the argument actually supports is narrower and still
the point:

> Manifest processes documents about **goods**, not decisions about people. Annex III does not
> list customs, trade documentation or tariff classification, and the system is not a safety
> component of a product covered by Annex I. On the argument as it stands, **no high-risk
> classification arises** — and it should say so plainly, in those words.

**This is a feature, not a disappointment.** Three projects in this portfolio have now asked
"is this high-risk?" and got three different answers — yes (Watermark's curtailment path), no
but GDPR Art. 22 bites harder (Watermark's anomaly path), and no (here). A trust layer that
only appears when a regulation forces it is a compliance product. The argument this repository
makes is the one the site already makes in its scope note: *an enterprise security
questionnaire and an investor's technical due diligence ask for the same evidence, and they
arrive years before a regulator does.*

Do not manufacture a high-risk angle to make the project sound weightier. Doing so would be
the exact failure this portfolio exists to argue against, and a reader who knows Annex III
will notice. Equally, do not round "not high-risk" up to "out of scope" — a reader who knows
Art. 4 will notice that too, and it is the cheaper mistake to make and the more embarrassing
one to be caught at.

### What binds regardless of risk class — to verify, then state

| To verify | Why it is on this list |
|---|---|
| **Art. 5 — prohibited practices** | Binds everyone. Almost certainly nothing here engages it; the deliverable is the checked statement, not the assumption |
| **Art. 4 — AI literacy** | An obligation on **providers and deployers**, with no risk-class gate. If it binds Northbridge it binds it here, and the README may not imply otherwise |
| **Art. 50 — transparency** | Verify whether any output surface counts as interaction with a natural person or as generated content requiring marking. Probably not; check rather than assert |
| **Chapter V — GPAI, and what a *deployer* carries** | If a general-purpose model is used for extraction, Northbridge is a deployer, not a provider. Verify what that actually carries rather than assuming it is nothing |
| **Applicability dates** | Different chapters apply from different dates. A present-tense obligation with the wrong date is a wrong statement |

Each row gets an article, an instrument, a consolidated-text reference and a verification date,
or it is deleted. Phase 0 closes this table; nothing may be written into a README from it
before then.

## What does apply

| Instrument | Why | Where it lands |
|---|---|---|
| **Union Customs Code** — Reg. (EU) 952/2013 | The declarant is legally responsible for the accuracy of the declaration. Automation does not move that responsibility | Claims 1, 2 and 5: a figure that reaches a declaration must be traceable and, below threshold, human-decided |
| **AEO criteria** (UCC + implementing acts) | Authorised Economic Operator status requires a demonstrable system of managing commercial records and appropriate internal controls. **This is the real audit** — a customs authority inspecting the record system, not an AI regulator | The whole record-keeping and versioning design; the diff report on re-extraction |
| **Record retention** under the UCC and national law | Documents and their derived records must be retained for a defined period | Retention class per field in the document contract; the erasure design must not violate it |
| **GDPR** — Reg. (EU) 2016/679 | Documents carry names, signatures and occasionally ID numbers | Art. 5 minimisation (extract only what the contract declares — the strongest control here); Art. 30 records; Art. 32 security. **Note the genuine tension: Art. 17 erasure against a customs retention obligation.** Resolve it explicitly, do not pretend it away |
| **EU sanctions regulations** | Screening parties against designated-person lists is a real obligation for this operator | **Deliberately out of scope** — see `docs/DECISIONS.md`. It is FintelliGuard's territory and would blur two projects |

## The tension worth writing down

GDPR Art. 17 gives a data subject the right to erasure. The Union Customs Code obliges the
declarant to retain records. They point in opposite directions, and the resolution is not
"whichever we implemented first". Art. 17(3)(b) provides an exemption where processing is
necessary for compliance with a legal obligation — so the honest design keeps the record,
minimises what was extracted in the first place, and **records the refusal and its legal basis
rather than silently ignoring the request**.

Watermark's claim 6 is about erasure being complete. Manifest's equivalent is about erasure
being *correctly refused, with a stated basis, and only to the extent the obligation requires*.
Two projects, two opposite correct answers, both defensible. Verify Art. 17(3)(b) and the
retention periods before writing any of this into a README.
