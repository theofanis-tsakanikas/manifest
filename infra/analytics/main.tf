# Redshift Serverless, and the four questions that justify it being here at all.
#
# `docs/DECISIONS.md` 6: if these marts are not built, drop Redshift rather than keeping it as
# a CV keyword. They answer things a single document cannot — duty exposure by HS chapter,
# review-queue economics, extraction cost per client, error rate by source and by carrier — and
# each one is a join across documents and across time, which is the shape a warehouse is for
# and the shape Athena over Iceberg does adequately and slowly.
#
# **Two figures from `docs/AWS-CONSTRAINTS.md`, read 2026-08-09, decide this file.**
#
# The default base capacity is **128 RPUs**, thirty-two times the minimum. A workgroup created
# without setting it is provisioned for a workload nothing in this scenario has, so it is set
# explicitly and the variable has no default that means "whatever the service picks".
#
# And the 4-RPU floor is **not available in eu-central-1** — the documented list is US East
# (Ohio, N. Virginia), US West (N. California, Oregon), AP (Mumbai, Singapore, Sydney, Tokyo),
# EU (Ireland) and EU (Stockholm). The estate's default region is Frankfurt, so the floor here
# is 8 RPUs and the variable's validation says so rather than letting an apply discover it.

resource "aws_redshiftserverless_namespace" "marts" {
  namespace_name        = "${var.project}-marts"
  admin_username        = var.admin_username
  manage_admin_password = true
  kms_key_id            = var.data_key_arn

  # The two that answer questions a single document cannot.
  log_exports = ["userlog", "connectionlog", "useractivitylog"]

  iam_roles = [aws_iam_role.redshift.arn]
}

resource "aws_redshiftserverless_workgroup" "marts" {
  namespace_name = aws_redshiftserverless_namespace.marts.namespace_name
  workgroup_name = "${var.project}-marts"

  # Explicit. The default is 128.
  base_capacity = var.base_capacity_rpu

  # Private. Nothing in this estate is reachable from the internet, and a warehouse is the
  # component most likely to be made public by somebody wanting to point a BI tool at it.
  publicly_accessible = false
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [var.endpoint_security_group_id]

  # **All nine, not the two that matter, and the reason is idempotence.**
  #
  # Redshift maintains a full parameter set on every workgroup and fills in what the request did
  # not mention. Declaring two left seven undeclared, so every later plan saw a difference, sent
  # an `UpdateWorkgroup` — and Redshift refused it with `ValidationException: You didn't specify
  # any changes to the configuration parameters`, because the two it was told about already
  # matched. The layer applied once and could never be applied again: not drift, not a failed
  # create, an **apply that is not repeatable**, which is the property every other layer here is
  # relied on for.
  #
  # The alternative was `ignore_changes = [config_parameter]`, and it is worse: `require_ssl`
  # and `enable_user_activity_logging` are the two controls in this block, and ignoring the
  # attribute they live in means nothing notices when they change. Declaring the service's
  # defaults turns them from assumptions into statements — and if a future Redshift version
  # changes one, this is where the plan will say so.
  config_parameter {
    parameter_key   = "require_ssl"
    parameter_value = "true"
  }

  config_parameter {
    parameter_key   = "enable_user_activity_logging"
    parameter_value = "true"
  }

  config_parameter {
    parameter_key   = "use_fips_ssl"
    parameter_value = "false"
  }

  config_parameter {
    parameter_key   = "auto_mv"
    parameter_value = "true"
  }

  config_parameter {
    parameter_key   = "datestyle"
    parameter_value = "ISO, MDY"
  }

  config_parameter {
    parameter_key   = "enable_case_sensitive_identifier"
    parameter_value = "false"
  }

  config_parameter {
    parameter_key   = "query_group"
    parameter_value = "default"
  }

  config_parameter {
    parameter_key   = "search_path"
    parameter_value = "$user, public"
  }

  # Four hours. A query still running after that is not a slow query; it is a query nobody is
  # waiting for any more, and Redshift Serverless bills for it the whole time.
  config_parameter {
    parameter_key   = "max_query_execution_time"
    parameter_value = "14400"
  }

  # A ceiling on RPU-hours per period. The budget guard disables the deploy role and cannot
  # stop a query; this is the control that bounds the warehouse itself, and without it a single
  # unbounded scan is the largest single line an estate this size can produce.
  max_capacity = var.max_capacity_rpu
}

data "aws_iam_policy_document" "redshift_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["redshift.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "redshift" {
  name               = "${var.project}-redshift"
  assume_role_policy = data.aws_iam_policy_document.redshift_assume.json
}

data "aws_iam_policy_document" "redshift" {
  # Read-only against the lake. The marts are derived; the record is in the records zone and
  # the warehouse has no business writing to either. A warehouse with write access to the
  # customs record is a second path to a value that is supposed to have exactly one.
  statement {
    sid       = "ReadTheLake"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.lake_bucket}", "arn:aws:s3:::${var.lake_bucket}/*"]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [var.data_key_arn]
  }

  statement {
    sid       = "ReadTheCatalogue"
    effect    = "Allow"
    actions   = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"]
    resources = ["*"]
    #checkov:skip=CKV_AWS_111:Catalogue reads need catalogue, database and table ARNs together; the role is read-only and does nothing else.
    #checkov:skip=CKV_AWS_356:As above.
  }
}

resource "aws_iam_role_policy" "redshift" {
  name   = "read-the-lake"
  role   = aws_iam_role.redshift.id
  policy = data.aws_iam_policy_document.redshift.json
}
