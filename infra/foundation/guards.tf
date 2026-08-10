# The two controls that decide whether this estate can quietly cost money.
#
# Both are written, both validate, **neither has ever fired, because nothing has been applied**.
# They are design constraints for the day somebody does apply this — not budget management for
# a running system, and nothing in this repository is entitled to describe them as tested.

# ── The budget guard is NOT here ─────────────────────────────────────────────
#
# It moved to `infra/bootstrap`, and the move is the point. The guard's action attaches a deny
# policy to the **deploy role**, which `bootstrap` owns — so from here it could only name that
# role by a transcribed string. `var.deploy_role_name = "manifest-deploy"` was exactly that: a
# cross-layer reference by hand-typed name, in the file whose job is to stop an estate costing
# money, pointed at a role this layer cannot see and cannot verify exists.
#
# `../attestor/infra/bootstrap/main.tf` puts budget, action and brake beside the role for that
# reason. A guard belongs with the thing it disarms.

# ── The reaper ───────────────────────────────────────────────────────────────
#
# A scheduled rule that reads `manifest:expires-at` and destroys what has passed it. The
# schedule is written here; what it *invokes* is the destroy workflow, which is gated behind
# the environment its OIDC trust names. That workflow has now been dispatched by hand (see
# decision 14); the reaper's own path to it has not fired, because nothing has yet passed its
# `manifest:expires-at`.
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
