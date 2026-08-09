# Day one — the manual work that has no API

**Nothing in this file has been done.** `docs/DECISIONS.md` 14: nothing in this repository is
ever applied to AWS. Writing this down was always the deliverable; doing it was not — the same
posture as Attestor and Watermark, and this file exists so that the manual work is *recorded*
rather than performed silently by whoever deploys and remembered by nobody afterwards.

If the author ever does deploy, this is the list. Every item here is manual because it has no
Terraform resource or no API, not because it was inconvenient to automate — and each says
which.

---

## Before the first apply

| # | Step | Why it is not in Terraform |
|---|---|---|
| 1 | **Request Bedrock model access** for the tier-2 extractor in the target Region | The access request is a console action against the account, and there is no resource for it. A deploy that skips it succeeds and the first `InvokeModel` fails with `AccessDeniedException`, which reads like an IAM problem and is not |
| 2 | **Register the GitHub OIDC provider**, or set `create_oidc_provider = false` | An account holds at most one provider per issuer URL. `infra/bootstrap/` creates it by default; where another repository in the same account got there first, creating a second is `EntityAlreadyExists` partway through an apply, with half the layer up |
| 3 | **Create the `deploy` and `destroy` GitHub environments** and set their required reviewers | The environments *are* the gate — the workflows name them and neither can run without one. A repository variable cannot substitute: the protection lives on the environment, not on the workflow |
| 4 | **Set `AWS_DEPLOY_ROLE_ARN`** as a repository variable, not a secret | It is an ARN. A role ARN with no trust for the caller is not a credential, and putting it in secrets makes it unreadable in a log where reading it is exactly what you want at three in the morning |
| 5 | **Confirm the SNS subscription** for budget and expiry alerts | An email subscription is pending until the recipient clicks. Terraform reports the subscription created and it delivers nothing — a guard that fires into a confirmation nobody completed |
| 6 | **Decide the analytics Region.** `eu-central-1` has no 4-RPU Redshift Serverless floor (`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09), so the minimum there is 8 RPUs | A capacity floor is a fact about the Region, not a resource. Either accept 8 or move `infra/analytics/` to `eu-west-1` — deliberately, not by discovering it in a bill |

## Before the first document

| # | Step | Why |
|---|---|---|
| 7 | **Install the tier-0 reader's language data** on whatever runs it — Greek and Dutch at minimum | The corpus and the scenario are in three languages and no managed reader in the stack reads two of them. A run without the data quietly reduces the system to English and every claim scored on it keeps reporting the same green; `scripts/ocr_record.py` fails rather than skips for this reason |
| 8 | **Set `expires_at` on every layer** | Every layer above `bootstrap` requires it and there is no default meaning "never". The reaper is the only thing between a portfolio estate and a monthly bill |

## What is deliberately *not* here

**No console click that a resource could have made.** If an item appears on this list because
automating it was tedious, it is in the wrong place — it belongs in Terraform.

**No screenshot, no timing, no euro figure.** There is no estate to capture one from.

**No "run it once to see".** `infra/bootstrap/` is the one layer whose design permits a laptop
apply and it is not applied either, because applying it "just to check" would put an exception
into every other sentence in this repository about not deploying.

## Tearing it down

`destroy.yml` exists, is gated behind its own protected environment, and has never been
dispatched. It is not optional and it is not a follow-up: a repository with a deploy path and
no teardown path is how an estate gets left standing, and it is the difference between a
portfolio piece and a bill.

The state bucket carries `prevent_destroy` and outlives the estate on purpose — it is the
record of what existed. Removing it is a deliberate, separate act, and `infra/bootstrap/`'s
README says what the two honest options are.
