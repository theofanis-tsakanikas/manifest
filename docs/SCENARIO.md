# The scenario

One fictional but realistically-shaped operator. Most of the engineering difficulty comes
from properties of these documents, not from the AWS services used to read them.

## The operator

**Northbridge Forwarding** — an EU freight forwarder and customs broker with offices in
Rotterdam and Piraeus.

| | |
|---|---|
| Archive | ~4,000,000 documents, 12 years |
| Inbound | ~18,000 documents/day, ~55,000 pages/day |
| Document types | 6 (below) |
| Languages | English, Greek, Dutch — plus party names in Chinese and Arabic script |
| Sources | email attachments, an SFTP drop from three carriers, a scanning desk at each office |
| Peak | Monday mornings and the two days before a quarter end, roughly 3× the mean |

## The documents

| Type | What it carries | Why it is hard |
|---|---|---|
| **Commercial invoice** | seller, buyer, line items, unit prices, currency, incoterm, total | Tables that break across pages; totals that must equal the sum of lines; currency symbols confused by OCR (€/E, ¥/Y) |
| **Bill of lading** | shipper, consignee, vessel, ports, container numbers, gross weight | Carrier-specific layouts; container numbers carry an ISO 6346 check digit, which makes a wrong read *detectable* — see below for what that is and is not |
| **Packing list** | package counts, dimensions, net and gross weight | Weights in kg and lb on the same page; must agree with the bill of lading |
| **Certificate of origin** | country of origin, chamber stamp, signature | The stamp lands on the country field roughly 8% of the time |
| **Customs declaration** | HS codes, duty, declared value, procedure codes | Structured but authored by humans under time pressure; the field with the most consequential errors |
| **Arrival notice** | ETA, terminal, release conditions | Superseded frequently — the amendment problem |

## The properties that make it hard

1. **Degraded input.** Scans of faxes of scans. Skew, JPEG artefacts, bleed-through from the
   reverse side, staple shadows, and stamps over the numbers that matter.
2. **Confidence is uncalibrated.** OCR and model confidence scores are not probabilities. A
   threshold on an uncalibrated score is a magic number. Claim 1 exists because of this.
3. **Tables across page breaks.** A line-item table continuing on page 3 with no repeated
   header is where naive extraction silently loses rows — and the total still looks plausible.
4. **Cross-document agreement.** The same shipment is described three times by three parties.
   Weight, container number, invoice value and package count must reconcile. Disagreements
   are ordinary — and they are exactly what the operator is paid to catch.
5. **Amendments and supersession.** A corrected bill of lading arrives after the original was
   processed. It supersedes, it does not overwrite, and everything derived from the original
   must be re-derived and diffed.
6. **Re-extraction at scale.** A better engine arrives. Four million documents must be
   reprocessed — idempotently, with a diff per document, without double-charging, and without
   invalidating the human decisions already made on fields that did not change.
7. **Entity resolution across scripts.** *北方桥* / *Northbridge* / *NORTHBRIDGE FWD BV* /
   *N. Bridge Forwarding B.V.* are one party. Transliteration, abbreviation, legal-form
   suffixes and OCR damage all at once. And a merge is sometimes wrong, so it must be
   reversible.
8. **Ambiguous classification.** HS classification is genuinely contested — the same goods are
   argued into different headings by competent professionals. A model that reports high
   confidence on a contested item is worse than one that abstains.
9. **The review queue is finite.** Two people per office. A threshold that routes 40% of
   fields to review does not make the system safer; it makes reviewers into a rubber stamp.
   This is a *system design constraint*, and claim 5 enforces it as one.
10. **Cost is per page.** Every design decision about which engine reads which page is a
    money decision, at 55,000 pages a day.
11. **Untrusted content.** A counterparty writes the invoice. Text in it reaching an
    extraction prompt as an instruction is prompt injection with a financial motive.
12. **Data minimisation.** Documents carry names, signatures, sometimes ID numbers. Extract
    what the contract declares and nothing else — the cheapest way to not mishandle personal
    data is to never extract it.

### The container check digit is a falsifier, not ground truth

Worth stating precisely, because the loose version of it was written down first and it would
have propagated into a claim.

The ISO 6346 check digit is a **mod-11** figure over the owner code, category identifier and
serial number. What it gives is one-directional:

- **A failing check digit proves the read is wrong.** That is real, it costs nothing, and it
  is the only field on any of these documents that can say so about itself.
- **A passing check digit does not prove the read is right.** Roughly one in eleven random
  corruptions passes, and the scheme is structurally blind to some transpositions. A
  confidently misread container number that happens to check out is a specific, reachable
  failure — not a theoretical one.

So it is a **precision instrument, not a recall one**: it is used to refuse values, never to
confirm them, and never as a stand-in for a label. On the synthetic corpus it adds nothing to
ground truth, because exact ground truth already exists by construction. Its real value is on
the **public dataset**, where there are no field labels at all and a self-checking field is
the only measurement available — and there its meaning is "this many reads are provably
wrong", which is a lower bound on the error rate and must be reported as one.

## The corpus

Two sources, and the split matters.

**Synthetic, and deliberately pathological.** Generated by rendering realistic layouts to PDF
and then *degrading* them: skew, gaussian and salt-pepper noise, JPEG recompression,
simulated stamps positioned over fields, bleed-through, handwritten-style corrections. Seeded
and deterministic, with **exact ground truth** — which is what makes claims 1, 2 and 4
measurable at all. It must contain, on purpose:

- N planted cross-document mismatches (weight, value, container, count) and a matched set of
  documents that agree, so false positives are measurable
- a line-item table that breaks across a page boundary with no repeated header
- stamps landing on the country-of-origin field
- currency-symbol confusions
- an amended bill of lading superseding an original already processed
- one party appearing under five surface forms across two scripts
- documents carrying an injection attempt in a free-text field
- fields deliberately illegible, so abstention counts are exact

**Real public documents, for the honesty check.** A public scanned-document dataset with a
compatible licence, used as an out-of-distribution baseline for OCR and layout quality — the
same role the real VED telemetry replay plays in Fleet Risk. Check the licence and record it
before committing anything. A generator that only tests itself proves nothing; the whole
point is that real documents are worse than the ones you imagined.
