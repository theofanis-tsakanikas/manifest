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

resource "aws_ecr_repository" "reader" {
  name                 = "${var.project}-reader"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.data_key_arn
  }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

# Keep the image the running functions point at, and nothing else.
#
# `IMMUTABLE` above means a tag never moves, so "latest" cannot silently become a different
# reader — which matters here more than in most systems: every threshold in this repository was
# derived from one build of that binary, and a tag that quietly repointed would move all of them
# without a diff.
resource "aws_ecr_lifecycle_policy" "reader" {
  repository = aws_ecr_repository.reader.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after a day; a tagged one is referenced by a deploy."
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}

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
  reader_image = "${aws_ecr_repository.reader.repository_url}@${var.reader_image_digest}"
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
  function_name    = "${var.project}-publish"
  description      = "Extraction and the derived thresholds — this project's own logic"
  role             = aws_iam_role.publish.arn
  runtime          = "python3.12"
  handler          = "manifest.handlers.publish.handler"
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
