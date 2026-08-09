# The review queue. **Ours, not a managed labelling service** — `docs/AWS-CONSTRAINTS.md`:
# Amazon A2I closed to new customers on 2026-07-30, so it is unavailable to this operator.
#
# That is the better outcome and ADR-0001 says why. Claim 5 is not "documents reach a human";
# it is that the queue has a declared finite capacity, that exceeding it fails the build, and
# that reviewer integrity is measured. Those are properties of a queue's *design*, and a managed
# worker pool supplies none of them.

resource "aws_sqs_queue" "review" {
  name                              = "${var.project}-review"
  kms_master_key_id                 = var.data_key_arn
  kms_data_key_reuse_period_seconds = 300
  visibility_timeout_seconds        = var.review_visibility_seconds
  message_retention_seconds         = 1209600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.review_failed.arn
    maxReceiveCount     = var.review_max_receives
  })
}

resource "aws_sqs_queue" "review_failed" {
  name                              = "${var.project}-review-failed"
  kms_master_key_id                 = var.data_key_arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600
}

resource "aws_sqs_queue_redrive_allow_policy" "review_failed" {
  queue_url = aws_sqs_queue.review_failed.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.review.arn]
  })
}

# The decision record. A recorded human decision is what claim 5 rests on, so it is stored in
# something with a primary key and point-in-time recovery rather than appended to a log.
# `terraform validate` warns that `hash_key`/`range_key` are deprecated in favour of a
# `key_schema` block. They are kept, and the reason is worth writing down because it is exactly
# what validating against a real provider schema is for: the pinned provider **accepts the
# deprecated arguments and rejects `key_schema`** — "Blocks of type key_schema are not expected
# here". The replacement lands in a later release than the one this repository pins.
#
# A warning is not an error, and writing the new form against a provider that does not have it
# yet would turn a validating configuration into a broken one for the sake of a cleaner log.
# The pin moves first; the syntax follows it.
resource "aws_dynamodb_table" "decisions" {
  name         = "${var.project}-review-decisions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document_version"
  range_key    = "field"

  attribute {
    name = "document_version"
    type = "S"
  }
  attribute {
    name = "field"
    type = "S"
  }
  attribute {
    name = "reviewer"
    type = "S"
  }

  # The integrity metrics are per reviewer over a window, so the reviewer needs to be a key
  # rather than a scan. A control that requires a full table scan is a control that gets run
  # once a quarter.
  global_secondary_index {
    name            = "by-reviewer"
    hash_key        = "reviewer"
    range_key       = "document_version"
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true
}

# The reprocessing ledger — claim 7's idempotence, in a table. Keyed by (document, reader)
# rather than by document, because keyed by document alone a reader upgrade looks like work
# already done, and a four-million-document re-extraction silently does nothing.
resource "aws_dynamodb_table" "ledger" {
  name         = "${var.project}-reprocessing-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document"
  range_key    = "reader"

  attribute {
    name = "document"
    type = "S"
  }
  attribute {
    name = "reader"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true
}
