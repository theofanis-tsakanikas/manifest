# The state machine that reads a document, and the role it runs as.
#
# Step Functions rather than a chain of Lambdas calling each other: the escalation decision is
# a *state*, and a state machine makes "this page was kept at tier 0 and this one escalated" a
# thing the execution history records rather than a thing somebody reconstructs from logs. The
# cascade's routing distribution is claim 7's measured input, and a design where it is only
# visible in log lines is a design where that input is expensive to trust.

resource "aws_cloudwatch_log_group" "pipeline" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate. The customs record is in the records bucket, which has no expiry at all.
  name              = "/aws/vendedlogs/states/${var.project}-extraction"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    # Scoped to this account's state machines. Without it the trust is to the service globally,
    # which is a confused-deputy shape: any Step Functions state machine anywhere could assume
    # a role that reads this operator's documents.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "${var.project}-extraction"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid     = "ReadTheLandingZone"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.landing_bucket}",
      "arn:aws:s3:::${var.landing_bucket}/*",
    ]
  }

  statement {
    sid     = "WriteRecordsAndEvidence"
    effect  = "Allow"
    actions = ["s3:PutObject", "s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.records_bucket}/*",
      "arn:aws:s3:::${var.evidence_bucket}/*",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  # The readers. Textract and Bedrock take no resource ARN for these actions — the API is not
  # resource-scoped — so the grant is on the action and the boundary is the role, which is why
  # this role does exactly these three things and nothing else.
  #checkov:skip=CKV_AWS_111:Textract and Bedrock document these actions as not resource-scoped; the constraint is the role's narrowness, not a resource pattern that the API does not support.
  #checkov:skip=CKV_AWS_356:As above — a "*" resource on an API with no resource ARNs is the only expressible form.
  statement {
    sid    = "ReadDocuments"
    effect = "Allow"
    actions = [
      "textract:DetectDocumentText",
      "textract:AnalyzeDocument",
      "textract:StartDocumentTextDetection",
      "textract:GetDocumentTextDetection",
      "bedrock:InvokeModel",
      "comprehend:DetectEntities",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "QueueForReview"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.review.arn]
  }

  statement {
    sid    = "RecordDecisionsAndTheLedger"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.decisions.arn,
      "${aws_dynamodb_table.decisions.arn}/index/*",
      aws_dynamodb_table.ledger.arn,
    ]
  }

  statement {
    sid    = "Log"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    #checkov:skip=CKV_AWS_111:Step Functions' vended-log delivery API is documented as requiring these on "*"; the log group itself is named in the state machine's logging configuration.
    #checkov:skip=CKV_AWS_356:As above.
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "extraction"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

# The machine itself. The definition is data — the routing decision it encodes is the one
# `contracts/cascade/routing.yaml` declares, and the Choice state below is the *adapter* over
# `manifest.core.cascade`, not a second copy of the rule. A second copy is how the deployed
# behaviour and the tested behaviour drift apart while both look right.
resource "aws_sfn_state_machine" "extraction" {
  # The scanner wants `include_execution_data = true`, and it is false here on purpose.
  #
  # The execution data of this machine is the text of a commercial invoice a counterparty
  # wrote. Turning it on writes that text — party names, values, and any free-text field
  # somebody used to attempt an injection — into a log group, where it is retained, replicated
  # into whatever reads logs, and outside the retention class its contract declares. GDPR Art.
  # 5(1)(c) minimisation is the strongest control this system has, and it is not much of a
  # control if the pipeline copies the whole document into CloudWatch on the way past.
  #
  # Logging is *on* at level ALL: every state transition, every failure, every routing choice is
  # recorded. What is excluded is the payload, which is the part that is somebody else's
  # personal data rather than the part that says what the machine did.
  #checkov:skip=CKV_AWS_285:Execution logging is enabled at level ALL; the payload is excluded because it is the counterparty's document text, and copying it into a log group puts personal data outside the retention class its contract declares.
  name     = "${var.project}-extraction"
  role_arn = aws_iam_role.pipeline.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.pipeline.arn}:*"
    include_execution_data = false # Document text is a counterparty's content; it does not go in a log.
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  definition = jsonencode({
    Comment = "Read a document at tier 0, escalate or abstain per the routing contract, then gate on provenance."
    StartAt = "ReadAtTierZero"
    States = {
      ReadAtTierZero = {
        Type       = "Task"
        Resource   = "arn:aws:states:::aws-sdk:textract:detectDocumentText"
        Parameters = { "Document" : { "S3Object" : { "Bucket.$" : "$.bucket", "Name.$" : "$.key" } } }
        ResultPath = "$.reading"
        Next       = "RouteOnConfidence"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "QueueForReview", ResultPath = "$.error" }]
      }

      # The escalation decision. On confidence, never on a preprocessing rejection: both managed
      # readers share their preprocessing limits, so a page one refuses the other refuses for
      # the same reason and escalating there is a second bill for the same answer (ADR-0004).
      RouteOnConfidence = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.routing.route"
            StringEquals = "escalate"
            Next         = "Escalate"
          },
          {
            Variable     = "$.routing.route"
            StringEquals = "abstain"
            Next         = "QueueForReview"
          }
        ]
        Default = "VerifyProvenance"
      }

      Escalate = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:textract:analyzeDocument"
        Parameters = {
          "Document" : { "S3Object" : { "Bucket.$" : "$.bucket", "Name.$" : "$.key" } },
          "FeatureTypes" : ["FORMS", "TABLES"]
        }
        ResultPath = "$.escalated"
        Next       = "VerifyProvenance"
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "QueueForReview", ResultPath = "$.error" }]
      }

      # Claim 2's gate, in the path rather than beside it. A field whose box does not verify
      # never reaches the record — the machine cannot publish past this state, which is what
      # makes "a published field that cannot be located is a build failure" a property of the
      # pipeline rather than of a report somebody reads afterwards.
      VerifyProvenance = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          "FunctionName" : "${var.project}-provenance-gate",
          "Payload.$" : "$"
        }
        ResultPath = "$.provenance"
        Next       = "PublishableOrReview"
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "QueueForReview", ResultPath = "$.error" }]
      }

      PublishableOrReview = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.provenance.Payload.verified"
          BooleanEquals = true
          Next          = "Publish"
        }]
        Default = "QueueForReview"
      }

      Publish = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:s3:putObject"
        Parameters = {
          "Bucket" : var.records_bucket,
          "Key.$" : "$.recordKey",
          "Body.$" : "$.record"
        }
        End = true
      }

      QueueForReview = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Parameters = {
          "QueueUrl" : aws_sqs_queue.review.url,
          "MessageBody.$" : "$"
        }
        End = true
      }
    }
  })
}
