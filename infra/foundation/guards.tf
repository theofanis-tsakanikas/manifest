# The two controls that decide whether this estate can quietly cost money.
#
# **One of them has now fired, and the other has not.** This header used to say that neither had
# ever fired and give the estate's unapplied state as the reason. That reason lapsed on
# 2026-08-10, and the claim itself was wrong about the more interesting half five days later.
#
# The budget action **fired on 2026-08-15**: it attached its deny policy to the deploy role and
# the estate stopped being deployable. The brake worked exactly as written. What was wrong was
# what it measured — the whole account rather than this project, so a sibling project's spend
# tripped it. That is a different defect with a different fix, recorded with a dated acceptance
# in `contracts/deploy/budget.yaml`, and it does not make the guard untested. It makes it the
# only control here that has been.
#
# The reaper has still never fired, and the reason is below: nothing has yet passed its
# `manifest:expires-at`, because every estate so far was destroyed by hand the same day.

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

  # The key existing is not permission to use it. See `scripts/check_deploy_path.py` — a first apply orders these two however it likes.
  depends_on = [aws_kms_key_policy.logs]
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
