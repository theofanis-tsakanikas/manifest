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

  # The escalation tiers. Tier 0 is not here: it is a function this layer owns, invoked below,
  # and the whole point of the cascade is that it costs nothing per page.
  #
  # `comprehend:DetectEntities` was here and is gone (2026-08-10). Entity recognition in this
  # system has exactly one right answer per declared rule, so it is deterministic code in
  # `core/entities.py`, and the service reads neither Greek nor Dutch. A grant for a service
  # nothing calls is a permission nobody will remember to remove.
  #
  # Textract takes no resource ARN for these actions. Bedrock does — a model ARN — so it is
  # scoped, in its own statement, below.
  #checkov:skip=CKV_AWS_111:The Textract document APIs are documented as not resource-scoped; the constraint is the role's narrowness, not a resource pattern the API does not support.
  #checkov:skip=CKV_AWS_356:As above — a "*" resource on an API with no resource ARNs is the only expressible form.
  statement {
    sid    = "EscalateToPerPageOcr"
    effect = "Allow"
    actions = [
      "textract:DetectDocumentText",
      "textract:AnalyzeDocument",
      "textract:StartDocumentTextDetection",
      "textract:GetDocumentTextDetection",
    ]
    resources = ["*"]
  }

  # Scoped to the models this system may call, by ARN, rather than to the service.
  #
  # `bedrock:InvokeModel` on `*` is a grant to invoke **any** model in the account, including
  # ones with different pricing, different data-handling terms and different regional
  # footprints. The escalation tier calls one model; naming it is the difference between a
  # budget guard that means something and one that guards a service.
  statement {
    sid    = "EscalateToTheModelTier"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:Converse",
    ]
    resources = var.escalation_model_arns
  }

  # Document automation, which is per page and takes a project ARN.
  #
  # `dynamic` rather than a plain statement, because the list may legitimately be empty — the
  # tier is reachable only by a second escalation the routing model does not attempt today. An
  # empty `resources` renders a statement with no `Resource`, which `terraform validate` accepts
  # and IAM rejects at apply time: a malformed-policy error four minutes in, with the approval
  # already spent. No statement at all is the correct rendering of "grants nothing".
  dynamic "statement" {
    for_each = length(var.document_automation_arns) > 0 ? [1] : []
    content {
      sid    = "EscalateToDocumentAutomation"
      effect = "Allow"
      actions = [
        "bedrock:InvokeDataAutomationAsync",
        "bedrock:GetDataAutomationStatus",
      ]
      resources = var.document_automation_arns
    }
  }

  # Invoke the three functions this layer creates, by ARN. Naming them rather than granting
  # `lambda:InvokeFunction` on `*` is what stops this role being a general-purpose invoker of
  # anything anybody later deploys into the account.
  statement {
    sid     = "InvokeThePipelineFunctions"
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.read_tier0.arn,
      aws_lambda_function.publish.arn,
      aws_lambda_function.provenance_gate.arn,
    ]
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
    Comment = "Read at tier 0 with the local reader, extract and threshold, gate on provenance, then publish or queue."
    StartAt = "ReadAtTierZero"
    States = {
      # **Tier 0 is the local reference reader, not a metered service.**
      #
      # This step used to invoke the per-page OCR API under this name. That is tier 1 wearing
      # tier 0's name, and it deletes the cascade's reason for existing: the local reader is
      # precisely what keeps the metered engine off the pages that do not need it. A cost model
      # whose cheapest tier is a paid service is a cost model for a different system.
      #
      # It is also the same build, from the same image, that produced `recordings/ocr/` — which
      # is what makes every derived threshold in this repository a statement about the reader
      # that actually runs here rather than about a laptop nobody else has.
      ReadAtTierZero = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          "FunctionName" : aws_lambda_function.read_tier0.arn,
          "Payload.$" : "$"
        }
        ResultSelector = { "reading.$" : "$.Payload" }
        ResultPath     = "$.tier0"
        Retry = [{
          # Only the transport, never the logic. `Lambda.ServiceException` and its siblings are
          # the runtime failing to deliver the invocation; a `HandlerError` is this system
          # refusing something and retrying it would just refuse it again, three times, slower.
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "ReadingFailed", ResultPath = "$.error" }]
        Next  = "ExtractAndThreshold"
      }

      # **The step that did not exist.** Every derived threshold, every contract and every
      # abstention rule lived in `src/manifest/core/` and ran only on a laptop; the deployed
      # pipeline had no state that executed any of it. A pipeline whose extraction logic never
      # runs publishes whatever the reader said.
      ExtractAndThreshold = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          "FunctionName" : aws_lambda_function.publish.arn,
          "Payload" : {
            "reading.$" : "$.tier0.reading.reading",
            "document_type.$" : "$.document_type",
            "language.$" : "$.tier0.reading.language"
          }
        }
        ResultSelector = { "outcome.$" : "$.Payload" }
        ResultPath     = "$.extraction"
        Retry = [{
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "ExtractionFailed", ResultPath = "$.error" }]
        Next  = "AnythingPublishable"
      }

      # Nothing cleared its threshold, so there is nothing for the gate to check. Straight to the
      # queue — and *not* through the gate, because a gate invoked on an empty set returns
      # `verified: true`, and a run that verified nothing must not be recorded as a run that
      # verified everything.
      AnythingPublishable = {
        Type = "Choice"
        Choices = [{
          Variable           = "$.extraction.outcome.publishable_count"
          NumericGreaterThan = 0
          Next               = "VerifyProvenance"
        }]
        Default = "QueueForReview"
      }

      # Claim 2's gate, in the path rather than beside it. A field whose box does not verify
      # never reaches the record — the machine cannot publish past this state, which is what
      # makes "a published field that cannot be located is a build failure" a property of the
      # pipeline rather than of a report somebody reads afterwards.
      #
      # **There is no `Catch` here, on purpose.** The previous version caught `States.ALL` and
      # sent the document to review — which meant a gate that was not deployed at all failed
      # invisibly and every document went to a human while the machine reported success. The
      # gate itself fails closed field by field; a failure to *invoke* it must stop the
      # execution, loudly, where an alarm can see it.
      VerifyProvenance = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          "FunctionName" : aws_lambda_function.provenance_gate.arn,
          "Payload" : {
            "document_id.$" : "$.extraction.outcome.document_id",
            "document_type.$" : "$.extraction.outcome.document_type",
            "language.$" : "$.tier0.reading.language",
            "fields.$" : "$.extraction.outcome.fields"
          }
        }
        ResultSelector = { "checked.$" : "$.Payload" }
        ResultPath     = "$.provenance"
        Retry = [{
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Next = "PublishableOrReview"
      }

      # One boolean, computed inside the gate. A Choice that had to scan the per-field list
      # would be a rule expressed in Amazon States Language, where no test in this repository
      # can reach it and no mutation can attack it.
      PublishableOrReview = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.provenance.checked.verified"
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
          # Keyed by document *and* fingerprint. Doctrine rule 4: a correction never erases what
          # was previously published, so a re-extraction writes a new object beside the old one
          # rather than over it, and both stay retrievable.
          "Key.$" : "States.Format('records/{}/{}.json', $.extraction.outcome.document_id, $.extraction.outcome.fingerprint)",
          "Body.$" : "$.provenance.checked",
          "ServerSideEncryption" : "aws:kms",
          "SsekmsKeyId" : var.data_key_arn
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

      # Two distinct failure states rather than one.
      #
      # A document that could not be *read* and a document whose *extraction* failed are
      # different operational problems — the first is a corrupt file or a missing language, the
      # second is a contract or a threshold artefact that does not match the deployment. Merging
      # them into one `Fail` would put both in the same alarm and make neither actionable.
      ReadingFailed = {
        Type  = "Fail"
        Error = "ReadingFailed"
        Cause = "The tier-0 reader could not produce a reading. The document is not published and is not queued: there is nothing to review."
      }

      ExtractionFailed = {
        Type  = "Fail"
        Error = "ExtractionFailed"
        Cause = "Extraction or thresholding failed. Most often a contract or threshold artefact that does not match this deployment."
      }
    }
  })
}
