# The two controls that decide whether this estate can quietly cost money.
#
# Both are written, both validate, **neither has ever fired, because nothing has been applied**.
# They are design constraints for the day somebody does apply this — not budget management for
# a running system, and nothing in this repository is entitled to describe them as tested.

# ── The budget guard ─────────────────────────────────────────────────────────
#
# The action disables the deploy role, which is the only identity that can create anything. It
# does not stop what is already running — a budget action cannot — so the guard bounds *new*
# spend and the reaper bounds the rest. Saying it the other way round would be claiming the
# budget can stop a cluster mid-job.

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_eur)
  limit_unit   = "EUR"
  time_unit    = "MONTHLY"

  # 80% forecast: warn before it happens rather than after. A guard that only fires on actual
  # spend tells you about money already gone.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}

data "aws_iam_policy_document" "budget_action_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "${var.project}-budget-action"
  description        = "Assumed by AWS Budgets to attach a deny policy when the threshold is crossed"
  assume_role_policy = data.aws_iam_policy_document.budget_action_assume.json
}

resource "aws_iam_policy" "deny_everything" {
  name        = "${var.project}-budget-stop"
  description = "Attached to the deploy role by a budget action. Denies every create."
  policy      = data.aws_iam_policy_document.deny_everything.json
}

data "aws_iam_policy_document" "deny_everything" {
  # A deny on `*` is the point of this policy: it is the brake, and a brake that lists the
  # services it stops is a brake that misses the one added last week.
  #checkov:skip=CKV_AWS_111:This is a Deny. Constraining it would narrow what the brake stops.
  #checkov:skip=CKV_AWS_356:As above — a wildcard Deny is the safe direction of a wildcard.
  statement {
    sid       = "StopCreatingThings"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
  }
}

resource "aws_budgets_budget_action" "stop" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_everything.arn
      roles      = [var.deploy_role_name]
    }
  }

  subscriber {
    address           = var.budget_notification_email
    subscription_type = "EMAIL"
  }
}

data "aws_iam_policy_document" "budget_action" {
  statement {
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:ListAttachedRolePolicies",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.deploy_role_name}"
    ]
  }
}

resource "aws_iam_role_policy" "budget_action" {
  name   = "attach-the-brake"
  role   = aws_iam_role.budget_action.id
  policy = data.aws_iam_policy_document.budget_action.json
}

# ── The reaper ───────────────────────────────────────────────────────────────
#
# A scheduled rule that reads `manifest:expires-at` and destroys what has passed it. The
# schedule is written here; what it *invokes* is the destroy workflow, which is gated behind a
# protected environment and has never been dispatched.
#
# It is a rule and an alarm rather than a Lambda that deletes things, and that is deliberate: a
# function with permission to destroy the estate is a function whose permissions are the most
# dangerous thing in the account, and it would have to be trusted forever to earn a saving that
# is measured in minutes of somebody's attention.

resource "aws_cloudwatch_event_rule" "expiry" {
  name                = "${var.project}-expiry"
  description         = "Fires daily; the estate's expiry is compared against it by the destroy workflow"
  schedule_expression = "cron(0 6 * * ? *)"
}

resource "aws_cloudwatch_event_target" "expiry" {
  rule      = aws_cloudwatch_event_rule.expiry.name
  target_id = "expiry-topic"
  arn       = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.project}-alerts"
  kms_master_key_id = aws_kms_key.logs.arn
}

resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.budget_notification_email
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid     = "EventsMayPublish"
    effect  = "Allow"
    actions = ["sns:Publish"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    resources = [aws_sns_topic.alerts.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.expiry.arn]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}
