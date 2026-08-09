# Iceberg tables over the records zone, a Glue catalogue, and an Athena workgroup.
#
# **Iceberg because of claim 3, not because it is current.** Re-extraction produces a new
# version of a record and the prior one stays retrievable; a table format with snapshots and
# time travel makes "what did we say before" a query rather than an archaeology project. A
# plain Parquet dataset would need the versioning rebuilt by hand on top of it, and the
# hand-built one is the one that has a hole in it.

resource "aws_s3_bucket" "lake" {
  bucket = "${var.project}-lake-${data.aws_caller_identity.current.account_id}"

  #checkov:skip=CKV_AWS_144:Single-Region estate; the lake is derived from the records zone and is rebuildable.
  #checkov:skip=CKV2_AWS_62:Nothing consumes object events on the lake.
  #checkov:skip=CKV_AWS_18:Access logging is configured in aws_s3_bucket_logging.lake below; the scanner does not resolve the split-resource pattern back to the bucket.
  #checkov:skip=CKV2_AWS_61:Lifecycle is configured in its own resource below.
  #checkov:skip=CKV2_AWS_6:Configured in aws_s3_bucket_public_access_block.lake.
  #checkov:skip=CKV_AWS_145:Configured in aws_s3_bucket_server_side_encryption_configuration.lake.
  #checkov:skip=CKV_AWS_21:Configured in aws_s3_bucket_versioning.lake.
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.data_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "lake" {
  bucket        = aws_s3_bucket.lake.id
  target_bucket = var.access_logs_bucket
  target_prefix = "lake/"
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "expire-old-snapshots"
    status = "Enabled"
    filter {}
    # Snapshots, not records. Iceberg's expired metadata files are what this removes; the
    # records themselves live in the records zone, which has no expiry because UCC Art. 51's
    # period has not been read from the national instrument (`docs/REGULATORY.md`).
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

data "aws_iam_policy_document" "lake" {
  statement {
    sid       = "DenyUnencryptedTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
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

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake.json
}

resource "aws_glue_catalog_database" "records" {
  name        = "${replace(var.project, "-", "_")}_records"
  description = "Published document records and their versions, as Iceberg tables"
}

# The published record. One row per (document, version) — never one per document, because
# claim 3 is that a correction produces a new version and the prior one stays retrievable, and
# a table keyed by document alone cannot express that without deleting the thing it is about.
resource "aws_glue_catalog_table" "document_version" {
  name          = "document_version"
  database_name = aws_glue_catalog_database.records.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "table_type"     = "ICEBERG"
    "format"         = "parquet"
    "classification" = "parquet"
  }

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${aws_s3_bucket.lake.id}/document_version/"

    columns {
      name    = "document_id"
      type    = "string"
      comment = "shipment/document — the join key across the six types"
    }
    columns {
      name    = "version"
      type    = "string"
      comment = "Derived from content; the same input always produces the same identifier"
    }
    columns {
      name    = "supersedes"
      type    = "string"
      comment = "The version this replaces. A chain, not a pointer to current: the question an auditor asks is what you said before"
    }
    columns {
      name    = "reader"
      type    = "string"
      comment = "Opaque reader identity. Part of the version, because a reader upgrade is a different reader for every purpose"
    }
    columns {
      name = "field"
      type = "string"
    }
    columns {
      name    = "value"
      type    = "string"
      comment = "NULL where the system abstained. Missing is missing; there is no default"
    }
    columns {
      name = "confidence"
      type = "double"
    }
    columns {
      name    = "threshold"
      type    = "double"
      comment = "The derived threshold at publication time. Stored, not looked up: a threshold that moved later must not silently re-judge a record published under the old one"
    }
    columns {
      name = "page"
      type = "int"
    }
    columns {
      name    = "box"
      type    = "array<double>"
      comment = "left, top, width, height as fractions of the page. Claim 2's provenance"
    }
    columns {
      name = "provenance_verified"
      type = "boolean"
    }
    columns {
      name    = "review_decision"
      type    = "string"
      comment = "approved, corrected, supplied, rejected — or NULL. SUPPLIED is distinct from APPROVED: doctrine rule 7"
    }
    columns {
      name = "extracted_on"
      type = "timestamp"
    }
  }

  partition_keys {
    name = "extraction_date"
    type = "date"
  }
}

resource "aws_athena_workgroup" "analysis" {
  name = "${var.project}-analysis"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    # A byte ceiling per query. Athena bills per terabyte scanned, and the failure it prevents
    # is not a slow dashboard — it is a `SELECT *` against four million documents that costs
    # more than the extraction did.
    bytes_scanned_cutoff_per_query = 10 * 1024 * 1024 * 1024

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.id}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.data_key_arn
      }
    }
  }
}
