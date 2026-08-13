# The registry the reader image lives in.
#
# **Here rather than in `infra/extraction`, and the reason is ordering.** The image has to exist
# before a function can be created from it, so a registry owned by the layer that consumes it
# would have to be created by the same apply that needs it already populated. The first run
# fails at `docker push` — four minutes in, with the environment approval already spent — and
# every subsequent run works, which is the worst kind of bug to leave in a deploy path.
#
# It also belongs here on its own merits: a registry is shared, long-lived infrastructure like
# the KMS keys and the data zones beside it, not a detail of one pipeline.

resource "aws_ecr_repository" "reader" {
  name                 = "${var.project}-reader"
  image_tag_mutability = "IMMUTABLE"

  # A repository holding images refuses to be deleted. On a portfolio estate that is a teardown
  # that stops halfway and leaves a registry standing — and the images are rebuildable from the
  # Dockerfile in one command, so there is nothing here that deleting loses.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }

  tags = { "${var.project}:expires-at" = var.expires_at }

  # The key existing is not permission to use it. See `scripts/check_deploy_path.py` — a first apply orders these two however it likes.
  depends_on = [aws_kms_key_policy.data]
}

# The reprocessing job's interpreter. A second repository rather than a second tag in the first,
# because the two images have nothing in common and are rebuilt on different occasions: the
# reader changes when the binary or the language data does, and this one when the driver needs a
# different interpreter. One repository would make an image scan on either look like a finding
# about both.
resource "aws_ecr_repository" "job" {
  name                 = "${var.project}-reprocessing"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }

  tags = { "${var.project}:expires-at" = var.expires_at }

  # The key existing is not permission to use it. See `scripts/check_deploy_path.py` — a first apply orders these two however it likes.
  depends_on = [aws_kms_key_policy.data]
}

# **The service pulls this image as itself, and a repository policy is the only place to say so.**
#
# EMR Serverless does not pull a custom image as the job role; it pulls as
# `emr-serverless.amazonaws.com`. Without this, `StartJobRun` is refused with *"EMR Serverless
# service principal is not authorized to perform: ECR:BatchGetImage"* — at submission, after the
# image is built, pushed and attached to an application that reports it correctly.
#
# The `aws:SourceArn` condition is what keeps it from being a grant to every EMR Serverless
# application in every account: only applications in this one may pull.
data "aws_iam_policy_document" "job_repository" {
  statement {
    sid    = "EmrServerlessPullsTheInterpreterImage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
    actions = [
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
    ]
    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:emr-serverless:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:/applications/*"]
    }
  }
}

resource "aws_ecr_repository_policy" "job" {
  repository = aws_ecr_repository.job.name
  policy     = data.aws_iam_policy_document.job_repository.json
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

# **The registry must let Lambda pull, and it does not by default.**
#
# `CreateFunction` returned *"Lambda does not have permission to access the ECR image. Check the
# ECR permissions."* — a 403 from Lambda rather than from IAM, because the missing grant is on
# the **repository**, not on the deploy role or on the function's execution role.
#
# It is the least discoverable permission in this estate: the deploy role could create the
# repository, push to it, and create a function pointing at it, and the failure names neither
# of those things. A container function is the only resource here whose service reads a policy
# attached to a *different* resource in order to start.
#
# Scoped to functions in this account, so the repository is readable by this estate's Lambda
# service principal and by nothing else.
data "aws_iam_policy_document" "reader_repository" {
  statement {
    sid    = "LambdaMayPullTheReaderImage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]

    # Without this the repository trusts the Lambda service globally — any function in any
    # account could pull this image. The condition binds it to functions in this one.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_ecr_repository_policy" "reader" {
  repository = aws_ecr_repository.reader.name
  policy     = data.aws_iam_policy_document.reader_repository.json
}
