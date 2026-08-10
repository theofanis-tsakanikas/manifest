# Day one — the manual work that has no API

**Day one happened on 2026-08-10**, and this file was written before it. Everything below the
line was a *prediction* about what a first deploy would need; the section at the end records
what actually happened, including the part no item on this list saw coming.

The list held up better than the reason for keeping it. Every item here is manual because it
has no Terraform resource or no API, not because automating it was inconvenient — and each says
which. What the day proved is the narrower and more useful point: **a written-down manual step
is a step somebody performs; an unwritten one is a step somebody performs and forgets, and the
next person spends four minutes of a deploy discovering it.**

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
| 7a | **Nothing. This one is automated now, and the reason is the day-one finding below** | It used to read "check the reader version in the `Dockerfile` matches the one that produced `recordings/ocr/`" — a manual check, on a list, in a file. It was not done, the versions did not match, and the deploy went green. `scripts/reader_version_check.py` now runs in CI on every push, and `make ocr-record` refuses to record with a reader the estate cannot run. A manual step that decides every threshold in the repository was the wrong kind of manual step |
| 8 | **Dispatch `deploy` with `include_expensive_layers` off first** | EMR Serverless and the Redshift workgroup are the estate's cost, and they are opt-in for that reason. The first run should stand up foundation, extraction and lakehouse and stop; the two expensive layers are a second, deliberate dispatch with their own approval, used and then torn down |
| 9 | **Set `expires_at` on every layer** | Every layer above `bootstrap` requires it and there is no default meaning "never". The reaper is the only thing between a portfolio estate and a monthly bill |

## What is deliberately *not* here

**No console click that a resource could have made.** If an item appears on this list because
automating it was tedious, it is in the wrong place — it belongs in Terraform.

**No screenshot, no timing, no euro figure.** Still none, and now for a better reason than
"there is no estate": every claim in this repository is scored offline and stays that way, so a
console capture would be decoration next to a command anybody can run. The wall-clock figures
that do exist from the first deploy are stated as what they are — one run, one day, one
account — and no cost figure has changed category. See `docs/DECISIONS.md` 14 and 15.

**No "run it once to see".** The first apply was deliberate, dispatched by hand, with an
expiry, and torn down. That is not the same as running it to check.

## Tearing it down

`destroy.yml` exists, is scoped to its own environment, and has now been dispatched. It is not
optional and it is not a follow-up: a repository with a deploy path and no teardown path is how
an estate gets left standing, and it is the difference between a portfolio piece and a bill.

Running it is what proved that writing it was not enough. See the day-one record below: it
would have emptied nothing and reported success.

The state bucket carries `prevent_destroy` and outlives the estate on purpose — it is the
record of what existed. Removing it is a deliberate, separate act, and `infra/bootstrap/`'s
README says what the two honest options are.

---

## What day one was actually like — 2026-08-10

Recorded because the list above is a prediction, and a prediction is only worth keeping if
somebody says afterwards how it did.

### What the list got right

Every item on it was real. Items 5a and 5a2 — the numeric GitHub ids and the named model ARN —
were the two that would have been quietly wrong rather than loudly broken, and having them
written down is why they were not. Item 8's `include_expensive_layers` default did its job: the
estate that stood up was foundation, extraction and lakehouse, and the two expensive layers were
never asked for.

### What it did not see at all

**Twenty-four cycles of the deploy failed before one went green.** Not one of them was on this
list, and they fall into three families.

**Fourteen missing IAM grants.** The deploy role could create a thing and not use it, administer
a thing and not write into it, delete an object and not its versions. `scripts/check_deploy_path.py`
proves a grant exists for every service a layer declares; it cannot prove any grant is
*sufficient*, and only a real apply says. That limit was written down in
`contracts/deploy/acceptance.yaml` before the first dispatch, and it was the accurate part of
that file. The three that cost most were `kms:GenerateDataKey` (managing a key is not using
it), `s3:PutObject` (administering a bucket is not writing into it), and `s3:DeleteObjectVersion`
(deleting an object is not deleting its versions). It is one distinction, three times.

**Two limits nothing in the documentation mentions.** IAM's 10,240-byte cap on inline role
policies is an *aggregate* across a role, not per policy — the fix was six managed policies.
And Glue's Iceberg tables keep the partition spec in their own metadata rather than in the
catalogue, so `table_type` and `metadata_location` are reserved and a partition change there
would have dropped a column.

**One packet that was never refused.** The first four documents to reach the deployed pipeline
each hung for the full ten-minute Lambda timeout at 106 MB — no work, no log line past `START`.
The route table sent the packet to the S3 gateway endpoint correctly; the security group then
dropped it, because a gateway endpoint answers on a *public* prefix and egress was allowed only
to the VPC CIDR. Nothing errors in that arrangement. A dropped packet is not a refusal: no RST,
no 403, no message naming a permission — just a socket that never answers.

### The finding that mattered more than all of them

**The recording was made by a binary the estate cannot run.**

`recordings/ocr/` was produced on the author's laptop by Homebrew's `tesseract 5.5.2`. The image
is Debian, which carries `5.5.0` — as does Debian sid, as does every Ubuntu including the
unreleased one. No Linux distribution ships 5.5.2. Every threshold in this repository was a
statement about a reader the deployed pipeline could never be.

The image asserted only the reader's *series*, on the written argument that a patch release does
not move confidences — an assertion about a distribution nobody here had measured. Every offline
check reads `recordings/` directly and so never met the deployed binary. The deploy went green.
The first document that cleared the network failed on `s3:ListBucket`, four layers from the
cause, in a message containing no word about readers.

The refusal itself was correct: the extraction handler looks its thresholds up by the reader's
exact identity, because two readers' 0.8 are different events. It arrived late and unreadable
because the check that belonged offline did not exist.

Three things changed, and the first is the one that matters:

- **The ceremony moved into the image.** `.github/workflows/record.yml` builds `Dockerfile` and
  runs the reader inside it, so the recording's reader and the estate's reader are the same
  binary by construction. `Dockerfile` had claimed exactly this since it was written, and the
  sentence was false.
- The image asserts the **exact** version, and `scripts/reader_version_check.py` proves it
  agrees with the recording, offline, on every push.
- `make ocr-record` refuses to record with a reader the estate cannot run, because running it on
  a laptop is how this happened.

### And the teardown would have left everything standing

Found by reading `destroy.yml` against the account rather than by running it. It emptied its
buckets with `aws s3 rm --recursive || true`. All five are versioned, and on a versioned bucket
that command deletes nothing — it writes a delete marker over each current version and leaves
the noncurrent ones in place, so the bucket ends up holding more than it started with.
`terraform destroy` then fails on `BucketNotEmpty`, and the `|| true` has already discarded the
only message that would have explained it.

Its final step had the same shape: it printed everything still tagged for the project, said
"anything listed above still exists and still costs money", and exited zero — and called the
tagging API without `tag:GetResources`, a failure that did not fail the step because the step
ended in an `echo`. The report on whether an estate was left standing could not go red, and
could not run.

### The lesson worth keeping

Every one of these was invisible to `terraform validate`, to checkov at zero findings, and to a
suite of offline claims that all passed. They were not caught by being careful. They were caught
by applying the estate once, deliberately, with an expiry on it — and the cheapest of them was
caught by reading a workflow against the account it would run in, which cost nothing and should
have happened first.
