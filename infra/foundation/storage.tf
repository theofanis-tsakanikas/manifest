# The data zones, and the keys that encrypt them.
#
# Three buckets rather than three prefixes in one, because the retention rules genuinely differ
# and a lifecycle rule scoped by prefix is a rule somebody edits without noticing what else is
# under it.
#
#   landing   the page images as they arrived. Working data.
#   records   the published records and their versions. **The customs record** — UCC Art. 51.
#   evidence  crops and provenance artefacts a reviewer or an auditor is shown.

locals {
  buckets = {
    landing = {
      purpose   = "page images as received; working data, not the record"
      retention = var.retention_days
    }
    records = {
      # No expiry here at all. UCC Art. 51 sets a floor of at least three years from the end of
      # the relevant year and national law may extend it, and this repository has not read the
      # Dutch or Greek instruments (`docs/REGULATORY.md`). A lifecycle rule written to a period
      # nobody verified would delete a record the operator is obliged to keep — so the rule is
      # absent and its absence is deliberate, which is the safe direction for this one bucket.
      purpose   = "published records and their versions; the customs record"
      retention = 0
    }
    evidence = {
      purpose   = "crops and provenance artefacts shown to a reviewer"
      retention = var.retention_days
    }
  }
}

resource "aws_kms_key" "data" {
  description             = "Encrypts ${var.project} document data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

# An explicit policy, for the same reason the bootstrap key has one: the default grants the
# account root full access, which is not wrong and is not written down anywhere a reviewer
# reads. The service grants are added by the layers that need them, through grants rather than
# by widening this — a key whose policy names every service that might one day touch the data
# is a key nobody can reason about.
data "aws_iam_policy_document" "data_key" {
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key.
  #checkov:skip=CKV_AWS_356:As above — "*" here is the key itself, not every key.
  #checkov:skip=CKV_AWS_109:The root statement keeps the key administrable; omitting it orphans it.

  statement {
    sid       = "AccountRootAdministersTheKey"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_kms_key_policy" "data" {
  key_id = aws_kms_key.data.id
  policy = data.aws_iam_policy_document.data_key.json
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.project}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "logs" {
  description             = "Encrypts ${var.project} log groups"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.project}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

# CloudWatch Logs encrypts with a key it must be granted use of, by service principal and
# scoped to this account's log groups. Without the condition the grant is to the service
# everywhere, which is a wider key than the logs need.
data "aws_iam_policy_document" "logs_key" {
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key.
  #checkov:skip=CKV_AWS_356:As above — "*" here is the key itself, not every key.
  #checkov:skip=CKV_AWS_109:The root statement is what keeps the key administrable; omitting it orphans the key.

  statement {
    sid       = "AccountRootAdministersTheKey"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid    = "CloudWatchLogsUsesTheKey"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
      ]
    }
  }
}

resource "aws_kms_key_policy" "logs" {
  key_id = aws_kms_key.logs.id
  policy = data.aws_iam_policy_document.logs_key.json
}

resource "aws_s3_bucket" "access_logs" {
  bucket = "${var.project}-access-logs-${data.aws_caller_identity.current.account_id}"

  #checkov:skip=CKV_AWS_18:This IS the access-log bucket. A bucket logging to itself is a loop.
  #checkov:skip=CKV_AWS_144:Access logs are not worth a second Region; the data they describe is versioned and reproducible.
  #checkov:skip=CKV_AWS_145:SSE-S3 deliberately — S3 access logging cannot deliver into a bucket encrypted with a KMS key it holds no grant for, and granting the service use of the data key widens it to gain nothing.
  #checkov:skip=CKV2_AWS_62:One notification per log object, with nothing consuming them.
  #checkov:skip=CKV2_AWS_61:Lifecycle is configured in its own resource below, which is where the provider wants it.
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration { days = var.retention_days }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

# ── The zones ────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "zone" {
  for_each = local.buckets

  bucket = "${var.project}-${each.key}-${data.aws_caller_identity.current.account_id}"

  #checkov:skip=CKV_AWS_144:A single-Region estate. Cross-region replication guards against a Regional loss at the cost of a second Region's storage and a replication role, and the recovery it enables has never been exercised — which makes it a control in name. Versioning, which has been reasoned about, stays.
  #checkov:skip=CKV2_AWS_62:Event notifications are configured by the extraction layer on the bucket that needs them; a notification with no consumer is a control that cannot fail.
  #checkov:skip=CKV2_AWS_61:Lifecycle is configured in its own resource below, conditionally — the records bucket deliberately has none.
  #
  # The five below are one limitation, not five decisions. Every one of these controls IS
  # configured, in its own `aws_s3_bucket_*` resource further down this file, which is the
  # pattern the AWS provider has required since v4. checkov resolves those back to the bucket
  # when the bucket is a single resource and does not when it is a `for_each` — so it reports
  # the configuration as absent on each key. The resources are named directly here so a
  # reviewer can check the claim rather than take it:
  #checkov:skip=CKV2_AWS_6:Configured in aws_s3_bucket_public_access_block.zone, all four flags true.
  #checkov:skip=CKV_AWS_145:Configured in aws_s3_bucket_server_side_encryption_configuration.zone, aws:kms with the customer key.
  #checkov:skip=CKV_AWS_21:Configured in aws_s3_bucket_versioning.zone, Enabled.
  #checkov:skip=CKV_AWS_18:Configured in aws_s3_bucket_logging.zone, into the access-log bucket.
}

resource "aws_s3_bucket_public_access_block" "zone" {
  for_each = aws_s3_bucket.zone

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "zone" {
  for_each = aws_s3_bucket.zone

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "zone" {
  for_each = aws_s3_bucket.zone

  bucket = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "zone" {
  for_each = aws_s3_bucket.zone

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "zone" {
  for_each = aws_s3_bucket.zone

  bucket        = each.value.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "${each.key}/"
}

resource "aws_s3_bucket_lifecycle_configuration" "zone" {
  # Only the zones that declare a retention. The records bucket declares zero and is skipped
  # entirely, because a lifecycle rule written to a period nobody has verified against the
  # national instrument would delete a record the operator is obliged to keep.
  for_each = { for name, config in local.buckets : name => config if config.retention > 0 }

  bucket = aws_s3_bucket.zone[each.key].id

  rule {
    id     = "retire-${each.key}"
    status = "Enabled"
    filter {}
    expiration { days = each.value.retention }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

data "aws_iam_policy_document" "zone" {
  for_each = aws_s3_bucket.zone

  statement {
    sid     = "DenyUnencryptedTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      each.value.arn,
      "${each.value.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "zone" {
  for_each = aws_s3_bucket.zone

  bucket = each.value.id
  policy = data.aws_iam_policy_document.zone[each.key].json
}
