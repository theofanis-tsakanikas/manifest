# The tariff-classification model, and the honest limit on what it may be said to do.
#
# **It proposes. It never publishes.** `contracts/documents/` declares `hs_code` as
# always-review, so no score this endpoint returns — 0.999 included — publishes a
# classification. `src/manifest/classification/hs.py` already encodes that: `Proposal.publishes`
# is `False`, unconditionally, and the abstention band is measured on the *gap between the top
# two* rather than on the top score, because a model that is 0.97 on two headings at once is
# confident about nothing.
#
# **Why this was deliberately deferred, and what changed.** `PLAN.md` recorded the classifier as
# not-done with a real reason: a model trained on a synthetic distribution carries an accuracy
# figure that is not a claim about production. That reason still stands, and it is not an
# argument against building the thing — it is an argument about what may be said afterwards. So
# the estate is here, and the constraint travels with it:
#
#   Any accuracy figure this model produces is a statement about a distribution **this
#   repository generated**. It is labelled as such wherever it appears, it appears on no
#   scoreboard, and "the classifier is N% accurate" is a sentence this project does not have.
#
# That is the same treatment claims 1 and 4 already give their own figures, and it is why the
# model can exist without the repository becoming dishonest.
#
# **Serverless inference, and off by default.** A provisioned endpoint bills by the hour whether
# anything is classified or not. Serverless bills per request and scales to nothing, which fits
# a workload that is bursty by nature — a customs broker's documents arrive when ships do.

locals {
  # **The serving image, and why an account number is written down here.**
  #
  # AWS publishes the scikit-learn serving containers from a different account in each region,
  # and there is no API that returns the mapping — the SageMaker Python SDK ships it as a static
  # file, `image_uri_config/sklearn.json`, which is the authority. This value was read from that
  # file at SDK version 3.13.1 on 2026-08-13. Resolving it at deploy time would mean installing
  # the whole SDK in the workflow for one lookup in a JSON file that changes when a region is
  # added, so it is transcribed with its source and its date, exactly like every other external
  # constant in this repository.
  #
  # A region this map does not name fails at plan with a key error rather than at apply with an
  # image pull failure, which is the direction worth failing in.
  sklearn_registry = {
    "eu-central-1" = "492215442770"
  }

  # 1.2-1 is the container's scikit-learn version, and it is *not* the version anything here is
  # fitted with. `classification/artefact.py` explains at length why the artefact is JSON rather
  # than a pickle; the short form is that this number and the trainer's no longer have to agree,
  # and the container is a Python host rather than a model loader.
  classifier_image = "${local.sklearn_registry[var.aws_region]}.dkr.ecr.${var.aws_region}.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3"
}

# Enabling the endpoint without an artefact would produce a model resource pointing at nothing —
# which fails at apply, four minutes in, with the approval already spent. Checked here so that it
# fails at *plan*, by name, before anything is created. The image is no longer part of this
# check: it has a derived default, and a deploy that overrides it is saying something deliberate.
check "classifier_artefacts_are_named" {
  assert {
    condition     = !var.enable_classifier || var.classifier_model_data_url != ""
    error_message = "enable_classifier is true and classifier_model_data_url is empty."
  }
}

resource "aws_sagemaker_model" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name               = "${var.project}-hs-classifier"
  execution_role_arn = aws_iam_role.classifier[0].arn

  primary_container {
    image          = var.classifier_image_uri != "" ? var.classifier_image_uri : local.classifier_image
    model_data_url = var.classifier_model_data_url

    environment = {
      # Where the container looks for the entry point. Both are required and neither has a
      # useful default: without them the scikit-learn container serves its own handler, which
      # returns a bare prediction — a heading and nothing else. That is the endpoint deciding,
      # in the one place no test in this repository can reach, and it would look like success.
      SAGEMAKER_PROGRAM             = "inference.py"
      SAGEMAKER_SUBMIT_DIRECTORY    = "/opt/ml/model/code"
      SAGEMAKER_CONTAINER_LOG_LEVEL = "20"
      SAGEMAKER_REGION              = var.aws_region

      # The endpoint returns a ranked list, never a decision. The abstention band is applied in
      # `manifest.classification.hs`, on a laptop and in the estate alike, from one
      # implementation — a threshold applied inside the container would be a second copy of the
      # rule, in the one place no test in this repository can reach.
      MANIFEST_RETURNS = "ranked_proposals"
    }
  }

  # In the VPC, like everything else. An endpoint reachable from outside the network the
  # documents live in is an endpoint whose inputs can come from somewhere nobody declared.
  vpc_config {
    subnets            = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  enable_network_isolation = true

  tags = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_sagemaker_endpoint_configuration" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name        = "${var.project}-hs-classifier"
  kms_key_arn = var.data_key_arn

  production_variants {
    variant_name = "default"
    model_name   = aws_sagemaker_model.classifier[0].name

    serverless_config {
      memory_size_in_mb = 2048
      # A ceiling, deliberately low. The review queue in `contracts/review/` is a declared finite
      # resource and every classification this endpoint produces lands in it — `hs_code` is
      # always-review. An endpoint that could classify faster than humans can decide would
      # produce a backlog, and doctrine rule 1 is explicit that a queue past capacity is a
      # failure of the system rather than of the reviewers.
      max_concurrency = 5
    }
  }

  # Every input and every proposal, captured to the evidence bucket.
  #
  # Not for retraining and not for a dashboard: for claim 5. "A human decision is only evidence
  # if the human was looking" needs the model's proposal *and* the reviewer's answer, side by
  # side, to compute an agreement rate — and a reviewer whose agreement rate is 100% is a rubber
  # stamp with a login. Without capture there is no denominator.
  data_capture_config {
    enable_capture              = true
    initial_sampling_percentage = 100
    destination_s3_uri          = "s3://${var.evidence_bucket}/classification-capture/"
    kms_key_id                  = var.data_key_arn

    capture_options {
      capture_mode = "Input"
    }

    capture_options {
      capture_mode = "Output"
    }
  }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_sagemaker_endpoint" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name                 = "${var.project}-hs-classifier"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.classifier[0].name

  tags = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name               = "${var.project}-hs-classifier"
  description        = "The classification endpoint. Reads its own artefact, writes its own capture."
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

data "aws_iam_policy_document" "classifier" {
  statement {
    sid       = "ReadTheModelArtefact"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.records_bucket}/models/*"]
  }

  statement {
    sid       = "WriteTheCapture"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${var.evidence_bucket}/classification-capture/*"]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  #checkov:skip=CKV_AWS_111:CloudWatch's PutMetricData and the network-interface actions SageMaker needs for VPC attachment are documented as not resource-scoped.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpcAndReport"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeSecurityGroups",
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/*"]
  }
}

resource "aws_iam_role_policy" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name   = "classifier"
  role   = aws_iam_role.classifier[0].id
  policy = data.aws_iam_policy_document.classifier.json
}

# ── The caller ────────────────────────────────────────────────────────────────
#
# **An endpoint nothing calls is a service in the estate, not a capability.** The model, its role
# and its capture have been declared here since this file was written; what was missing is the
# half that turns a ranking into a disposition. `src/manifest/handlers/classify.py` applies the
# derived floor, the declared band and the contested pairs from `contracts/classification/` — so
# this function is where the project's central sentence is enforced rather than described.

resource "aws_cloudwatch_log_group" "classify" {
  #checkov:skip=CKV_AWS_338:Execution telemetry on a short-lived estate.
  count = var.enable_classifier ? 1 : 0

  name              = "/aws/lambda/${var.project}-classify"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
  tags              = { "${var.project}:expires-at" = var.expires_at }
}

resource "aws_iam_role" "classify" {
  count = var.enable_classifier ? 1 : 0

  name               = "${var.project}-classify"
  description        = "Asks the classification endpoint for a ranking. Decides nothing itself."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { "${var.project}:expires-at" = var.expires_at }
}

# **`count` here too, and its absence is the defect the both-ways plan check exists for.**
#
# A policy document with no count is evaluated whether or not the feature is on, and this one
# indexes `aws_cloudwatch_log_group.classify[0]` — which does not exist when the classifier is
# off. Every deploy with the flag off would have failed at plan, which is to say every ordinary
# deploy, and `terraform validate` cannot see it because both shapes are type-correct.
data "aws_iam_policy_document" "classify" {
  count = var.enable_classifier ? 1 : 0

  statement {
    sid    = "AskTheEndpoint"
    effect = "Allow"
    # `InvokeEndpoint` and nothing else. This role cannot create a model, cannot change an
    # endpoint's configuration and cannot read the artefact — so the one thing it could do to
    # alter a proposal is the one thing it is not granted.
    actions   = ["sagemaker:InvokeEndpoint"]
    resources = ["arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:endpoint/${var.project}-hs-classifier"]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
  }

  #checkov:skip=CKV_AWS_111:The network-interface actions Lambda needs for VPC attachment are documented as not resource-scoped.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "AttachToTheVpcAndLog"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${one(aws_cloudwatch_log_group.classify[*].arn)}:*"]
  }
}

resource "aws_iam_role_policy" "classify" {
  count = var.enable_classifier ? 1 : 0

  name   = "classify"
  role   = aws_iam_role.classify[0].id
  policy = data.aws_iam_policy_document.classify[0].json
}

resource "aws_lambda_function" "classify" {
  #checkov:skip=CKV_AWS_115:Reserved concurrency is set below.
  #checkov:skip=CKV_AWS_116:Synchronous invocations; a dead-letter queue receives only from asynchronous ones.
  #checkov:skip=CKV_AWS_272:Code signing needs a profile owned by bootstrap; the zip's integrity here is the deploy role's write scope plus the source hash.
  count = var.enable_classifier ? 1 : 0

  function_name = "${var.project}-classify"
  description   = "Ranks tariff headings at the endpoint, decides against contracts/classification/"
  role          = aws_iam_role.classify[0].arn
  runtime       = "python3.12"
  handler       = "manifest.handlers.classify.handler"
  s3_bucket     = var.records_bucket
  s3_key        = var.publish_package_key

  source_code_hash = var.publish_package_hash

  # A serverless endpoint's first request after an idle period waits for a container. The
  # handler's own client gives up at 30s and says so; this is the outer bound.
  timeout     = 60
  memory_size = 512

  # The endpoint's own ceiling is five concurrent requests, and every proposal it produces lands
  # in a review queue with a declared capacity. A caller that could outrun both would be building
  # a backlog, which doctrine rule 1 calls a failure of the system rather than of the reviewers.
  reserved_concurrent_executions = 5

  kms_key_arn = var.data_key_arn

  environment {
    variables = {
      CLASSIFIER_ENDPOINT = aws_sagemaker_endpoint.classifier[0].name
      CONTRACTS_DIR       = "/var/task/contracts"
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  tracing_config { mode = "Active" }

  depends_on = [aws_cloudwatch_log_group.classify]
  tags       = { "${var.project}:expires-at" = var.expires_at }
}
