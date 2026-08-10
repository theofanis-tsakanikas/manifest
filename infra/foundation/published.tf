# What this layer offers its neighbours, published where they can read it.
#
# The alternative this replaces was a CI artifact: `terraform output -json` written to
# `foundation.json`, uploaded, downloaded by each later job, and unpacked with `jq` into `-var`
# flags. It worked for one workflow run and for nothing else — dispatching `lakehouse` on its
# own found no artifact, and `infra/batch` and `infra/analytics` had no path at all because
# there was no run that had produced one for them.
#
# The alternative it *also* replaces is `terraform_remote_state`, and that one is worse for a
# reason worth stating: it needs every consuming layer to hold read access to the state bucket,
# which turns the blast radius of a bug in the smallest layer into the blast radius of the
# largest. `../attestor/infra/foundation/main.tf` says the same thing and says it first.
#
# What a layer offers its neighbours should be a short, deliberate, enumerable list. This is
# that list, and adding to it is a visible change.

locals {
  published = {
    vpc_id = aws_vpc.main.id
    # JSON, not a comma-joined string. A consumer declares this as `list(string)`, and
    # Terraform parses a `TF_VAR_` holding `["subnet-a","subnet-b"]` straight into one — a
    # comma-joined value would fail to parse, or worse, be declared as a string and split in
    # the consumer, which puts the list's shape in two places.
    private_subnet_ids         = jsonencode([for subnet in aws_subnet.private : subnet.id])
    endpoint_security_group_id = aws_security_group.endpoints.id
    data_key_arn               = aws_kms_key.data.arn
    logs_key_arn               = aws_kms_key.logs.arn
    landing_bucket             = aws_s3_bucket.zone["landing"].id
    records_bucket             = aws_s3_bucket.zone["records"].id
    evidence_bucket            = aws_s3_bucket.zone["evidence"].id
    access_logs_bucket         = aws_s3_bucket.access_logs.id
    alerts_topic_arn           = aws_sns_topic.alerts.arn
    reader_repository_url      = aws_ecr_repository.reader.repository_url
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:A VPC id, four bucket names and three ARNs. None is a secret, and a SecureString would add a KMS grant to every consuming layer in exchange for encrypting facts already visible in the console to anyone who can read them here.
  #checkov:skip=CKV_AWS_337:Same reason. Secrets live in Secrets Manager; this is a cross-layer reference table.
  for_each = local.published

  name        = "/${var.project}/foundation/${each.key}"
  description = "Cross-layer reference published by infra/foundation."
  type        = "String"
  value       = each.value
}
