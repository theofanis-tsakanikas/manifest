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

# Enabling the endpoint without naming its artefacts would produce a model resource pointing at
# an empty image URI — which fails at apply, four minutes in, with the approval already spent.
# Checked here so that it fails at *plan*, by name, before anything is created.
check "classifier_artefacts_are_named" {
  assert {
    condition = !var.enable_classifier || (
      var.classifier_image_uri != "" && var.classifier_model_data_url != ""
    )
    error_message = "enable_classifier is true and classifier_image_uri or classifier_model_data_url is empty."
  }
}

resource "aws_sagemaker_model" "classifier" {
  count = var.enable_classifier ? 1 : 0

  name               = "${var.project}-hs-classifier"
  execution_role_arn = aws_iam_role.classifier[0].arn

  primary_container {
    image          = var.classifier_image_uri
    model_data_url = var.classifier_model_data_url

    environment = {
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
