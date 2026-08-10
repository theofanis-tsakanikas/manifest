# The review queue. **Ours, not a managed labelling service** — `docs/AWS-CONSTRAINTS.md`:
# Amazon A2I closed to new customers on 2026-07-30, so it is unavailable to this operator.
#
# That is the better outcome and ADR-0001 says why. Claim 5 is not "documents reach a human";
# it is that the queue has a declared finite capacity, that exceeding it fails the build, and
# that reviewer integrity is measured. Those are properties of a queue's *design*, and a managed
# worker pool supplies none of them.

# **This queue has no consumer in this estate, and that is a decision rather than an omission.**
#
# Stated here because "a queue nothing reads" is indistinguishable, from the outside, from the
# gap this repository has now found five times: something written, validated, scanned clean and
# inert. The difference is that the others were accidents and this one is not.
#
# The consumer of a review queue is a **reviewing interface** — a screen showing a human the page
# crop, the proposed value, the field's contract and the reason it was queued, and recording what
# they decided and how long they looked at it. That is an application, and building one here would
# be building a product this project is not about.
#
# What the project *does* claim about review is claim 5, and every part of it is proved offline in
# `evals/review/`: nothing publishes below its threshold without a recorded decision, a field with
# no provenance cannot be approved into existence, the queue's declared capacity is measured
# against, and rubber-stamping is detected. Those are properties of the decision *record* and the
# capacity *model*, and they hold whether the screen exists or not.
#
# So the queue is the interface, deliberately: it holds the item, its reason, its provenance and
# its contract, encrypted, for fourteen days, with a dead-letter queue behind it. A consumer
# reads it — and until one does, items accumulate visibly rather than disappearing, which is the
# right failure for a system whose doctrine says abstention is the safe state and that a queue
# past capacity is a failure of the system rather than of the reviewers.
#
# **What would be dishonest** is a Lambda here that auto-approved anything, or a consumer that
# drained the queue to keep a dashboard green. Doctrine rule 5: nothing approves itself.
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
