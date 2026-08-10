# bootstrap — the one layer applied from a laptop

It creates the two things CI cannot create for itself: the S3 backend every other layer keeps
its state in, and the IAM role GitHub Actions assumes. CI cannot create the role it needs in
order to run, so somebody has to, once — which is why this is the one layer whose design
permits a laptop apply.

**Applied on 2026-08-10, 31 resources**, deliberately, by the author. `docs/DECISIONS.md` 14.
It is also the layer that outlives every teardown: `destroy.yml` does not name it, because it
holds the state bucket, the lock table and the role the teardown assumes, and a teardown that
destroyed its own credentials halfway through would leave the rest standing and unreachable.

**Every other layer under `infra/` is applied only from a gated workflow.** A layer that can be
applied from a laptop is a layer that will drift, and the drift is discovered by a `plan` that
wants to destroy something nobody remembers creating. This layer is the exception because
somebody has to go first, and it is the only one.

## What it describes

| Resource | Why |
|---|---|
| `<project>-tfstate-<account>` | State. Versioned, KMS-encrypted, access-logged, TLS enforced, `prevent_destroy` |
| `<project>-tfstate-logs-<account>` | Access logs for the above, SSE-S3, expiring |
| `alias/<project>-tfstate` | The state key, rotating, with an explicit key policy |
| GitHub OIDC provider | Optional — set `create_oidc_provider = false` if the account already has one |
| `<project>-deploy` | The role CI would assume. Trusts this repository and named environments only |

No DynamoDB lock table: the S3 backend locks with a lock file (`use_lockfile = true`), which
puts the lock in the same bucket, under the same key, as the thing it protects.

The deploy role's trust list includes **`destroy`** from this first commit. A repository with a
deploy path and no teardown path is how an estate gets left standing, and the identity that
would tear it down belongs to the deploy path rather than to a follow-up commit.

## What it is checked with

```bash
make tf-validate   # terraform validate, -backend=false, no credentials
make iac-scan      # checkov, zero findings
```

Both run offline. `terraform validate` reaches the provider registry and nothing else. Green
means the configuration is well-formed and every attribute exists — **not** that every value
would be accepted; provider value validators run in the plan phase, and a plan needs
credentials this repository does not use. That limit is stated in `scripts/tf_validate.py`
and it is the whole of what "validated" is allowed to mean here.

## If the author ever does apply it

That is his run, not a claim this repository makes. The commands would be:

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply -var 'github_owner=<github-account>'
```

Then `terraform output backend_configuration`, pasted into each layer with its own `key` —
two layers sharing one state key is the mistake that output exists to prevent.

Its state would stay local, because this layer creates the backend and cannot store its state
in it. `.gitignore` covers `*.tfstate`: the file holds resource ids and the KMS key ARN, and
once it is in the history it is in the history.
