"""Tariff classification — a proposal, an abstention band, and a gate that does not move.

HS classification is genuinely contested: the same goods are argued into different headings by
competent professionals, and a model reporting high confidence on a contested item is worse
than one that abstains. `contracts/documents/customs_declaration.yaml` therefore declares
`hs_code` as `always_review`, and **that is a property of the consequence rather than of the
model** — no proposal here publishes, whatever it scores.

What this package is allowed to claim is stated in `PLAN.md` and repeated on the face of the
README: the accuracy figure below is measured on a synthetic distribution this repository
generated, so it is **not a claim about production accuracy**. The claim is about the *gate*.
"""
