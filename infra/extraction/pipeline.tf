# The state machine that reads a document, and the role it runs as.
#
# Step Functions rather than a chain of Lambdas calling each other, so that the escalation
# decision *can* be a state: "this page was kept at tier 0 and this one escalated" belongs in an
# execution history, not in log lines somebody reconstructs. The cascade's routing distribution
# is the cost model's measured input, and a design where it is visible only in logs is a design
# where that input is expensive to trust.
#
# **This machine has no escalation state, and the header above used to say it did.**
#
# The deployed states are `ReadAtTierZero`, `ExtractAndThreshold`, `VerifyProvenance`, and then
# publish or queue. Nothing routes a page upward, because routing a page upward means calling
# Textract or a Bedrock model — a billed API this repository has never called and does not call
# from an estate whose whole cost argument is that tier 0 is free. The escalation grants below
# exist so the role is ready for a machine that does; today they are permissions with no caller,
# and saying so is cheaper than discovering it from an execution history that never branches.
#
# The routing rule itself is real and is proved where it can be proved without a bill:
# `src/manifest/cascade/` decides, `contracts/cascade/routing.yaml` declares, and `evals/scale/`
# scores the distribution over the corpus offline. What the estate demonstrates is tier 0
# end to end. What it does not demonstrate is the escalation, and no figure here implies it.

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
    # The escalation function is in this list only when it exists. `concat` with a conditional
    # slice rather than a ternary, for the same reason the states use a filtered comprehension:
    # a list of three ARNs and a list of four are different shapes to Terraform.
    resources = concat(
      [
        aws_lambda_function.read_tier0.arn,
        aws_lambda_function.publish.arn,
        aws_lambda_function.provenance_gate.arn,
        # **Added with the landing state, and the omission is why there is now a check.** The
        # state referenced the function properly — by `.arn`, not by a name, which is what
        # `_check_nothing_names_what_nothing_creates` was written to enforce — and the role that
        # runs the machine still could not call it. Referencing a resource and being allowed to
        # invoke it are two facts, and only one of them was being checked.
        aws_lambda_function.land.arn,
      ],
      aws_lambda_function.index[*].arn,
      aws_lambda_function.escalate[*].arn,
    )
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
# **The escalation states, present only when the tiers they call are.**
#
# Merged into the machine rather than written into it, so that when `enable_escalation_tiers` is
# false they do not exist at all — not present-and-skipped. A dead state is a state somebody
# maintains, and an execution history full of branches that never fire is one nobody reads.
locals {
  escalation_states = {
    # Only the fields that abstained can be rescued, so a document where nothing abstained
    # never spends a billed call. `queued_count` is computed in `publish`, in Python, where a
    # test can reach it — a Choice that had to scan the field list would be a rule expressed
    # in Amazon States Language, which nothing in this repository can attack.
    AnythingEscalatable = {
      Type = "Choice"
      Choices = [{
        Variable           = "$.extraction.outcome.queued_count"
        NumericGreaterThan = 0
        Next               = "Escalate"
      }]
      Default = "AnythingPublishable"
    }

    # **No `Catch`, deliberately.** An escalation that fails must stop the execution rather
    # than fall through to the publish decision: falling through would publish a document on
    # tier-0 evidence while the history recorded that it had been escalated, and the two
    # would disagree with nobody watching. The fields that abstained are still abstaining;
    # the correct outcome of a broken escalation is a loud stop, not a quiet queue.
    Escalate = {
      Type     = "Task"
      Resource = "arn:aws:states:::lambda:invoke"
      Parameters = {
        # `one(...)` rather than `[0]`: a local is evaluated whether or not the states it builds are
        # merged in, so indexing a resource with `count = 0` fails at plan time with the flag
        # off — a configuration that cannot even be planned when its optional feature is
        # disabled. `one` yields null there, and the state is filtered out before it is used.
        "FunctionName" : one(aws_lambda_function.escalate[*].arn),
        "Payload.$" : "$"
      }
      ResultSelector = { "outcome.$" : "$.Payload.extraction.outcome", "escalation.$" : "$.Payload.escalation" }
      ResultPath     = "$.extraction"
      Retry = [{
        ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
        IntervalSeconds = 2
        MaxAttempts     = 3
        BackoffRate     = 2
      }]
      Next = "AnythingPublishable"
    }
  }
}

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
    # A filtered comprehension rather than a ternary: Terraform requires both branches of a
    # conditional to have the same type, and an object with two attributes is not the same type
    # as an empty one. `for ... if` yields nothing when the flag is false, which is what "these
    # states do not exist" has to mean.
    States = merge(
      { for name, state in local.escalation_states : name => state if var.enable_escalation_tiers },
      {
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
              "document_type.$" : "$.tier0.reading.document_type",
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
          # **Where the cascade actually happens, and it is opt-in.**
          #
          # Escalation runs *before* the publish decision, because its whole purpose is to turn a
          # field that abstained into one that can publish. Running it afterwards would mean the
          # machine had already sent the document to a human, and the better reading would arrive
          # for a decision already made.
          #
          # When `enable_escalation_tiers` is false the states below are not in the machine at
          # all — not present-and-skipped. A dead state is a state somebody maintains, and an
          # execution history full of skipped branches is one nobody reads.
          Next = var.enable_escalation_tiers ? "AnythingEscalatable" : "AnythingPublishable"
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
            # **The record must be able to say which version it is.**
            #
            # `fingerprint` and `reader` were not passed, so the gate — which returns
            # `{**event, ...}` — could not carry them, and the object written to
            # `records/<id>/<fingerprint>.json` did not contain its own fingerprint. The *key*
            # said which version it was and the *record* did not, which is a record you cannot
            # check against the place it came from: rename the object and nothing in it
            # disagrees.
            #
            # It surfaced as the landing function refusing a record with no version — doctrine
            # rule 4 depends on versions being retrievable and comparable, and a diff between two
            # records neither of which names itself is a diff between two anonymous documents.
            "Payload" : {
              "document_id.$" : "$.extraction.outcome.document_id",
              "document_type.$" : "$.extraction.outcome.document_type",
              "fingerprint.$" : "$.extraction.outcome.fingerprint",
              "reader.$" : "$.extraction.outcome.reader",
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
            Next          = "AnythingAbstained"
          }]
          Default = "QueueForReview"
        }

        # **The state that was missing, and the reason it matters more than anything else here.**
        #
        # `Publish` and `QueueForReview` used to be the only two terminal states, and they are
        # mutually exclusive. So a document with twelve fields where eight cleared their thresholds
        # and four abstained took the publish path — and the four that abstained **never reached a
        # human**. They were written into the record object marked as queued, and nothing was sent
        # to the queue.
        #
        # Nothing failed. The execution succeeded, the record was correct about what it did and did
        # not contain, and the queue stayed empty. In a system whose first doctrine rule is
        # *"abstention is the safe state — and abstention is not free"*, a pipeline where the
        # common case of abstention costs the queue nothing makes the capacity model a measurement
        # of the empty set. Claim 5's build-fails-on-overload check would have been scored against
        # a queue that only ever received documents where **every** field abstained.
        #
        # Found by reading this definition against the doctrine, not by running it — an execution
        # that drops four abstentions looks exactly like an execution that had none.
        #
        # Queue *before* publish, deliberately. If the queue send fails the execution stops and
        # nothing is published, which is the safe direction; the reverse would publish and then
        # lose the abstentions to a failure, which is the state this fixes.
        AnythingAbstained = {
          Type = "Choice"
          Choices = [{
            Variable           = "$.extraction.outcome.queued_count"
            NumericGreaterThan = 0
            Next               = "QueueTheAbstentions"
          }]
          Default = "Publish"
        }

        # The same queue and the same message shape as `QueueForReview`. It is a separate state
        # rather than a shared one because a state can have exactly one `Next`, and this one
        # continues to `Publish` while the other ends — the difference between "this document
        # abstained entirely" and "this document published some of itself and owes a human the
        # rest".
        QueueTheAbstentions = {
          Type     = "Task"
          Resource = "arn:aws:states:::sqs:sendMessage"
          Parameters = {
            "QueueUrl" : aws_sqs_queue.review.url,
            "MessageBody.$" : "$"
          }
          # **Discard the send's result.** Without this the SQS response replaces the state's
          # output and `Publish` loses `$.provenance.checked` — it would then write a message id
          # into the records bucket where a customs record belongs, and the execution would still
          # report success.
          ResultPath = null
          Next       = "Publish"
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
            # **The object, not a string of it.** This used to be
            # `States.JsonToString($.provenance.checked)`, on the reasoning that `s3:PutObject`
            # takes a string body. It does — and the SDK integration serialises the parameter as
            # well, so the record landed in the bucket **double-encoded**: a JSON string literal
            # whose contents are the JSON.
            #
            #     "{\"language\":\"en\",\"document_id\":\"E2E-PROOF2\",\"fields\":[...]}"
            #
            # Nothing failed. The execution succeeded, the object was written, its key was right
            # and its bytes were valid JSON — of a string. Every consumer downstream — Athena over
            # the lakehouse, Glue's crawler, a human opening the file — reads text where a customs
            # record should be, and the first one to notice would be whichever query returned a
            # column of escaped quotes.
            #
            # Found by the end-to-end verifier failing to call `.get` on a `str`, which is a
            # cheaper way to learn it than a mart that silently has one column.
            "Body.$" : "$.provenance.checked",
            "ServerSideEncryption" : "aws:kms",
            "SsekmsKeyId" : var.data_key_arn
          }
          ResultPath = "$.published"
          Next       = "LandInTheLake"
        }

        # **The record becomes queryable, and the order is deliberate.**
        #
        # Landing runs *after* the object is written, never instead of it. The customs record is
        # the object in the records bucket — versioned, keyed by fingerprint, the thing doctrine
        # rule 4 protects. The lake is a view of it, and a view that can be rebuilt from the
        # bucket is a view whose loss is an inconvenience rather than a restatement.
        #
        # **`Catch` sends a failed landing to review rather than failing the execution**, and
        # that is the opposite of the choice made at `VerifyProvenance` one state earlier. The
        # difference is what is at stake: a provenance gate that cannot run must stop everything,
        # because publishing past it is the thing this system exists not to do. A row that did
        # not reach the warehouse has cost a query and nothing else — the record is published,
        # the reviewer's queue is intact, and the row can be replayed. Failing the execution
        # there would turn an analytics outage into a document that looks unprocessed.
        LandInTheLake = {
          Type     = "Task"
          Resource = "arn:aws:states:::lambda:invoke"
          Parameters = {
            "FunctionName" : aws_lambda_function.land.arn,
            "Payload" : { "record.$" : "$.provenance.checked" }
          }
          ResultSelector = { "landed.$" : "$.Payload" }
          ResultPath     = "$.lake"
          Retry = [{
            ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
            IntervalSeconds = 2
            MaxAttempts     = 3
            BackoffRate     = 2
          }]
          Catch = [{ ErrorEquals = ["States.ALL"], Next = "LandingFailed", ResultPath = "$.error" }]
          Next  = var.search_endpoint == "" ? "Done" : "IndexTheRecord"
        }

        # A terminal state that exists so the machine has one when search is off. `End = true` on
        # `LandInTheLake` would make the shape of the machine depend on a flag in a way that
        # reads as two different pipelines; this way the tail is always the same and the only
        # difference is whether one state sits in it.
        Done = { Type = "Succeed" }

        # **Search is the least urgent thing in this pipeline and its failure says so.**
        #
        # Same `Catch` reasoning as the landing state, one step weaker: a record that is not
        # searchable is published, in the bucket, in the lake and in the queue. Every consumer
        # that decides anything already has it. Failing the execution here would turn "the search
        # box is stale" into "this document looks unprocessed", which is the more expensive
        # sentence by a wide margin.
        IndexTheRecord = {
          Type     = "Task"
          Resource = "arn:aws:states:::lambda:invoke"
          Parameters = {
            "FunctionName" : one(aws_lambda_function.index[*].arn),
            "Payload" : { "record.$" : "$.provenance.checked" }
          }
          ResultSelector = { "indexed.$" : "$.Payload" }
          ResultPath     = "$.search"
          Retry = [{
            ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
            IntervalSeconds = 2
            MaxAttempts     = 3
            BackoffRate     = 2
          }]
          Catch = [{ ErrorEquals = ["States.ALL"], Next = "IndexingFailed", ResultPath = "$.error" }]
          End   = true
        }

        IndexingFailed = {
          Type  = "Fail"
          Error = "IndexingFailed"
          Cause = "The record is published, landed and queued; it is not searchable. The index is a view and can be rebuilt from the records bucket."
        }

        # A terminal state of its own rather than a shared failure, so that "the warehouse is
        # behind" is distinguishable in the execution history from "a document was refused".
        # They have different responders and only one of them is urgent.
        LandingFailed = {
          Type  = "Fail"
          Error = "LandingFailed"
          Cause = "The record is published and its rows are not in the lake. The bucket is the record; the lake is a view and can be replayed."
        }

        QueueForReview = {
          Type     = "Task"
          Resource = "arn:aws:states:::sqs:sendMessage"
          Parameters = {
            "QueueUrl" : aws_sqs_queue.review.url,
            "MessageBody.$" : "$"
          }
          # **Discard the send's result, exactly as `QueueTheAbstentions` does.**
          #
          # Without this the execution's output is an SQS receipt — a message id and a set of HTTP
          # headers — and everything the document actually did is gone. The record of what was
          # read, what cleared its threshold and what the gate said is replaced by proof that a
          # queue accepted a message.
          #
          # It cost an hour of the first end-to-end run: the verifier read the execution output,
          # found no reading and no fields, and reported five failures whose real cause was that
          # the terminal state had overwritten the evidence.
          ResultPath = null
          End        = true
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
    })
  })
}
