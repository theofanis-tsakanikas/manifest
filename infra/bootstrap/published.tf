# What the deploy needs to know, published where the deploy can read it.
#
# Terraform outputs live in this layer's state file, which sits on the laptop that applied it.
# CI never sees them. Without this file every value below has to be transcribed into repository
# configuration by hand — and a transcribed value looks like an independent setting. Rename the
# state bucket here and the deploy fails on a backend nobody can find, with the fix in a
# settings page rather than in a diff.
#
# This is the same pattern as `../watermark/infra/bootstrap/published.tf`, and it is here
# because the first version of this repository's deploy did **not** have it: `deploy.yml`
# referenced four repository variables — the role ARN, the state bucket, the access-log bucket
# and the alert address — every one of them a name this layer had already chosen. The
# access-log bucket was the worst of the four, because `infra/foundation` creates it and did not
# even output it, so the variable had to be hand-typed to match a name Terraform computed.
#
# What **cannot** be published is the account id: CI has to know which account before it can ask
# that account anything, and reading a parameter is already asking. That one stays a repository
# variable — one irreducible value rather than four transcribed ones.
#
# The path is `/<project>/bootstrap/<name>`, which is exactly the prefix the deploy role is
# granted in `deploy_permissions.tf` and nothing wider.

locals {
  published = {
    state_bucket         = aws_s3_bucket.state.id
    state_kms_key_arn    = aws_kms_key.state.arn
    deploy_role_arn      = aws_iam_role.deploy.arn
    escalation_model_arn = var.escalation_model_arn
    # Computed here rather than in `foundation`, because the deploy needs it *before* foundation
    # has run — the lakehouse layer takes it as a variable and the name is deterministic. A
    # value two layers can compute is a value that will be computed differently in one of them.
    access_logs_bucket = "${var.project}-access-logs-${data.aws_caller_identity.current.account_id}"
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:A bucket name, a key ARN and a role ARN. None is a secret — the boundary is the OIDC trust policy, scoped to one repository and one environment, not the confidentiality of these strings. The one value that IS personal data is a SecureString below.
  #checkov:skip=CKV_AWS_337:Same reason. A customer-managed key to encrypt a bucket name buys nothing and costs a euro a month.
  for_each = local.published

  name        = "/${var.project}/bootstrap/${each.key}"
  description = "Published by infra/bootstrap so the deploy resolves it instead of transcribing it."
  type        = "String"
  value       = each.value
}

# The one published value that is personal data.
#
# Everything above is a name this layer chose. An address belongs to a person, and this project
# fails closed on personal data rather than reasoning about who can already read the account —
# the same rule the document contracts apply to a consignee.
#
# Encrypted with this layer's own key rather than the AWS-managed SSM one: same layer, same
# lifecycle, same blast radius, and the deploy role's `UseTheStateKey` grant already covers it.
# The alternative was a second grant on a key nobody here controls, to save a euro a month on a
# key that is already being paid for.
resource "aws_ssm_parameter" "budget_notification_email" {
  name        = "/${var.project}/bootstrap/budget_notification_email"
  description = "Destination for the foundation layer's budget alarm and the expiry rule."
  type        = "SecureString"
  key_id      = aws_kms_key.state.arn
  value       = var.budget_notification_email

  # The key existing is not permission to use it. See `scripts/check_deploy_path.py` — a first apply orders these two however it likes.
  depends_on = [aws_kms_key_policy.state]
}
