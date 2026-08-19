# Security

## Reporting

Report a vulnerability by opening a [private security advisory][advisory] on this repository, or
by email to `theotsak90@gmail.com`. Please do not open a public issue for anything exploitable.

This is a portfolio project maintained by one person outside working hours. Expect an
acknowledgement within a week; there is no service level beyond that, and no bounty.

[advisory]: https://github.com/theofanis-tsakanikas/manifest/security/advisories/new

## What is in scope

The controls this repository claims to enforce. If one of them can be defeated, that is a
vulnerability even though no estate is standing:

- **Prompt injection reaching an extraction prompt.** A commercial invoice is a document a
  counterparty wrote. Text inside it that carries an instruction and is neither fenced by
  `src/manifest/security/` nor refused by the envelope is the primary class here — including a
  forged delimiter that the fencing escapes rather than refuses.
- **A published field with no provenance.** Any path by which a value reaches a published record
  without a page, a box and a passing check in `src/manifest/gates/provenance.py`. Doctrine rule 7
  is the one door with no key: a field the system cannot locate on a page may not be overridden
  into existence, by anyone, including an approver.
- **A threshold that moves without the derivation moving.** Anything that lets a confidence
  threshold be raised, lowered or bypassed other than by deriving it from a committed recording
  against a declared error budget. ADR-0001's forbidden move — relaxing an error budget under
  queue pressure — is the specific shape to look for.
- **A decision the system made about itself.** No model, pipeline or service principal may raise a
  threshold, clear a cross-document mismatch, approve a classification, or record a review
  decision. A path that lets one do so is the most serious class of defect in this project.
- **An approval counted as evidence when nobody looked.** A route by which a rubber-stamp
  reviewer's agreement contributes to the feedback loop in `src/manifest/core/feedback.py`.
- **A credential, account identifier or non-synthetic trade datum committed to the repository** —
  including in a screenshot. `gitleaks` reads bytes and a console capture is pixels, so
  `scripts/mask_account_id.py --check` is the same rule applied to images and runs in
  `make preflight`. It covers two exposures that look like one: the console's identity chip, and
  the account id printed again inside every bucket name and ARN a terminal capture happens to
  show.

## What is not in scope

- **Findings in the generated corpus.** Every document under `corpus/` is synthetic and every
  party name is invented. A "leaked" shipper is a fixture.
- **The AWS estate.** Nothing is standing. Report a defect in the Terraform that *would* be
  exploitable on apply — an over-broad policy, a public bucket, an unencrypted volume — as a
  normal issue; those are design defects rather than live exposures.
- **Base-image CVEs in the reader container.** Known and named in the README: the image inherits
  Debian package advisories and no gate reads the ECR scan. A report that one of them is
  *reachable through this system's own code paths* is in scope and welcome.

## Known limitations

- **Required reviewers on the deploy environment are off**, under a dated acceptance in
  `contracts/deploy/acceptance.yaml` that `scripts/check_deploy_path.py` refuses to let outlive
  its expiry.
- **The container image is not gated on vulnerability scan results.** Terraform is scanned to zero
  checkov findings; the image is not.
- **`infra/bootstrap` is applied from a laptop**, because it creates the state bucket the other
  five layers use as their backend. It is the only layer with that property.
