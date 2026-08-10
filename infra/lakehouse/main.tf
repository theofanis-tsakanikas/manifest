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
    # **A column, because it used to be a partition key and Iceberg refuses those.**
    #
    # Removing the `partition_keys` block would otherwise have deleted the field outright —
    # `extraction_date` was declared there and nowhere else, so the fix for the create error
    # would have silently dropped the column every date-bounded query in `analytics/` reads.
    # That is the shape worth noticing: the error was about partitioning and the damage would
    # have been to the schema.
    columns {
      name    = "extraction_date"
      type    = "date"
      comment = "The day the record was extracted. Iceberg partitions on it through its own spec, not through a catalogue partition key"
    }
  }

  # **No `partition_keys`, because Iceberg refuses them.** `CreateTable` returned *"Cannot
  # create partitions in an iceberg table"*.
  #
  # A Hive table declares its partitions to the catalogue and the catalogue enforces them. An
  # Iceberg table keeps its partition specification in its **own** metadata, evolves it without
  # rewriting data, and treats a catalogue-level partition key as a contradiction — which is one
  # of the reasons decision 12 chose Iceberg in the first place: claim 3 re-publishes a document
  # as a new version, and a partitioning scheme that could not change without a rewrite would
  # make that expensive.
  #
  # `extraction_date` is still how this table is read. It is a **column**, declared above, and
  # the partition specification over it is set through Athena once the table exists:
  #
  #     ALTER TABLE document_version SET TBLPROPERTIES (
  #       'write.spec' = 'day(extraction_date)'
  #     )
  #
  # That is a data-plane statement rather than a catalogue attribute, and it does not belong in
  # the layer that creates the table — which is exactly what the error was saying.
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
