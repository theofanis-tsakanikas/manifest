# EMR Serverless for bulk re-extraction. **Written, validated, never run.**
#
# Claim 7 is not proved here and cannot be: no distributed job in this repository has ever been
# executed. Idempotence is proved against the pure planner and its ledger on a laptop
# (`evals/scale/`), and this application is the adapter that would execute a plan that planner
# produced. Saying it the other way round would be claiming a property of a cluster nobody has
# started.
#
# The sizing comes from `docs/AWS-CONSTRAINTS.md`, read 2026-08-09, and one constraint decides
# the shape: a 32-vCPU worker's memory must be exactly 60, 120 or 244 GB, and a job whose total
# (Spark memory plus the default 10% overhead) falls outside 8 GB of one of those is **rejected
# at submission**. That is a failure that reads like a configuration typo, so worker sizing is
# declared data here rather than Spark configuration scattered through a job script.

resource "aws_emrserverless_application" "reprocessing" {
  name          = "${var.project}-reprocessing"
  release_label = var.emr_release
  type          = "SPARK"

  # A ceiling, not a target. EMR Serverless bills for what it uses, so the cap is what stops a
  # runaway plan from being a runaway bill — and the budget guard in `foundation` disables the
  # deploy role rather than stopping a running job, which is why this ceiling exists at all.
  maximum_capacity {
    cpu    = "${var.max_vcpu} vCPU"
    memory = "${var.max_memory_gb} GB"
  }

  # Auto-stop after fifteen minutes idle, which is the documented default and is written out
  # because a default that is not written down is a default somebody changes. An application
  # left running with pre-initialised capacity is the single most expensive way this estate can
  # do nothing.
  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 15
  }

  auto_start_configuration {
    enabled = true
  }

  network_configuration {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.endpoint_security_group_id]
  }

  interactive_configuration {
    # Off. An interactive endpoint on a batch application is a way for somebody to run a
    # notebook against four million documents without a plan, a ledger entry or a diff.
    livy_endpoint_enabled = false
    studio_enabled        = false
  }
}

data "aws_iam_policy_document" "job_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "job" {
  name               = "${var.project}-reprocessing-job"
  assume_role_policy = data.aws_iam_policy_document.job_assume.json
}

data "aws_iam_policy_document" "job" {
  statement {
    sid     = "ReadRecordsAndWriteTheLake"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::${var.records_bucket}",
      "arn:aws:s3:::${var.records_bucket}/*",
      "arn:aws:s3:::${var.lake_bucket}",
      "arn:aws:s3:::${var.lake_bucket}/*",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  # The ledger. This is the whole of claim 7's idempotence in permissions terms: the job may
  # read what has been done and append what it completes, and it may not delete. A job that
  # could delete a ledger entry could make already-processed work look undone, which is the
  # expensive failure, and it could do so halfway through and leave no trace.
  statement {
    sid       = "ReadAndAppendTheLedger"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem"]
    resources = [var.ledger_table_arn]
  }

  statement {
    sid       = "CatalogueTheTables"
    effect    = "Allow"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions", "glue:UpdateTable"]
    resources = ["*"]
    #checkov:skip=CKV_AWS_111:Glue catalogue reads need the catalogue, database and table ARNs together; narrowing to the table alone breaks GetDatabase, and the role does nothing else.
    #checkov:skip=CKV_AWS_356:As above.
  }

  # **The executors start the per-document pipeline; they do not read pages themselves.**
  #
  # `pipelines/reprocess.py` explains why at length: every threshold here is keyed to one reader
  # identity, asserted at build time in `Dockerfile`, and Amazon Linux 2023 — which every EMR
  # Serverless custom image is built on — carries no tesseract at all. Compiling it onto the
  # cluster would be a second build of the binary whose exact build is the unit of evidence. So
  # the estate has one reader, and this role's power over it is to start the machine that calls
  # it and to watch the result.
  #
  # `StartExecution` and the two reads, and nothing that stops or redrives one. A bulk job that
  # could cancel a running execution could abandon a document halfway between read and publish.
  statement {
    sid       = "StartThePerDocumentPipeline"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [var.state_machine_arn]
  }

  statement {
    sid       = "WatchWhatItStarted"
    effect    = "Allow"
    actions   = ["states:DescribeExecution"]
    resources = ["${replace(var.state_machine_arn, ":stateMachine:", ":execution:")}:*"]
  }

  # Listing the landing bucket, because a document id is not a key. The key carries the language
  # and the document type, both decided by whoever uploaded the object, and a job that rebuilt
  # the key from a convention would silently read the wrong page for anything that did not
  # follow it. Read-only: this role may find the source objects and may not change them.
  statement {
    sid     = "FindTheSourceDocuments"
    effect  = "Allow"
    actions = ["s3:ListBucket", "s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.landing_bucket}",
      "arn:aws:s3:::${var.landing_bucket}/*",
    ]
  }

  # `Scan` reads the ledger whole, which is what planning needs: the plan is a function of every
  # entry, and a query per document would be four million round trips to answer one question.
  statement {
    sid       = "ReadTheWholeLedgerToPlan"
    effect    = "Allow"
    actions   = ["dynamodb:Scan"]
    resources = [var.ledger_table_arn]
  }

  statement {
    sid       = "Log"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.jobs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "reprocessing"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

resource "aws_cloudwatch_log_group" "jobs" {
  #checkov:skip=CKV_AWS_338:Job telemetry on a short-lived estate; the customs record is in the records bucket, which has no expiry.
  name              = "/aws/emr-serverless/${var.project}"
  retention_in_days = 30
  kms_key_id        = var.logs_key_arn
}
