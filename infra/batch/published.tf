# What this layer offers whoever submits a job. Same mechanism as every other layer: a short,
# enumerable reference table in SSM, so a submitter resolves names rather than transcribing them.
#
# **A submitter, not another layer.** Nothing in this estate depends on the batch layer — that is
# the point of it being opt-in — so these exist for `scripts/reprocess_submit.py`, which is an
# operational action rather than part of any deploy.

locals {
  published = {
    application_id = aws_emrserverless_application.reprocessing.id
    job_role_arn   = aws_iam_role.job.arn
    logs_group     = aws_cloudwatch_log_group.jobs.name
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:An application id, a role ARN and a log group name. None is a secret.
  #checkov:skip=CKV_AWS_337:Same reason — this is a cross-layer reference table, not a secret store.
  for_each = local.published

  name        = "/${var.project}/batch/${each.key}"
  description = "Reference published by infra/batch."
  type        = "String"
  value       = each.value
}
