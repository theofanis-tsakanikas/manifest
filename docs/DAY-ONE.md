# Day one — the manual work that has no API

**Nothing in this file has been done.** `docs/DECISIONS.md` 14: nothing in this repository is
ever applied to AWS. Writing this down was always the deliverable; doing it was not — the same
posture as Attestor and Watermark, and this file exists so that the manual work is *recorded*
rather than performed silently by whoever deploys and remembered by nobody afterwards.

If the author ever does deploy, this is the list. Every item here is manual because it has no
Terraform resource or no API, not because it was inconvenient to automate — and each says
which.

---

## The shape of it

```
  laptop                             CI (gated, human-dispatch only)
  ──────                             ─────────────────────────────────
  terraform apply infra/bootstrap
      ├── state bucket + KMS
      ├── OIDC provider + deploy role
      │     └── permissions for every layer  ← deploy_permissions.tf
      └── SSM /manifest/bootstrap/*  ────────►  aws ssm get-parameter
            state_bucket                          │
            state_kms_key_arn                     ├─ terraform init -backend-config
            deploy_role_arn                       │
            budget_notification_email (Secure)    └─ terraform apply, layer by layer

  repository variable: AWS_ACCOUNT_ID   ← the only one, and the only one allowed
```

State locking is the S3 backend's lock file (`use_lockfile = true`), **not** a DynamoDB table.
That is a deliberate change carried over from Watermark: it removes a table, its capacity mode,
its own encryption decision and one more thing to destroy, and it puts the lock in the same
bucket, under the same key, as the thing it protects. `required_version = ">= 1.10"` is what
stops an older Terraform applying with no lock at all.

## Before the first apply

| # | Step | Why it is not in Terraform |
|---|---|---|
| 1 | **Request Bedrock model access** for the tier-2 extractor in the target Region | The access request is a console action against the account, and there is no resource for it. A deploy that skips it succeeds and the first `InvokeModel` fails with `AccessDeniedException`, which reads like an IAM problem and is not |
| 2 | **Register the GitHub OIDC provider**, or set `create_oidc_provider = false` | An account holds at most one provider per issuer URL. `infra/bootstrap/` creates it by default; where another repository in the same account got there first, creating a second is `EntityAlreadyExists` partway through an apply, with half the layer up |
| 3 | **Create the `deploy` and `destroy` GitHub environments** and set their required reviewers | The environments *are* the gate — the workflows name them and neither can run without one. A repository variable cannot substitute: the protection lives on the environment, not on the workflow |
| 4 | **Set `AWS_ACCOUNT_ID`** as a repository variable — and *only* that one | The single value CI cannot derive: it has to know which account before it can ask that account anything, and reading an SSM parameter is already asking. Everything else the deploy needs — the state bucket, the deploy role ARN, the alert address — is published by `infra/bootstrap` to `/manifest/bootstrap/*` and resolved at run time. `scripts/check_deploy_path.py` fails if a second variable ever appears |
| 5 | **Confirm the SNS subscription** for budget and expiry alerts | An email subscription is pending until the recipient clicks. Terraform reports the subscription created and it delivers nothing — a guard that fires into a confirmation nobody completed |
| 5a | **Look up the two numeric ids** — `https://api.github.com/users/<owner>` and `https://api.github.com/repos/<owner>/<repo>` — and pass them as `github_owner_id` and `github_repository_id` | Neither has a default, deliberately. The deploy role's whole security is the `sub` condition on its trust policy, and a subject scoped to *names* is one that whoever releases and re-registers the repository name inherits. Numeric ids cannot be re-registered. A default here would be a trust policy silently trusting the wrong repository |
| 5a2 | **Pass `escalation_model_arn` to the bootstrap apply** — the exact model or inference-profile ARN the cascade may invoke | No default, and never `*`. `bedrock:InvokeModel` on `*` is permission to invoke every model in the account: different prices, different data-handling terms, different regional footprints. Naming one is what makes the budget guard a guard rather than a formality. Bootstrap publishes it and the deploy resolves it, so it never becomes a second repository variable |
| 5b | **Pass `budget_notification_email` to the bootstrap apply** | It is the one input nothing can derive. `infra/bootstrap` publishes it as a `SecureString` — an address belongs to a person, and this project fails closed on personal data rather than reasoning about who can already read the account |
| 6 | **Decide the analytics Region.** `eu-central-1` has no 4-RPU Redshift Serverless floor (`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09), so the minimum there is 8 RPUs | A capacity floor is a fact about the Region, not a resource. Either accept 8 or move `infra/analytics/` to `eu-west-1` — deliberately, not by discovering it in a bill |

## Before the first document

| # | Step | Why |
|---|---|---|
| 7 | **Install the tier-0 reader's language data** on whatever runs it — Greek and Dutch at minimum | The corpus and the scenario are in three languages and no managed reader in the stack reads two of them. A run without the data quietly reduces the system to English and every claim scored on it keeps reporting the same green; `scripts/ocr_record.py` fails rather than skips for this reason |
| 7a | **Check the reader version in the `Dockerfile` matches the one that produced `recordings/ocr/`** | The image asserts it at build time and refuses to build otherwise, so this is a heads-up rather than a manual check: if the base image's publisher moved the binary, the build fails by name. Accept the movement with `make ocr-record`, which prints every threshold's shift, and update `EXPECTED_READER_VERSION` in the same commit. A reader that moved silently moves every number on the scoreboard |
| 8 | **Dispatch `deploy` with `include_expensive_layers` off first** | EMR Serverless and the Redshift workgroup are the estate's cost, and they are opt-in for that reason. The first run should stand up foundation, extraction and lakehouse and stop; the two expensive layers are a second, deliberate dispatch with their own approval, used and then torn down |
| 9 | **Set `expires_at` on every layer** | Every layer above `bootstrap` requires it and there is no default meaning "never". The reaper is the only thing between a portfolio estate and a monthly bill |

## What is deliberately *not* here

**No console click that a resource could have made.** If an item appears on this list because
automating it was tedious, it is in the wrong place — it belongs in Terraform.

**No screenshot, no timing, no euro figure.** There is no estate to capture one from.

**No "run it once to see".** `infra/bootstrap/` is the one layer whose design permits a laptop
apply and it is not applied either, because applying it "just to check" would put an exception
into every other sentence in this repository about not deploying.

## Tearing it down

`destroy.yml` exists, is scoped to its own environment, and has never been
dispatched. It is not optional and it is not a follow-up: a repository with a deploy path and
no teardown path is how an estate gets left standing, and it is the difference between a
portfolio piece and a bill.

The state bucket carries `prevent_destroy` and outlives the estate on purpose — it is the
record of what existed. Removing it is a deliberate, separate act, and `infra/bootstrap/`'s
README says what the two honest options are.
