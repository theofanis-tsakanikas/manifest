# The three functions that run this project's logic.
#
# **They did not exist.** The state machine invoked `${var.project}-provenance-gate` by name and
# no layer created it. On a real deployment every document would have failed there with
# `ResourceNotFoundException`, been caught by the step's `Catch`, and gone to a human — a
# pipeline reporting success while routing 100% of its volume to review.
#
# Two of them share a container image because they need the reader binary and its language data;
# the third is a zip because it needs neither, and keeping it separate means a change to the
# extraction logic does not rebuild a large image.

# ── What the functions may do ────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "reader" {
  name               = "${var.project}-reader"
  description        = "The tier-0 reader and the provenance gate. Reads documents, writes readings."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "reader" {
  statement {
    sid       = "ReadTheLandingZone"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.landing_bucket}/*"]
  }

  statement {
    sid     = "ReadAndWriteRecords"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject"]
    # Scoped to the two prefixes these functions own. The reader writes readings and renders;
    # the gate reads renders back. Neither has any business elsewhere in the bucket, and a
    # bucket-wide grant here would put the published customs record inside the blast radius of
    # a function that processes counterparty-supplied files.
    resources = [
      "arn:aws:s3:::${var.records_bucket}/readings/*",
      "arn:aws:s3:::${var.records_bucket}/renders/*",
      "arn:aws:s3:::${var.records_bucket}/thresholds/*",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  statement {
    sid     = "Log"
    effect  = "Allow"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.reader.arn}:*",
      "${aws_cloudwatch_log_group.gate.arn}:*",
    ]
  }

  # The private subnets. Every function runs inside the VPC — see the `vpc_config` blocks — so
  # each needs to create and delete its own elastic network interfaces. The actions are not
  # resource-scopable; the constraint is that this role does nothing else.
  #checkov:skip=CKV_AWS_111:The EC2 network-interface actions Lambda requires for VPC attachment are documented as not resource-scoped.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "reader" {
  name   = "reader"
  role   = aws_iam_role.reader.id
  policy = data.aws_iam_policy_document.reader.json
}

resource "aws_iam_role" "publish" {
  name               = "${var.project}-publish"
  description        = "Applies the derived thresholds. Reads a reading, writes nothing to storage."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "publish" {
  # Read-only, deliberately. This function decides; the state machine writes. A function that
  # both decided what publishes and could write the published object would be one compromise
  # away from writing a record no threshold ever approved.
  statement {
    sid     = "ReadTheReadingAndTheThresholds"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.records_bucket}/readings/*",
      "arn:aws:s3:::${var.records_bucket}/thresholds/*",
    ]
  }

  # **Listing one prefix, so that a missing artefact can say it is missing.**
  #
  # Without this, S3 answers `AccessDenied` for an object that is simply not there — it will not
  # confirm a key's absence to a caller that cannot list the bucket. That is the correct S3
  # behaviour and it made the single most important failure of the first deploy unreadable: the
  # handler asked for `thresholds/reference-ocr@tesseract-5.5.2.json`, the deployment had
  # shipped exactly that, and the *reading* in front of it came from 5.5.0 — a reader mismatch
  # that reported itself as an IAM error naming an action nobody had thought about.
  #
  # `scripts/reader_version_check.py` now makes that mismatch impossible to commit, so this is
  # the second line rather than the first. It is still worth having: the diagnosis cost hours,
  # the grant is one prefix, and it reads key names in a bucket this role already reads two
  # prefixes of. A control that cannot say what is wrong gets muted by whoever is on call.
  statement {
    sid       = "ListTheThresholdsSoAMissingOneSaysSo"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.records_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["thresholds/*"]
    }
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.publish.arn}:*"]
  }

  #checkov:skip=CKV_AWS_111:As on the reader role — Lambda's VPC attachment actions take no resource ARN.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "publish" {
  name   = "publish"
  role   = aws_iam_role.publish.id
  policy = data.aws_iam_policy_document.publish.json
}

# ── Log groups, created here rather than by the service ──────────────────────
#
# A function whose log group the service creates gets one with no retention and no key. Created
# here they carry both, and the deletion of the layer takes them with it.

resource "aws_cloudwatch_log_group" "reader" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate; the customs record is in the records bucket and has no expiry.
  name              = "/aws/lambda/${var.project}-read-tier0"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_cloudwatch_log_group" "gate" {
  #checkov:skip=CKV_AWS_338:As above.
  name              = "/aws/lambda/${var.project}-provenance-gate"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_cloudwatch_log_group" "publish" {
  #checkov:skip=CKV_AWS_338:As above.
  name              = "/aws/lambda/${var.project}-publish"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

# ── The functions ────────────────────────────────────────────────────────────

locals {
  # The image is built and pushed by the deploy workflow, which passes the digest in. A *digest*
  # rather than a tag: a tag identifies what somebody meant, a digest identifies what is
  # actually there, and every threshold in this repository was derived from one specific build
  # of the reader inside it.
  #
  # The registry itself lives in `foundation`. It has to: the image must exist **before** a
  # function can be created from it, so a registry created by this layer would have to be
  # created by the same apply that consumes it. On the first run there is no repository to push
  # to and the deploy fails at `docker push`, four minutes in, with the approval spent.
  reader_image = "${var.reader_repository_url}@${var.reader_image_digest}"
}

resource "aws_lambda_function" "read_tier0" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below via a separate argument on this resource; see `reserved_concurrent_executions`.
  #checkov:skip=CKV_AWS_116:A dead-letter queue applies to *asynchronous* invocation. Every function here is invoked synchronously by the state machine, which owns the error path: `Retry` on transport faults and a named `Fail` state otherwise. A DLQ attached here would never receive anything, and a control that cannot fire is worse than none — it reads as coverage.
  #checkov:skip=CKV_AWS_272:Code signing needs a signing profile in bootstrap and an image-based function cannot use it; the integrity control here is the immutable tag plus the pinned digest.
  function_name = "${var.project}-read-tier0"
  description   = "Tier 0: the local reference reader, the same build that produced recordings/ocr/"
  role          = aws_iam_role.reader.arn
  package_type  = "Image"
  image_uri     = local.reader_image

  # Generous, because rasterising and reading a twenty-page degraded scan is genuinely slow and
  # the alternative to a long timeout is a document that fails halfway with no reading and no
  # error anybody can attribute.
  timeout     = 600
  memory_size = 3008

  # The reader writes page renders to /tmp before reading them.
  ephemeral_storage {
    size = 2048
  }

  # A ceiling, not a target. The queue capacity in `contracts/review/` is a declared finite
  # resource and so is this: an unbounded reader would happily process a backlog faster than
  # the review queue can absorb what it abstains on.
  reserved_concurrent_executions = 20

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  # Encrypted at rest with the estate's own key rather than with the service-managed one. The
  # values here are bucket and key names rather than secrets, but the control costs nothing and
  # its absence is the kind of thing that is true of the secret somebody adds later.
  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      RECORDS_BUCKET = var.records_bucket
      DATA_KEY_ARN   = var.data_key_arn
      CONTRACTS_DIR  = "/var/task/contracts"
    }
  }

  # An image entrypoint, overriding the Dockerfile's CMD. Both functions run the same image.
  image_config {
    command = ["manifest.handlers.read_tier0.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.reader]
  tags       = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_lambda_function" "provenance_gate" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set on this resource below.
  #checkov:skip=CKV_AWS_116:As on the reader — these are synchronous invocations from the state machine, and a dead-letter queue only receives from asynchronous ones.
  #checkov:skip=CKV_AWS_272:See the reader function — an image-based function cannot use code signing.
  function_name = "${var.project}-provenance-gate"
  description   = "Claim 2, in the path: every published field's box checked against the page"
  role          = aws_iam_role.reader.arn
  package_type  = "Image"
  image_uri     = local.reader_image

  timeout     = 300
  memory_size = 2048

  ephemeral_storage {
    size = 1024
  }

  reserved_concurrent_executions = 20

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  # Encrypted at rest with the estate's own key rather than with the service-managed one. The
  # values here are bucket and key names rather than secrets, but the control costs nothing and
  # its absence is the kind of thing that is true of the secret somebody adds later.
  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      RECORDS_BUCKET = var.records_bucket
      DATA_KEY_ARN   = var.data_key_arn
      CONTRACTS_DIR  = "/var/task/contracts"
    }
  }

  image_config {
    command = ["manifest.handlers.provenance_gate.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.gate]
  tags       = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_lambda_function" "publish" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set on this resource below.
  #checkov:skip=CKV_AWS_116:As on the reader — these are synchronous invocations from the state machine, and a dead-letter queue only receives from asynchronous ones.
  #checkov:skip=CKV_AWS_272:Code signing would need a signing profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  function_name = "${var.project}-publish"
  description   = "Extraction and the derived thresholds — this project's own logic"
  role          = aws_iam_role.publish.arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.publish.handler"
  # From object storage, not from a local path.
  #
  # `filename` with `filebase64sha256()` reads the file at plan time — including during
  # `terraform destroy`, where the zip does not exist because the teardown workflow never built
  # one. The estate would then be undeleteable from CI, which is the failure the destroy path
  # exists to prevent. An S3 reference is three strings and evaluates on any machine.
  s3_bucket        = var.records_bucket
  s3_key           = var.publish_package_key
  source_code_hash = var.publish_package_hash

  timeout     = 120
  memory_size = 1024

  reserved_concurrent_executions = 20

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      RECORDS_BUCKET = var.records_bucket
      CONTRACTS_DIR  = "/var/task/contracts"
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.publish]
  tags       = { "${var.project}:expires-at" = var.expires_at }
}

# ── The escalation reader ─────────────────────────────────────────────────────
#
# **The function that makes the cascade a measurement rather than a design.** Everything above
# tier 0 was written, schema-tested and never called; this is what calls it. It is a zip rather
# than the reader image because it renders nothing — it reads a page raster the tier-0 step
# already wrote to storage, and hands the response to an adapter.
#
# `count` rather than a flag inside the function: when escalation is off there is no function,
# no role, and no grant to Textract or Bedrock anywhere in the account. A disabled feature that
# still holds permissions is a permission nobody remembers to remove.

data "aws_iam_policy_document" "escalate" {
  # **`count` on the data source, not just on the resources it feeds.**
  #
  # A data source without one is evaluated whatever the flag says, and this one interpolates the
  # log group's ARN — which is null when the group does not exist, and null does not go into a
  # string. The plan failed with the feature merely switched off, which is the worst kind of
  # optional: one that breaks the configuration it is absent from.
  count = var.enable_escalation_tiers ? 1 : 0

  statement {
    sid     = "ReadTheReadingTheRenderAndTheThresholds"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.records_bucket}/readings/*",
      "arn:aws:s3:::${var.records_bucket}/renders/*",
      "arn:aws:s3:::${var.records_bucket}/thresholds/*",
    ]
  }

  # Document automation reads from storage and writes to storage; it returns neither the page
  # nor the reading inline. So this tier needs to *put* the page it is submitting — a full-page
  # PNG is five megabytes and the service refuses it, so what is written is the same re-encoded
  # JPEG the model tier sends — and to *get* the standard output back afterwards, which arrives
  # as two files: a job metadata object naming the reading, and the reading.
  #
  # `GetObject` alongside `PutObject` was missing while nothing called this tier, and the
  # failure would have been an AccessDenied on the last step of a successful read.
  statement {
    sid       = "SubmitAPageAndReadWhatComesBack"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::${var.records_bucket}/escalated/*"]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  # Textract's document APIs take no resource ARN — the constraint is the narrowness of this
  # role, which exists only when escalation is enabled and can do nothing else.
  #checkov:skip=CKV_AWS_111:The Textract document APIs are documented as not resource-scoped.
  #checkov:skip=CKV_AWS_356:As above — "*" is the only expressible form for an API with no resource ARNs.
  statement {
    sid       = "ReadAPageAtTierOne"
    effect    = "Allow"
    actions   = ["textract:DetectDocumentText", "textract:AnalyzeDocument"]
    resources = ["*"]
  }

  # Scoped to the model this system may call, by ARN. `bedrock:InvokeModel` on `*` is permission
  # to invoke every model in the account — different prices, different data-handling terms,
  # different regional footprints. Naming one is what makes the budget guard a guard.
  dynamic "statement" {
    for_each = length(var.escalation_model_arns) > 0 ? [1] : []
    content {
      sid     = "ReadAPageAtTierThree"
      effect  = "Allow"
      actions = ["bedrock:InvokeModel", "bedrock:Converse"]
      # **A cross-Region inference profile is two resources, and the policy named one.**
      #
      # `Converse` against a profile is authorised twice: once on the profile, and once on the
      # *foundation model in whichever Region the profile routes the request to*. The first
      # Greek page refused with `AccessDenied ... bedrock:InvokeModel on resource:
      # arn:aws:bedrock:eu-north-1::foundation-model/anthropic.claude-sonnet-5` — a Region this
      # estate does not deploy into and never names anywhere, chosen by Bedrock at call time.
      #
      # The six ARNs are **derived from the profile**, not transcribed. Which Regions a profile
      # spans is AWS's decision and it changes without notice; a hardcoded list is a policy that
      # is correct until the day the routing picks a Region nobody wrote down, and that failure
      # arrives as an AccessDenied on one page in one language.
      # The list is the profile **and every foundation model it routes to**, resolved by
      # `deploy.yml` from `bedrock:GetInferenceProfile` rather than transcribed here. Six ARNs
      # in six Regions, and which six is AWS's decision that changes without notice.
      resources = var.escalation_model_arns
    }
  }

  # **Two statements, because the two calls are authorised against different resource types.**
  #
  # `InvokeDataAutomationAsync` is judged against the project and the profile — and the profile
  # in *every region the profile spans*, which is why the list arrives with six entries.
  # `GetDataAutomationStatus` is judged against the **invocation**, a resource that does not
  # exist until the first call creates it and whose identifier nobody chooses. One statement
  # naming both actions and the project's ARNs authorised the start and refused the poll, which
  # is the shape that gets discovered one call further in than the grant was tested.
  dynamic "statement" {
    for_each = length(var.document_automation_arns) > 0 ? [1] : []
    content {
      sid       = "ReadAPageAtTierTwo"
      effect    = "Allow"
      actions   = ["bedrock:InvokeDataAutomationAsync"]
      resources = var.document_automation_arns
    }
  }

  dynamic "statement" {
    for_each = length(var.document_automation_arns) > 0 ? [1] : []
    content {
      sid     = "WatchTheReadItStarted"
      effect  = "Allow"
      actions = ["bedrock:GetDataAutomationStatus"]
      # A wildcard on the *id* and nothing else: the account, the region and the resource type
      # are all fixed, and the identifier is one AWS mints per call.
      resources = [
        "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:data-automation-invocation/*"
      ]
    }
  }

  statement {
    sid     = "Log"
    effect  = "Allow"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    # `one(...)` for the same reason the state machine uses it: this data source has no `count`,
    # so it is evaluated even when the log group does not exist, and `[0]` on an empty list is a
    # plan-time failure with the feature merely switched off.
    resources = ["${aws_cloudwatch_log_group.escalate[0].arn}:*"]
  }

  # **The statement the other three roles have and this one was written without.**
  #
  # This function has a `vpc_config` like every other function here, so Lambda creates an elastic
  # network interface in the private subnets on its behalf — using *this role*, at create time,
  # before any code runs. Without the grant `CreateFunction` itself is refused:
  # `InvalidParameterValueException: The provided execution role does not have permissions to
  # call CreateNetworkInterface on EC2`, five minutes into an apply that had already built the
  # other three functions and pushed the image.
  #
  # It is the newest role in the layer and the omission is the ordinary one: the escalation was
  # written as *what this tier is allowed to read* — Textract, Bedrock, the page, the key — and
  # attaching to the network is not a permission the feature needs, it is one the runtime needs.
  # `scripts/check_deploy_path.py` now pairs every `vpc_config` with its role's grant, because a
  # fourth function is exactly when a copied block stops being copied.
  #checkov:skip=CKV_AWS_111:The EC2 network-interface actions Lambda requires for VPC attachment are documented as not resource-scoped.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "escalate" {
  count              = var.enable_escalation_tiers ? 1 : 0
  name               = "${var.project}-escalate"
  description        = "Calls the upper tiers of the cascade. Reads a page; writes no record."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_iam_role_policy" "escalate" {
  count  = var.enable_escalation_tiers ? 1 : 0
  name   = "escalate"
  role   = aws_iam_role.escalate[0].id
  policy = data.aws_iam_policy_document.escalate[0].json
}

resource "aws_cloudwatch_log_group" "escalate" {
  count = var.enable_escalation_tiers ? 1 : 0
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate; the record itself has no expiry.
  name              = "/aws/lambda/${var.project}-escalate"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
}

resource "aws_lambda_function" "escalate" {
  count = var.enable_escalation_tiers ? 1 : 0
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Synchronous invocations from the state machine; a dead-letter queue receives only from asynchronous ones.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  function_name = "${var.project}-escalate"
  description   = "Routes an abstaining field to the cheapest tier that can read it"
  role          = aws_iam_role.escalate[0].arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.escalate.handler"
  s3_bucket     = var.records_bucket
  s3_key        = var.publish_package_key
  # Same artefact as `publish`, deliberately: both are this project's own logic over the same
  # contracts, and two zips built from one commit are two chances for them to diverge.
  source_code_hash = var.publish_package_hash

  # Longer than `publish` because it waits on somebody else's service. Bounded well under the
  # state machine's own patience so that a hung upper tier fails here, by name, rather than as a
  # timeout three layers up.
  timeout     = 300
  memory_size = 1024

  # **Lower than the reader's, and that is the cost control.** Every concurrent execution here is
  # a billed call to a metered service; the reader's concurrency costs nothing per page. This
  # number is the ceiling on how fast this estate can spend money.
  reserved_concurrent_executions = 5

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  environment {
    variables = {
      RECORDS_BUCKET      = var.records_bucket
      CONTRACTS_DIR       = "/var/task/contracts"
      ESCALATION_MODEL_ID = var.escalation_model_id
      BDA_PROFILE_ARN     = var.bda_profile_arn
      BDA_PROJECT_ARN     = var.bda_project_arn
    }
  }

  kms_key_arn = var.data_key_arn
  depends_on  = [aws_cloudwatch_log_group.escalate]

  tags = { "${var.project}:expires-at" = var.expires_at }
}


# ── The landing function ─────────────────────────────────────────────────────
#
# **The step four services were waiting for.** The lakehouse has declared an Iceberg table since
# the day it was written and nothing ever wrote to it, so the marts, the search surface and the
# bulk reprocessor all read from an empty schema. This is the per-document path: one published
# record in, one row per field out.

resource "aws_iam_role" "land" {
  name               = "${var.project}-land"
  description        = "Writes a published record into the record lake. Reads the record; decides nothing."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "land" {
  # **Read, not write.** This function does not produce the record; it copies what `Publish`
  # already wrote into a queryable shape. A landing function that could write to `records/`
  # would be a second author of the customs record.
  statement {
    sid       = "ReadThePublishedRecords"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.records_bucket}/records/*"]
  }

  # Listing one prefix, so `supersedes` can be found. The previous version is read from the
  # records bucket rather than from the lake, because the bucket is where doctrine rule 4 is
  # enforced and the lake is the table this function is in the middle of writing.
  statement {
    sid       = "ListOneDocumentsVersions"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.records_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["records/*"]
    }
  }

  # Athena writes the table's data files and its own results into the lake bucket.
  statement {
    sid       = "WriteTheTableAndTheQueryResults"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["arn:aws:s3:::${var.lake_bucket}/*"]
  }

  statement {
    sid       = "ListTheLakeSoIcebergCanPlan"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.lake_bucket}"]
  }

  # **An Iceberg append is a catalogue commit.** The data files are S3; the new metadata pointer
  # is an `UpdateTable` against Glue. Granting the S3 half alone produces a query that writes
  # parquet nobody can see — the failure that looks like a slow table rather than a broken one.
  statement {
    sid    = "CommitTheIcebergMetadata"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:UpdateTable",
      "glue:GetPartitions",
    ]
    resources = [
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:database/${var.glue_database}",
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${var.glue_database}/*",
    ]
  }

  #checkov:skip=CKV_AWS_111:Athena's execution actions are scoped to the workgroup below; the query-level ones take no resource.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "RunTheLandingStatement"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
    ]
    resources = [
      "arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.land.arn}:*"]
  }

  #checkov:skip=CKV_AWS_111:As on every other function here — Lambda's VPC attachment actions take no resource ARN.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "land" {
  name   = "land"
  role   = aws_iam_role.land.id
  policy = data.aws_iam_policy_document.land.json
}

resource "aws_cloudwatch_log_group" "land" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate.
  name              = "/aws/lambda/${var.project}-land"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_lambda_function" "land" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Synchronous invocations from the state machine; a dead-letter queue receives only from asynchronous ones.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  function_name = "${var.project}-land"
  description   = "Writes one row per published field into the Iceberg table"
  role          = aws_iam_role.land.arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.land.handler"
  s3_bucket     = var.records_bucket
  s3_key        = var.publish_package_key

  source_code_hash = var.publish_package_hash

  # Longer than the others because it waits on a query rather than on its own work. The handler
  # gives up at 120s and says which execution it gave up on; this is the outer bound.
  timeout     = 180
  memory_size = 512

  # **Low on purpose.** Every append is a catalogue commit, and Iceberg resolves concurrent
  # commits by retrying — so a hundred parallel landings turn into a hundred retries against one
  # table. The queue in front of this is the pipeline, and the pipeline is per document.
  reserved_concurrent_executions = 2

  # The same key every other function's environment is encrypted with. Not a formality here:
  # `kms:Decrypt` on it is already granted to this role, so leaving it off would have been a
  # function whose configuration is readable to anyone who can read Lambda's API while the data
  # it processes is not.
  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      RECORDS_BUCKET   = var.records_bucket
      GLUE_DATABASE    = var.glue_database
      LAKE_TABLE       = var.lake_table
      ATHENA_WORKGROUP = var.athena_workgroup
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  tracing_config { mode = "Active" }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

# ── The catalogue's own permission layer ─────────────────────────────────────
#
# **IAM is necessary and not sufficient here, because this account enforces Lake Formation.**
# `CreateTableDefaultPermissions` is `[]` rather than `IAM_ALLOWED_PRINCIPALS` — set by a
# sibling project, not by this one — so a principal holding every `glue:` action still gets
# `Access Denied when accessing database ..., table ...` from Athena. The landing function did.
#
# `ALTER` is in the list and is not decoration: an Iceberg append rewrites the table's metadata
# pointer, so a writer that may `INSERT` and not `ALTER` commits data files nobody can see.
resource "aws_lakeformation_permissions" "land_writes_the_table" {
  principal   = aws_iam_role.land.arn
  permissions = ["SELECT", "INSERT", "ALTER", "DESCRIBE"]

  table {
    database_name = var.glue_database
    name          = var.lake_table
  }
}

resource "aws_lakeformation_permissions" "land_sees_the_database" {
  principal   = aws_iam_role.land.arn
  permissions = ["DESCRIBE"]

  database {
    name = var.glue_database
  }
}

# ── The indexer ──────────────────────────────────────────────────────────────
#
# **Created only when there is a collection to write to.** `enable_search` is the lakehouse's
# flag and this layer cannot read it; what it can read is whether an endpoint was published. A
# function pointed at an empty string would be a function whose first invocation fails on an
# environment variable, which is the "present and disabled" shape the escalation states were
# deliberately built to avoid.

resource "aws_iam_role" "index" {
  count              = var.search_endpoint == "" ? 0 : 1
  name               = "${var.project}-index"
  description        = "Indexes published records. Reads nothing and writes only to the collection."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "index" {
  count = var.search_endpoint == "" ? 0 : 1

  # **The document is handed to it; nothing is read.** The record arrives in the payload, so this
  # role needs no S3 at all — which is worth having, because it is the one function whose output
  # is reachable by a human through a search box.
  statement {
    sid       = "WriteTheIndex"
    effect    = "Allow"
    actions   = ["aoss:APIAccessAll"]
    resources = ["arn:aws:aoss:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:collection/*"]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${one(aws_cloudwatch_log_group.index[*].arn)}:*"]
  }

  #checkov:skip=CKV_AWS_111:As on every other function here — Lambda's VPC attachment actions take no resource ARN.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "index" {
  count  = var.search_endpoint == "" ? 0 : 1
  name   = "index"
  role   = aws_iam_role.index[0].id
  policy = data.aws_iam_policy_document.index[0].json
}

resource "aws_cloudwatch_log_group" "index" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate.
  count             = var.search_endpoint == "" ? 0 : 1
  name              = "/aws/lambda/${var.project}-index"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_lambda_function" "index" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Synchronous invocations from the state machine; a dead-letter queue receives only from asynchronous ones.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  count         = var.search_endpoint == "" ? 0 : 1
  function_name = "${var.project}-index"
  description   = "Puts a published record into the search surface. Published values only."
  role          = aws_iam_role.index[0].arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.index_record.handler"
  s3_bucket     = var.records_bucket
  s3_key        = var.publish_package_key

  source_code_hash = var.publish_package_hash

  timeout     = 60
  memory_size = 256

  reserved_concurrent_executions = 5

  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      SEARCH_ENDPOINT = var.search_endpoint
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  tracing_config { mode = "Active" }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

# ── The search function ───────────────────────────────────────────────────────
#
# **The half that made the index worth building.** The indexer has written published records into
# OpenSearch since it was built and nothing ever read one back — and the network policy makes
# that permanent rather than temporary: `AllowFromPublic = false`, so the collection answers
# inside the VPC and nowhere else. The only way to ask it anything is a function that lives
# there, and until this one existed "search over published records" was a sentence in a README
# with a bill behind it.
#
# It holds `aoss:APIAccessAll` like the indexer does — that action is the only one IAM has for
# this service — and the *verb* separation is in the collection's own data-access policy, where
# this role gets `ReadDocument` and the indexer gets `WriteDocument`. Two places, because IAM
# cannot express the distinction and the service can.

resource "aws_cloudwatch_log_group" "search" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate.
  count             = var.search_endpoint == "" ? 0 : 1
  name              = "/aws/lambda/${var.project}-search"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_iam_role" "search" {
  count              = var.search_endpoint == "" ? 0 : 1
  name               = "${var.project}-search"
  description        = "Answers questions against the published-record index. Read-only at the collection."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "search" {
  count = var.search_endpoint == "" ? 0 : 1

  statement {
    sid       = "AskTheIndex"
    effect    = "Allow"
    actions   = ["aoss:APIAccessAll"]
    resources = ["arn:aws:aoss:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:collection/*"]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${one(aws_cloudwatch_log_group.search[*].arn)}:*"]
  }

  #checkov:skip=CKV_AWS_111:As on every other function here — Lambda's VPC attachment actions take no resource ARN.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "search" {
  count  = var.search_endpoint == "" ? 0 : 1
  name   = "search"
  role   = aws_iam_role.search[0].id
  policy = data.aws_iam_policy_document.search[0].json
}

resource "aws_lambda_function" "search" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Invoked synchronously; a dead-letter queue receives only from asynchronous invocations.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  count         = var.search_endpoint == "" ? 0 : 1
  function_name = "${var.project}-search"
  description   = "Answers a question against the published-record index. Reads; never writes."
  role          = aws_iam_role.search[0].arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.search_records.handler"
  s3_bucket     = var.records_bucket
  s3_key        = var.publish_package_key

  source_code_hash = var.publish_package_hash

  timeout     = 30
  memory_size = 256

  reserved_concurrent_executions = 5

  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      SEARCH_ENDPOINT = var.search_endpoint
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  tracing_config { mode = "Active" }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

# ── The three functions claims 4, 5 and 6 needed and never had ────────────────
#
# **Each of these proved its claim offline and had no path in the estate at all.** `evals/`
# scored reconciliation against a corpus with planted mismatches, entity resolution against
# transliterated party names, and the review loop against a generated queue — and no document on
# this estate was ever compared with another, no two names were ever merged, and the decisions
# table had never held a row. The claims were properties of pure functions and untested
# properties of the running system.
#
# They are invoked, not triggered. A reconciliation is about a *shipment*, which is a set of
# documents nothing in this pipeline groups; an un-merge is a human undoing a judgement; a
# decision is a human making one. None of the three is a thing that should happen because a file
# landed in a bucket, and a state machine step that guessed which documents belong together would
# be inventing the one fact `analytics/schema.sql` already lists as having no source.

locals {
  # One role's worth of shared access, written once. All three read published records; two write
  # something; none of them may delete anything at all.
  human_loop_functions = var.enable_escalation_tiers ? toset(["decide", "reconcile", "entities"]) : toset([])
}

resource "aws_cloudwatch_log_group" "human_loop" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate.
  for_each = local.human_loop_functions

  name              = "/aws/lambda/${var.project}-${each.key}"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_iam_role" "human_loop" {
  for_each = local.human_loop_functions

  name               = "${var.project}-${each.key}"
  description        = "Claims 4, 5 and 6 on the estate. Reads records; decides nothing."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "human_loop" {
  for_each = local.human_loop_functions

  statement {
    sid     = "ReadPublishedRecords"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.records_bucket}",
      "arn:aws:s3:::${var.records_bucket}/records/*",
      "arn:aws:s3:::${var.records_bucket}/entities/*",
    ]
  }

  # **Writes, narrowly.** `decide` publishes a superseding record; `entities` writes the resolved
  # state that makes a merge reversible. `reconcile` writes nothing at all — its output is a
  # review item and a return value, and a reconciler that could write a record could resolve a
  # disagreement by editing one of the documents.
  dynamic "statement" {
    for_each = each.key == "decide" ? [1] : []
    content {
      sid       = "PublishASupersedingVersion"
      effect    = "Allow"
      actions   = ["s3:PutObject"]
      resources = ["arn:aws:s3:::${var.records_bucket}/records/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "entities" ? [1] : []
    content {
      sid       = "WriteTheResolvedState"
      effect    = "Allow"
      actions   = ["s3:PutObject"]
      resources = ["arn:aws:s3:::${var.records_bucket}/entities/*"]
    }
  }

  # The decisions table. `PutItem` and nothing else: a role that could delete a decision could
  # remove the evidence claim 5 is computed from, and one that could update one could change a
  # rejection into an approval after the fact.
  dynamic "statement" {
    for_each = each.key == "decide" ? [1] : []
    content {
      sid       = "RecordTheDecision"
      effect    = "Allow"
      actions   = ["dynamodb:PutItem"]
      resources = [aws_dynamodb_table.decisions.arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "reconcile" ? [1] : []
    content {
      sid       = "QueueADisagreement"
      effect    = "Allow"
      actions   = ["sqs:SendMessage"]
      resources = [aws_sqs_queue.review.arn]
    }
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.human_loop[each.key].arn}:*"]
  }

  #checkov:skip=CKV_AWS_111:Lambda's VPC attachment actions take no resource ARN.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpc"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "human_loop" {
  for_each = local.human_loop_functions

  name   = each.key
  role   = aws_iam_role.human_loop[each.key].id
  policy = data.aws_iam_policy_document.human_loop[each.key].json
}

resource "aws_lambda_function" "human_loop" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Invoked synchronously; a dead-letter queue receives only from asynchronous invocations.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  for_each = local.human_loop_functions

  function_name = "${var.project}-${each.key}"
  description = lookup({
    decide    = "Records a reviewer's decision. core.review.publishable decides."
    reconcile = "Compares what several documents say about one shipment."
    entities  = "Resolves party names, and un-merges with lineage intact."
  }, each.key, each.key)
  role             = aws_iam_role.human_loop[each.key].arn
  runtime          = "python3.12"
  handler          = "manifest.handlers.${each.key}.handler"
  s3_bucket        = var.records_bucket
  s3_key           = var.publish_package_key
  source_code_hash = var.publish_package_hash

  timeout     = 60
  memory_size = 512

  # Low. Each of these is a human's action or an operator's, not a document arriving.
  reserved_concurrent_executions = 5

  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      RECORDS_BUCKET   = var.records_bucket
      DATA_KEY_ARN     = var.data_key_arn
      DECISIONS_TABLE  = aws_dynamodb_table.decisions.name
      REVIEW_QUEUE_URL = aws_sqs_queue.review.url
      CONTRACTS_DIR    = "/var/task/contracts"
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  tracing_config { mode = "Active" }

  depends_on = [aws_cloudwatch_log_group.human_loop]
  tags       = { "${var.project}:expires-at" = var.expires_at }
}
