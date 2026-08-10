# The cost guard, beside the role it disarms.
#
# It is in this layer and not in `foundation` because its action attaches a policy to the
# **deploy role**, which this layer owns. From `foundation` the role could only be named by a
# transcribed string — and a guard whose target is a hand-typed name is a guard pointed at a
# name rather than at a role.
#
# **The ceiling is declared in euro and enforced in dollars, and that gap is stated rather than
# hidden.**
#
# AWS Budgets refuses `EUR`: *"EUR is not in the supported unit set: [USD]"*. It surfaced on the
# first real apply, because `terraform validate` checks that an attribute exists and not that a
# service will accept its value — a distinction `scripts/tf_validate.py` already warns about in
# its own docstring, and here is the first time it cost something.
#
# So the design ceiling stays what `CLAUDE.md` says it is, in euro, and the *guard* is set in
# dollars with the conversion written down: a rate, a source and a date, exactly as every other
# figure in this repository has to carry. The converted number is deliberately generous — a
# guard that fires early because the euro moved is a guard somebody raises, and a raised guard
# is no guard.
resource "aws_budgets_budget" "estate" {
  name         = "${var.project}-estate"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Forecast first. A guard that only fires on actual spend tells you about money already gone.
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

# At the ceiling the deploy role loses its ability to create anything and keeps its ability to
# tear things down — see `budget_brake` in `deploy_permissions.tf` for why that asymmetry is the
# whole design. **Not an email: an action.** An alert that arrives while nobody is reading it
# has never stopped a bill.
resource "aws_budgets_budget_action" "brake" {
  budget_name        = aws_budgets_budget.estate.name
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
      policy_arn = aws_iam_policy.budget_brake.arn
      roles      = [aws_iam_role.deploy.name]
    }
  }

  subscriber {
    address           = var.budget_notification_email
    subscription_type = "EMAIL"
  }
}

data "aws_iam_policy_document" "budget_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "${var.project}-budget-action"
  description        = "Assumed by AWS Budgets to attach the brake when the ceiling is crossed"
  assume_role_policy = data.aws_iam_policy_document.budget_assume.json
}

resource "aws_iam_role_policy" "budget_action" {
  name = "attach-the-brake"
  role = aws_iam_role.budget_action.id

  # One role, named by ARN. A grant on `role/*` would let the guard disable any identity in the
  # account, which is a larger power than the thing it is guarding against.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:GetRole"]
      Resource = aws_iam_role.deploy.arn
    }]
  })
}
