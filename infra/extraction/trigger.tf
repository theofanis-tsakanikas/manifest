# What starts an execution.
#
# **There was nothing.** The state machine existed, the queue existed, the buckets existed, and a
# document landing in the landing zone caused precisely nothing to happen. Written, validated,
# scanned to zero findings, and inert — the same family as the provenance gate that was invoked
# by a name nothing created, and just as invisible to every check that ran.
#
# S3 → EventBridge → Step Functions, rather than an S3 notification straight to the state
# machine, and the reason is not style: a bucket notification is a property *of the bucket*,
# which `infra/foundation` owns. Wiring it there would make the foundation layer name a state
# machine that a later layer creates — the cross-layer reference in the wrong direction, and
# undeletable in the right order. EventBridge lets the consuming layer own its own subscription.
#
# **The rule matches the landing convention and nothing else.** `incoming/<language>/<type>/…`
# is declared in `manifest.handlers.read_tier0.KEY_CONVENTION`, parsed there, and refused there
# if it does not match. The prefix filter here is an optimisation, not the control: it keeps the
# state machine from starting on an object that could never be processed, so a mistyped upload
# fails at the rule instead of consuming an execution and a Lambda invocation to say the same
# thing.

resource "aws_cloudwatch_event_rule" "document_landed" {
  name        = "${var.project}-document-landed"
  description = "A document arrived in the landing zone under the declared key convention."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.landing_bucket] }
      object = { key = [{ prefix = "incoming/" }] }
    }
  })

  tags = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_cloudwatch_event_target" "start_extraction" {
  rule     = aws_cloudwatch_event_rule.document_landed.name
  arn      = aws_sfn_state_machine.extraction.arn
  role_arn = aws_iam_role.trigger.arn

  # Only the bucket and the key. The language and the document type are parsed from the key by
  # the reader, once, in Python, where the parse is tested and where a key that does not match
  # is refused by name. An input transformer cannot split a string, so the alternatives were a
  # rule per document type or a default — and a default here reads a page in a language nobody
  # chose.
  input_transformer {
    input_paths = {
      bucket = "$.detail.bucket.name"
      key    = "$.detail.object.key"
    }
    input_template = <<-JSON
      {
        "bucket": "<bucket>",
        "key": "<key>"
      }
    JSON
  }

  # Where an event that could not start an execution goes.
  #
  # Without this a throttled or malformed invocation is retried a few times and then dropped, and
  # a dropped document is a customs record that silently does not exist. The queue is the
  # difference between "we did not process it" and "we do not know whether we processed it".
  dead_letter_config {
    arn = aws_sqs_queue.trigger_failures.arn
  }

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }
}

resource "aws_sqs_queue" "trigger_failures" {
  name                              = "${var.project}-trigger-failures"
  kms_master_key_id                 = var.data_key_arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600

  tags = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_sqs_queue_policy" "trigger_failures" {
  queue_url = aws_sqs_queue.trigger_failures.id
  policy    = data.aws_iam_policy_document.trigger_failures.json
}

data "aws_iam_policy_document" "trigger_failures" {
  statement {
    sid       = "EventBridgeMayReportItsOwnFailures"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.trigger_failures.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    # Scoped to this rule. Without it the queue accepts messages from every EventBridge rule in
    # the account, which is a confused-deputy shape and a free denial-of-service.
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.document_landed.arn]
    }
  }
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "trigger" {
  name               = "${var.project}-trigger"
  description        = "EventBridge starting the extraction machine. One action, one target."
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "trigger" {
  statement {
    sid       = "StartTheExtractionMachine"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.extraction.arn]
  }
}

resource "aws_iam_role_policy" "trigger" {
  name   = "trigger"
  role   = aws_iam_role.trigger.id
  policy = data.aws_iam_policy_document.trigger.json
}
