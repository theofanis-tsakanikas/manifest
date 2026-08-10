# The out-of-distribution set, and the terms it arrives under

**Dataset:** CORD — *Consolidated Receipt Dataset for Post-OCR Parsing*
**Authors:** Seunghyun Park, Seung Shin, Bado Lee, Junyeop Lee, Jaeheung Surh, Minjoon Seo,
Hwalsuk Lee (Clova AI, NAVER Corp.), Document Intelligence Workshop at NeurIPS 2019.

**Licence: Creative Commons Attribution 4.0 International (CC BY 4.0).**

Quoted from the dataset's own repository, <https://github.com/clovaai/cord>, read **2026-08-10**:

> "This work is licensed under a Creative Commons Attribution 4.0 International License."

Licence text: <http://creativecommons.org/licenses/by/4.0/>, read 2026-08-10. The distribution
used here is <https://huggingface.co/datasets/naver-clova-ix/cord-v2>, whose dataset card carries
the same `cc-by-4.0` identifier, read 2026-08-10.

CC BY 4.0 permits redistribution and derivative works, for any purpose including commercial, on
the condition of attribution. This file is that attribution.

---

## Why this set, against the four criteria in `README.md`

**1 · A licence that permits redistribution and derived works.** Quoted above, from the authors'
own repository and corroborated by the distribution card, both with the date read. Two other
candidates were checked first and one was rejected on this axis alone: the widely used scanned
document collections in this space descend from litigation archives whose terms are either
research-only or unstated, and "it is widely used" is not a licence.

**2 · Real capture, not renders.** This is the criterion that decided it. **DocLayNet**
(CDLA-Permissive-1.0, so it passes criterion 1 comfortably) was the obvious first choice and was
rejected here: it is **born-digital PDF**, and a born-digital page rasterised cleanly tells this
project nothing it does not already know. CORD is photographs of physical receipts — thermal
print, creases, uneven lighting, hands holding paper, perspective skew. That is somebody else's
capture pipeline, which is the whole point.

**3 · Field-level ground truth.** Per-word text with quadrilateral coordinates
(`valid_line[].words[]`), which is exactly the grain this project needs: a confidence, a box, and
what the word actually said.

**4 · Personal data.** Restaurant receipts. They name merchants, items and prices rather than
individuals. No image is redistributed by this repository — see below — and what is committed is
a list of confidences and correctness flags with no text in it at all, which carries no personal
data forward under any reading.

---

## What is committed here, and what is not

**No images.** `recordings/external/` holds the *derived* observations — one confidence and one
correct/wrong flag per matched word — and nothing else. Not to avoid the licence, which permits
redistribution, but for the same reason `recordings/ocr/` exists: the recording is the unit of
evidence, and a repository does not need to carry 223 MB to state a calibration figure.

`make external-record` fetches the set and regenerates the recording. A stranger reproduces the
number the same way they reproduce every other number here.

---

## What this set can and cannot tell us

**It can answer the question that undermines everything else.** Every figure on this
repository's scoreboard is scored against a corpus this repository generated, and the obvious
challenge — *did you tune the generator until the claims passed?* — has only ever had one answer,
the declared envelope, which is also ours. Real photographed paper breaks that circle: if the
reader's confidence behaves the same way on documents nobody here designed, the generated
corpus has earned its credibility on the axis that matters.

**It cannot stand in for the domain.** These are receipts, not bills of lading. The fields do
not map to `contracts/documents/`, so **no threshold in this repository is derived from them and
none should be**. What transports is the *calibration* — the relationship between a confidence
and whether the word was right — and that is the only thing measured against it.

Stated plainly because the temptation runs the other way: an out-of-distribution column is worth
a great deal here, and the way it would go wrong is by quietly becoming a claim about trade
documents.
