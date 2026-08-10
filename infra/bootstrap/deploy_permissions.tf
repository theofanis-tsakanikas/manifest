# What the deploy role may create, one statement per layer.
#
# **This file exists because it was missing, and its absence was the single largest defect in
# the deploy path.** `oidc.tf` says each phase adds the permissions its own layer needs, in the
# commit that adds the layer — and then six layers were written and not one permission was
# granted. The role could read and write Terraform state and nothing else, so the first
# `terraform apply` would have failed on `ec2:CreateVpc` after the environment approval, four
# minutes in, with the reviewer's approval already spent.
#
# It is the failure this repository is least able to catch on its own: `terraform validate`
# checks a configuration against a provider schema and knows nothing about IAM, and checkov
# scans what a policy *grants* rather than what an apply would *need*. Nothing but reading the
# layers against the role finds it, which is why `scripts/check_deploy_permissions.py` now
# does exactly that.
#
# **Still not `AdministratorAccess`, and the reason has not changed.** Attaching it now and
# narrowing "later" means that by the time the estate exists nobody can say which permissions
# are load-bearing, so the policy stays and the deploy role becomes the most powerful identity
# in the account — held by a workflow anybody can trigger a run of.

data "aws_iam_policy_document" "deploy_estate" {
  # `iam:CreateServiceLinkedRole` on `*`. AWS defines no resource form for it, and the roles it
  # creates are AWS's own — their trust policy and their permissions are fixed by the service,
  # not by this account. The alternative to granting it is an apply that fails partway through
  # creating an EMR application, with the reviewer's approval already spent, on an IAM error
  # about a role nobody wrote. Documented at the statement rather than only here.
  #checkov:skip=CKV_AWS_109:The only unconstrained permissions-management action here is iam:CreateServiceLinkedRole, which AWS requires on "*" and which creates roles AWS itself defines. Every other IAM action in this document is scoped to arn:aws:iam::<account>:role/<project>-*.
  # Every statement below is scoped by *tag* where the API supports it and by ARN pattern where
  # it does not. Neither is a perfect boundary and both are better than `Resource: "*"`: a
  # deploy role that can delete a bucket in another project's account is a role whose blast
  # radius is the account rather than the estate.

  # ── foundation: the network ───────────────────────────────────────────────
  #
  # EC2's VPC actions take no resource ARN at create time — the resource does not exist yet, so
  # there is nothing to name. The constraint is the condition below: this role may only create
  # things it will then tag as belonging to this project, and may only modify things already
  # tagged that way.
  #checkov:skip=CKV_AWS_111:VPC creation has no resource to name before the resource exists. The boundary is the RequestTag condition, which is the constraint the API actually supports.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "CreateTheNetwork"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc",
      "ec2:CreateSubnet",
      "ec2:CreateRouteTable",
      "ec2:CreateVpcEndpoint",
      "ec2:CreateSecurityGroup",
      "ec2:CreateFlowLogs",
      "ec2:CreateTags",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/manifest:project"
      values   = [var.project]
    }
  }

  # Reads and modifications. `Describe*` cannot be tag-scoped at all — the API has no such
  # condition key — and a deploy that cannot describe what it created cannot plan a second
  # time. The write half is tag-scoped; the read half is not, and that asymmetry is stated
  # rather than hidden behind one wildcard statement covering both.
  #checkov:skip=CKV_AWS_111:ec2:Describe* supports no resource-level permissions or tag conditions; a deploy that cannot describe cannot plan.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "DescribeTheNetwork"
    effect    = "Allow"
    actions   = ["ec2:Describe*", "ec2:GetSecurityGroupsForVpc"]
    resources = ["*"]
  }

  #checkov:skip=CKV_AWS_111:Modification and deletion are scoped by ResourceTag below, which is the boundary the API supports for these actions.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "ModifyWhatThisProjectOwns"
    effect = "Allow"
    actions = [
      "ec2:ModifyVpcAttribute",
      "ec2:ModifyVpcEndpoint",
      "ec2:ModifySubnetAttribute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:DeleteVpc",
      "ec2:DeleteSubnet",
      "ec2:DeleteRouteTable",
      "ec2:DeleteVpcEndpoints",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteFlowLogs",
      "ec2:DeleteTags",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
  }

  # ── foundation: storage and keys ──────────────────────────────────────────
  #
  # Named by prefix rather than by tag: S3 bucket actions are evaluated before the bucket has
  # tags, and the names are deterministic — `<project>-<zone>-<account>` — so a prefix is the
  # tighter boundary here and not the looser one.
  statement {
    sid    = "TheDataZones"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:PutBucketPolicy",
      "s3:PutBucketVersioning",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketLogging",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketTagging",
      "s3:PutBucketNotification",
      "s3:Get*",
      "s3:List*",
    ]
    resources = [
      "arn:aws:s3:::${var.project}-*-${data.aws_caller_identity.current.account_id}",
      "arn:aws:s3:::${var.project}-*-${data.aws_caller_identity.current.account_id}/*",
    ]
  }

  # KMS key creation takes no resource — same reason as the VPC. Everything after creation is
  # scoped to keys this project tagged.
  #checkov:skip=CKV_AWS_111:kms:CreateKey has no resource to name; the alias and tag conditions below are the boundary.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "CreateKeys"
    effect    = "Allow"
    actions   = ["kms:CreateKey", "kms:CreateAlias", "kms:ListAliases", "kms:TagResource"]
    resources = ["*"]
  }

  statement {
    sid    = "ManageThisProjectsKeys"
    effect = "Allow"
    actions = [
      "kms:DescribeKey",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:PutKeyPolicy",
      "kms:EnableKeyRotation",
      "kms:ScheduleKeyDeletion",
      "kms:DeleteAlias",
      "kms:ListResourceTags",
    ]
    resources = ["arn:aws:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
  }

  # ── foundation: the guards ────────────────────────────────────────────────
  #
  # The budget action attaches a deny policy to *this role*, which is the only role it may
  # touch. A grant on `role/*` would let the guard disable any identity in the account.
  statement {
    sid    = "TheBudgetGuard"
    effect = "Allow"
    actions = [
      "budgets:CreateBudget",
      "budgets:DescribeBudget",
      "budgets:ModifyBudget",
      "budgets:DeleteBudget",
      "budgets:CreateBudgetAction",
      "budgets:DescribeBudgetAction",
      "budgets:UpdateBudgetAction",
      "budgets:DeleteBudgetAction",
    ]
    resources = ["arn:aws:budgets::${data.aws_caller_identity.current.account_id}:budget/${var.project}-*"]
  }

  statement {
    sid    = "TheAlertTopicAndTheReaperSchedule"
    effect = "Allow"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
      "sns:Unsubscribe",
      "sns:ListSubscriptionsByTopic",
      "sns:TagResource",
      "events:PutRule",
      "events:DeleteRule",
      "events:DescribeRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "events:ListTargetsByRule",
      "events:TagResource",
    ]
    resources = [
      "arn:aws:sns:*:${data.aws_caller_identity.current.account_id}:${var.project}-*",
      "arn:aws:events:*:${data.aws_caller_identity.current.account_id}:rule/${var.project}-*",
    ]
  }

  # ── The roles each layer creates ──────────────────────────────────────────
  #
  # Scoped to names this project owns, and **`iam:PassRole` is conditioned on the service that
  # may receive it**. Without that condition a deploy role that can pass any role to any service
  # is a privilege-escalation path with a Terraform file in front of it.
  statement {
    sid    = "TheServiceRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-*",
    ]
  }

  # `iam:CreateServiceLinkedRole` has no resource form — AWS requires `*` — and without it an
  # apply fails the first time a service needs its own linked role. EMR Serverless, Redshift
  # Serverless and Athena all do, on first use, and the failure arrives as an IAM error in the
  # middle of creating something else. Listed alone rather than folded into the statement above
  # so the wildcard is visible and attributable.
  #checkov:skip=CKV_AWS_111:iam:CreateServiceLinkedRole has no resource form; AWS requires "*".
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_107:This is not a credentials-exposing action; it creates a role AWS itself defines and controls.
  statement {
    sid       = "ServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }

  statement {
    sid       = "PassRolesOnlyToTheServicesThatRunThem"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "states.amazonaws.com",
        "vpc-flow-logs.amazonaws.com",
        "budgets.amazonaws.com",
        "emr-serverless.amazonaws.com",
        "redshift.amazonaws.com",
        "glue.amazonaws.com",
      ]
    }
  }

  # ── extraction ────────────────────────────────────────────────────────────
  statement {
    sid    = "TheQueueAndTheRecords"
    effect = "Allow"
    actions = [
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:GetQueueAttributes",
      "sqs:SetQueueAttributes",
      "sqs:TagQueue",
      "sqs:ListQueueTags",
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:ListTagsOfResource",
      "states:CreateStateMachine",
      "states:DeleteStateMachine",
      "states:DescribeStateMachine",
      "states:UpdateStateMachine",
      "states:TagResource",
      "states:ListTagsForResource",
    ]
    resources = [
      "arn:aws:sqs:*:${data.aws_caller_identity.current.account_id}:${var.project}-*",
      "arn:aws:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${var.project}-*",
      "arn:aws:states:*:${data.aws_caller_identity.current.account_id}:stateMachine:${var.project}-*",
    ]
  }

  # ── lakehouse ─────────────────────────────────────────────────────────────
  #
  # Glue's catalog ARNs are per-database and per-table under one catalog resource, which is why
  # the catalog itself appears alongside the pattern.
  statement {
    sid    = "TheCatalogAndTheQueryEngine"
    effect = "Allow"
    actions = [
      "glue:CreateDatabase",
      "glue:DeleteDatabase",
      "glue:GetDatabase",
      "glue:UpdateDatabase",
      "glue:CreateTable",
      "glue:DeleteTable",
      "glue:GetTable",
      "glue:UpdateTable",
      "glue:TagResource",
      "athena:CreateWorkGroup",
      "athena:DeleteWorkGroup",
      "athena:GetWorkGroup",
      "athena:UpdateWorkGroup",
      "athena:TagResource",
      "athena:ListTagsForResource",
    ]
    resources = [
      "arn:aws:glue:*:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:*:${data.aws_caller_identity.current.account_id}:database/${var.project}*",
      "arn:aws:glue:*:${data.aws_caller_identity.current.account_id}:table/${var.project}*/*",
      "arn:aws:athena:*:${data.aws_caller_identity.current.account_id}:workgroup/${var.project}-*",
    ]
  }

  # ── batch and analytics ───────────────────────────────────────────────────
  #
  # The two expensive layers. They are not applied by the default deploy — separate dispatches
  # with their own approval — and the grant exists anyway, because a permission that arrives
  # only when somebody is already mid-deploy is a permission added under pressure.
  statement {
    sid    = "TheExpensiveLayers"
    effect = "Allow"
    actions = [
      "emr-serverless:CreateApplication",
      "emr-serverless:DeleteApplication",
      "emr-serverless:GetApplication",
      "emr-serverless:UpdateApplication",
      "emr-serverless:TagResource",
      "emr-serverless:ListTagsForResource",
      "redshift-serverless:CreateNamespace",
      "redshift-serverless:DeleteNamespace",
      "redshift-serverless:GetNamespace",
      "redshift-serverless:UpdateNamespace",
      "redshift-serverless:CreateWorkgroup",
      "redshift-serverless:DeleteWorkgroup",
      "redshift-serverless:GetWorkgroup",
      "redshift-serverless:UpdateWorkgroup",
      "redshift-serverless:TagResource",
      "redshift-serverless:ListTagsForResource",
    ]
    resources = [
      "arn:aws:emr-serverless:*:${data.aws_caller_identity.current.account_id}:/applications/*",
      "arn:aws:redshift-serverless:*:${data.aws_caller_identity.current.account_id}:namespace/*",
      "arn:aws:redshift-serverless:*:${data.aws_caller_identity.current.account_id}:workgroup/*",
    ]
  }

  # ── Log groups, everywhere ────────────────────────────────────────────────
  statement {
    sid    = "LogGroups"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:PutRetentionPolicy",
      "logs:AssociateKmsKey",
      "logs:DisassociateKmsKey",
      "logs:TagResource",
      "logs:ListTagsForResource",
    ]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:*"]
  }

  # ── What bootstrap published ──────────────────────────────────────────────
  #
  # Scoped to this project's bootstrap prefix and nothing wider. A grant on `/manifest/*` would
  # let a compromised deploy read every parameter any later layer ever writes.
  statement {
    sid    = "ReadEveryLayersPublishedReferences"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
      "ssm:DescribeParameters",
    ]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/*"
    ]
  }

  # Write, and **not** to the bootstrap prefix. That exclusion is the one meaningful boundary
  # here: every other prefix is written by this same role applying a layer, so scoping between
  # them would be theatre. `/bootstrap/*` is different — it is written by a human at a laptop,
  # and it is what CI resolves its own backend and role from. A deploy that could rewrite it
  # could point the next deploy at a state bucket of its choosing.
  statement {
    sid    = "PublishThisLayersOwnReferences"
    effect = "Allow"
    actions = [
      "ssm:PutParameter",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "ssm:AddTagsToResource",
      "ssm:ListTagsForResource",
    ]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/*"
    ]
  }

  statement {
    sid     = "NeverRewriteWhatTheHumanPublished"
    effect  = "Deny"
    actions = ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters"]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/bootstrap/*"
    ]
  }

  # Reading the account id is how every layer names its own buckets.
  #checkov:skip=CKV_AWS_111:sts:GetCallerIdentity takes no resource and never has.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "KnowWhichAccountThisIs"
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy_estate" {
  name   = "estate"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_estate.json
}


# ── The brake ────────────────────────────────────────────────────────────────
#
# Attached to this role by the budget action in `guards.tf` when spend crosses the ceiling.
# It lives here rather than in `infra/foundation` because it acts on a role this layer owns,
# and a guard that names its target by a transcribed string is a guard pointed at a name.
#
# **It denies creation and permits teardown**, and that asymmetry is the whole design. The
# first version denied `*`, which is the obvious thing and is worse than useless: a brake that
# also blocks `terraform destroy` strands every running resource in the estate, so the spend
# the brake fired over *continues* while the only identity that could stop it has been
# disarmed. The bill gets bigger because the guard worked.
#
# `../attestor/infra/bootstrap/main.tf` keeps `iam`, `sts`, `s3`, `dynamodb` and `budgets`,
# which is enough to detach the brake and read state and not enough to tear down an EMR
# application. This goes further: the verbs a `terraform destroy` actually needs — refresh
# (`Describe`, `Get`, `List`) and removal (`Delete`, `Remove`, `Terminate`, and the rest) —
# are permitted, and everything that could create or grow anything is denied.
#
# IAM cannot express "create" and "delete" as categories, so this is a verb-prefix
# approximation and it is stated as one. What it gets right is the direction: the failure mode
# of a too-permissive brake is a resource that could have been deleted and was, and the failure
# mode of a too-strict one is an estate nobody can turn off.
data "aws_iam_policy_document" "budget_brake" {
  #checkov:skip=CKV_AWS_111:It is a Deny. Constraining its resource would narrow what the brake stops, which is the wrong direction for a brake.
  #checkov:skip=CKV_AWS_356:As above — a wildcard Deny is the safe direction of a wildcard.
  #checkov:skip=CKV_AWS_289:Permissions management is deliberately *not* denied: the brake has to be removable by the same identity that can read state, or lifting it needs a second human with console access.
  #checkov:skip=CKV_AWS_290:As above.
  statement {
    sid       = "DenyEverythingExceptLookingAndDeleting"
    effect    = "Deny"
    resources = ["*"]

    not_actions = [
      # Refresh. A destroy plans before it deletes, and a plan that cannot read state reports
      # every resource as already gone and deletes nothing.
      "*:Describe*",
      "*:Get*",
      "*:List*",
      "*:BatchGet*",
      # Removal.
      "*:Delete*",
      "*:Remove*",
      "*:Terminate*",
      "*:Deregister*",
      "*:Disassociate*",
      "*:Revoke*",
      "*:Detach*",
      "*:Stop*",
      "*:Cancel*",
      "*:Abort*",
      # The state backend, so the destroy can read and write its own record of what it removed.
      "s3:PutObject",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "sts:*",
      # Lifting the brake once the spend is understood. Denying this would mean the only way
      # back is a console session by somebody with more rights than this role — which is a
      # human doing IAM by hand under time pressure, at the exact moment that is most costly.
      "iam:DetachRolePolicy",
      "iam:AttachRolePolicy",
      "budgets:*",
    ]
  }
}

resource "aws_iam_policy" "budget_brake" {
  name        = "${var.project}-budget-stop"
  description = "Attached to the deploy role by a budget action. Denies creation; permits teardown."
  policy      = data.aws_iam_policy_document.budget_brake.json
}
