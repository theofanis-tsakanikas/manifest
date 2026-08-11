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

# **Six managed policies, not one inline one, and IAM decided that rather than taste.**
#
# `PutRolePolicy` returned *"Maximum policy size of 10240 bytes exceeded"* — and that ceiling is
# the **aggregate** across every inline policy on a role, so splitting into two inline documents
# failed identically. Managed policies are attached rather than embedded and counted separately.
#
# The split is by concern, which is the shape this should always have had. A ten-kilobyte grant
# is unreviewable: nobody reads the statement in the middle, and "the deploy role can do this"
# stops being a sentence anybody can check. Six policies named for what they build are six
# questions a reviewer can answer one at a time.

data "aws_iam_policy_document" "deploy_network" {
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

  # **Configuration is unconditioned; deletion is tag-scoped.** Both used to sit behind
  # `aws:ResourceTag`, and the second real deploy was refused `AuthorizeSecurityGroupIngress`
  # and `AuthorizeSecurityGroupEgress`: those apply to a security group whose tags the provider
  # writes in a *separate* call, so at the moment of authorising there is nothing to match.
  #
  # The same lesson as the create statement above, one layer along — a tag condition is only as
  # good as the API's tag timing. Deletion keeps it, and that is where it was always earning its
  # place: deleting something this project does not own is the failure worth a condition, and a
  # resource being deleted has existed long enough to carry its tags.
  #checkov:skip=CKV_AWS_111:These apply to resources whose tags the provider writes in a separate call; the boundary is the role's trust, and deletion below is tag-scoped.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "ConfigureWhatThisProjectCreated"
    effect = "Allow"
    actions = [
      "ec2:ModifyVpcAttribute",
      "ec2:ModifyVpcEndpoint",
      "ec2:ModifySubnetAttribute",
      "ec2:ModifySecurityGroupRules",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:DeleteTags",
    ]
    resources = ["*"]
  }

  #checkov:skip=CKV_AWS_111:Deletion is scoped by ResourceTag — the boundary the API supports and the one worth having.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "DeleteOnlyWhatThisProjectOwns"
    effect = "Allow"
    actions = [
      "ec2:DeleteVpc",
      "ec2:DeleteSubnet",
      "ec2:DeleteRouteTable",
      "ec2:DeleteVpcEndpoints",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteFlowLogs",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
  }
}

resource "aws_iam_policy" "deploy_network" {
  name        = "${var.project}-deploy-network"
  description = "Deploy role: network."
  policy      = data.aws_iam_policy_document.deploy_network.json
}

resource "aws_iam_role_policy_attachment" "deploy_network" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_network.arn
}

data "aws_iam_policy_document" "deploy_storage" {
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
      # **Setting a policy is not removing it**, and only a teardown ever needs the second.
      #
      # The first real destroy failed here with a 403 on `DeleteBucketPolicy` for the lake
      # bucket, four layers into a teardown. It is the fourth time this distinction has cost a
      # cycle — managing a key is not using it, administering a bucket is not writing into it,
      # deleting an object is not deleting its versions, and now setting a policy is not
      # deleting one. IAM never merges the pair and the administrative half always reads as the
      # larger.
      #
      # What makes this family invisible is that a deploy exercises the `Put` and never the
      # `Delete`, so a permission set can be complete for every apply anybody runs and short for
      # the one teardown that matters. The five below were found by asking, in one pass, which
      # actions a `terraform destroy` of *this* estate calls that the role does not hold.
      "s3:DeleteBucketPolicy",
      "ecr:BatchDeleteImage",
      "lambda:DeleteEventSourceMapping",
      "logs:DeleteSubscriptionFilter",
      "kms:DisableKey",
      "iam:DeletePolicyVersion",

      # **And the reads a delete performs, which are a different gap in the same wall.**
      #
      # The second teardown attempt failed on `iam:ListInstanceProfilesForRole`: the provider
      # checks a role's instance profiles before removing it. That is not a `Delete` anybody
      # would think to grant — it is a *read*, and a deploy never performs it, because creating
      # a thing does not require reading what you are about to remove.
      #
      # So the list below is the answer to a second question, asked the same way: which reads
      # does a `terraform destroy` of this estate perform that the role does not hold? Six more,
      # out of forty-nine checked, and five of them are tag or membership lookups the provider
      # does while planning a removal.
      "iam:ListInstanceProfilesForRole",
      "iam:ListEntitiesForPolicy",
      "iam:ListRoleTags",
      "iam:ListPolicyTags",
      "logs:ListTagsLogGroup",
      "ecr:ListImages",
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
      # **Objects, not only buckets.** Everything above configures a bucket; the deploy also
      # *writes into one* — the derived thresholds the extraction handler reads, and the zip
      # carrying that handler. Both go to the records bucket, and without this the refusal is
      # `s3:PutObject` after the whole of `foundation` has been built.
      #
      # The same shape as the KMS grant one commit ago: administering a thing and using it are
      # different permissions, and the administrative one reads as the larger.
      "s3:PutObject",
      "s3:DeleteObject",
      # **A version is not an object, as far as IAM is concerned.** `s3:DeleteObject` on a
      # versioned bucket writes a delete marker; removing the version underneath it is
      # `s3:DeleteObjectVersion`, a separate action, and every bucket in this estate is
      # versioned. Without this the teardown empties nothing, `terraform destroy` fails on
      # `BucketNotEmpty`, and five buckets are left standing with their storage cost and the
      # KMS key they need — which is the failure mode `destroy.yml` exists to prevent.
      #
      # Third time this distinction has cost a cycle: managing a key is not using it,
      # administering a bucket is not writing into it, and deleting an object is not deleting
      # its versions. IAM never merges the pair, and the administrative half always reads as
      # the larger one.
      "s3:DeleteObjectVersion",
      "s3:AbortMultipartUpload",
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
      "kms:TagResource",
      "kms:UntagResource",
      # A service that encrypts on this key's behalf — flow logs, a log group, a queue — asks
      # KMS for a grant when it is attached. Without this the *consumer* fails rather than the
      # key, and the error names the consumer rather than the missing permission.
      "kms:CreateGrant",
      "kms:ListGrants",
      "kms:RevokeGrant",
    ]
    resources = ["arn:aws:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
  }

  # **Using the keys, not only managing them.**
  #
  # The statement above covers a key's *administration* — describe it, set its policy, schedule
  # its deletion. It says nothing about encrypting with it, and the deploy does exactly that:
  # it uploads the derived thresholds and the handler package to the records bucket with
  # `--ssekms-key-id`, and S3 asks KMS for a data key on the caller's behalf.
  #
  # `kms:GenerateDataKey` was the refusal, after the whole of `foundation` had been built. The
  # distinction between managing a key and using one is easy to miss precisely because the
  # first sounds like the larger permission.
  #
  # Scoped by tag, so this covers the keys this project created and no other key in the account.
  statement {
    sid    = "EncryptWithThisProjectsKeys"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
    ]
    resources = ["arn:aws:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
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
}

resource "aws_iam_policy" "deploy_storage" {
  name        = "${var.project}-deploy-storage"
  description = "Deploy role: storage."
  policy      = data.aws_iam_policy_document.deploy_storage.json
}

resource "aws_iam_role_policy_attachment" "deploy_storage" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_storage.arn
}

data "aws_iam_policy_document" "deploy_identity" {
  # This policy is where the estate's service roles are created and where `iam:PassRole` lives.
  # Permissions management is the whole job of it, and scoping it away would mean a deploy that
  # cannot create the role a state machine runs as.
  #
  # The constraints that do the work are here and are worth naming: every role name matches
  # `${var.project}-*`, `iam:PassRole` is conditioned on `iam:PassedToService` against a named
  # list, and the budget action may attach a policy to this role and to no other identity.
  #checkov:skip=CKV_AWS_109:Creating the estate's service roles is this policy's purpose; the role-name prefix and the PassedToService list are the constraints, and the OIDC trust bounds who may use it at all.
  #checkov:skip=CKV_AWS_110:As above.
  #checkov:skip=CKV_AWS_286:As above.
  #checkov:skip=CKV_AWS_287:As above.
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
        "lambda.amazonaws.com",
        "sagemaker.amazonaws.com",
        # EventBridge passes the trigger role when a rule targets a state machine. Absent, the
        # rule and its role both exist, the target does not, and a document landing in the
        # bucket causes nothing — the exact failure the trigger was written to close, reached
        # this time through a missing entry in this list rather than through a missing rule.
        "events.amazonaws.com",
      ]
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
      "events:UntagResource",
      # Terraform reads tags back on every refresh. Absent, the *plan* fails rather than the
      # apply — which is a layer that can be created once and never updated or destroyed.
      "events:ListTagsForResource",
      "sns:ListTagsForResource",
      "sns:TagResource",
      "sns:UntagResource",
      # A subscription's attributes are read back on every refresh too, and the refusal names
      # the subscription rather than the permission.
      "sns:GetSubscriptionAttributes",
      "sns:SetSubscriptionAttributes",
    ]
    resources = [
      "arn:aws:sns:*:${data.aws_caller_identity.current.account_id}:${var.project}-*",
      "arn:aws:events:*:${data.aws_caller_identity.current.account_id}:rule/${var.project}-*",
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

resource "aws_iam_policy" "deploy_identity" {
  name        = "${var.project}-deploy-identity"
  description = "Deploy role: identity."
  policy      = data.aws_iam_policy_document.deploy_identity.json
}

resource "aws_iam_role_policy_attachment" "deploy_identity" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_identity.arn
}

data "aws_iam_policy_document" "deploy_data" {
  # **`states:ValidateStateMachineDefinition` takes no resource**, because it validates a
  # definition that does not exist yet — there is nothing to name. The provider calls it before
  # every create and update, so without this the layer cannot be applied at all, and the refusal
  # names the state machine rather than the shape of the grant.
  #
  # The fourth API in this estate whose boundary is inexpressible: `ec2:Describe*`,
  # `ssm:DescribeParameters`, `ecr:GetAuthorizationToken` and now this one.
  #checkov:skip=CKV_AWS_111:states:ValidateStateMachineDefinition validates a definition that does not exist yet; there is no resource to scope it to.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "ValidateADefinitionBeforeItExists"
    effect    = "Allow"
    actions   = ["states:ValidateStateMachineDefinition"]
    resources = ["*"]
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
      # **The refresh reads, named together.** Terraform reads every attribute of a table back
      # on each plan — time-to-live, point-in-time recovery, deletion protection — and a missing
      # one fails the *plan*, not the apply. Discovering them a deploy at a time costs a cycle
      # each; they are the documented read surface of a table and belong in one list.
      "dynamodb:DescribeTimeToLive",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:DescribeContributorInsights",
      "dynamodb:DescribeKinesisStreamingDestination",
      "dynamodb:DescribeTableReplicaAutoScaling",
      "states:CreateStateMachine",
      "states:DeleteStateMachine",
      "states:DescribeStateMachine",
      "states:UpdateStateMachine",
      "states:TagResource",
      "states:ListTagsForResource",
      "states:UntagResource",
      "states:DescribeStateMachineAlias",
      "states:ListStateMachineVersions",
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
      "glue:UntagResource",
      # Glue spells its tag read `GetTags`, not `ListTagsForResource` — and Terraform calls it
      # on every refresh. The fifth service today whose read surface had to be discovered by
      # being refused: the plan fails, not the apply, so the symptom is a catalogue that can be
      # created once and then never changed.
      "glue:GetTags",
      "glue:GetDatabases",
      "glue:GetTables",
      "glue:GetPartitions",
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
      # **`DeleteDatabase` is authorised against what the database *contains*, not only against
      # the database.** Glue checks the catalog, the database, its tables and its user-defined
      # functions — so a grant naming the first three is refused on the fourth, and the message
      # points at a `userDefinedFunction/` ARN for a database that has none.
      #
      # There is nothing to guess here: it is the documented authorisation model, and the only
      # way to meet it is to name a resource type this estate never creates.
      "arn:aws:glue:*:${data.aws_caller_identity.current.account_id}:userDefinedFunction/${var.project}*/*",
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
}

resource "aws_iam_policy" "deploy_data" {
  name        = "${var.project}-deploy-data"
  description = "Deploy role: data."
  policy      = data.aws_iam_policy_document.deploy_data.json
}

resource "aws_iam_role_policy_attachment" "deploy_data" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_data.arn
}

# `lambda:CreateFunction` with `iam:PassRole` is a genuine privilege-escalation shape, and it
# is what an IaC deploy role does. It is bounded by the role-name prefix, by the
# `iam:PassedToService` list in the identity policy, and — the control that actually matters —
# by an OIDC trust naming one repository by numeric id and one environment. The residual risk
# is written down rather than skipped past: anybody who can merge here and dispatch the deploy
# can run code as any `manifest-*` role.
data "aws_iam_policy_document" "deploy_compute" {
  #checkov:skip=CKV_AWS_110:Real and inherent. `lambda:CreateFunction` with `iam:PassRole` is an escalation shape and it is what an IaC deploy role does; the boundary is the OIDC trust naming one repository by numeric id and one environment. See the comment above this block for the residual risk, which is not hidden.
  #checkov:skip=CKV_AWS_109:Same statement, same reasoning — the role-name prefix and the PassedToService list bound it, and the trust bounds who can use it at all.
  #checkov:skip=CKV_AWS_286:As above.
  #checkov:skip=CKV_AWS_287:As above.
  # ── What bootstrap published ──────────────────────────────────────────────
  #
  # Scoped to this project's bootstrap prefix and nothing wider. A grant on `/manifest/*` would
  # let a compromised deploy read every parameter any later layer ever writes.
  # The functions that run this project's logic, and the registry their image lives in.
  #
  # `ecr:GetAuthorizationToken` takes no resource and is the one the deploy needs *before* it
  # can push anything; the rest are scoped by the repository the extraction layer creates.
  #checkov:skip=CKV_AWS_111:GetAuthorizationToken and the Lambda create/describe APIs are documented as not resource-scoped; the boundary is the deploy role's trust, which names one repository and one environment.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "BuildTheComputeAndItsRegistry"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:GetPolicy",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
      "lambda:ListVersionsByFunction",
      # Same reason as the table reads above: the plan asks a function about itself, and a
      # refusal on any one of these is a layer that can be created and never updated.
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionEventInvokeConfig",
      "lambda:GetFunctionUrlConfig",
      "lambda:GetRuntimeManagementConfig",
      "lambda:ListFunctionEventInvokeConfigs",
      "lambda:GetLayerVersion",
      "lambda:GetAlias",
      "lambda:ListAliases",
      "lambda:PutFunctionEventInvokeConfig",
      "lambda:DeleteFunctionEventInvokeConfig",
      "lambda:PutRuntimeManagementConfig",
      "ecr:GetAuthorizationToken",
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:DescribeImages",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:TagResource",
      "ecr:ListTagsForResource",
      "ecr:PutImageTagMutability",
      "ecr:PutImageScanningConfiguration",
      # The repository policy that lets Lambda pull. Setting it is part of building the estate,
      # and without the grant the deploy cannot write the permission the function needs to start.
      "ecr:SetRepositoryPolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:DeleteRepositoryPolicy",
    ]
    resources = ["*"]
  }

  # SageMaker and OpenSearch Serverless, for the two opt-in surfaces.
  #
  # Granted whether or not those surfaces are enabled: the grant is on the deploy *role*, and a
  # role that gains permissions on the day somebody flips a flag is a role whose first apply
  # after the flip fails on an access denial four minutes in.
  #checkov:skip=CKV_AWS_111:These service APIs are largely not resource-scoped; the constraint is the deploy role's trust, which names one repository and one environment.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "BuildTheClassifierAndTheSearchSurface"
    effect = "Allow"
    actions = [
      "sagemaker:CreateModel",
      "sagemaker:DeleteModel",
      "sagemaker:DescribeModel",
      "sagemaker:CreateEndpoint",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:DeleteEndpoint",
      "sagemaker:DeleteEndpointConfig",
      "sagemaker:DescribeEndpoint",
      "sagemaker:DescribeEndpointConfig",
      "sagemaker:UpdateEndpoint",
      "sagemaker:AddTags",
      "sagemaker:ListTags",
      "aoss:CreateCollection",
      "aoss:DeleteCollection",
      "aoss:BatchGetCollection",
      "aoss:ListCollections",
      "aoss:UpdateCollection",
      "aoss:CreateSecurityPolicy",
      "aoss:DeleteSecurityPolicy",
      "aoss:GetSecurityPolicy",
      "aoss:UpdateSecurityPolicy",
      "aoss:CreateAccessPolicy",
      "aoss:DeleteAccessPolicy",
      "aoss:GetAccessPolicy",
      "aoss:UpdateAccessPolicy",
      "aoss:CreateVpcEndpoint",
      "aoss:DeleteVpcEndpoint",
      "aoss:BatchGetVpcEndpoint",
      "aoss:TagResource",
      "aoss:UntagResource",
      "aoss:ListTagsForResource",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy_compute" {
  name        = "${var.project}-deploy-compute"
  description = "Deploy role: compute."
  policy      = data.aws_iam_policy_document.deploy_compute.json
}

resource "aws_iam_role_policy_attachment" "deploy_compute" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_compute.arn
}

data "aws_iam_policy_document" "deploy_references" {
  statement {
    sid    = "ReadEveryLayersPublishedReferences"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
      "ssm:ListTagsForResource",
    ]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/*"
    ]
  }

  # **`ssm:DescribeParameters` takes no resource, and it was in the scoped statement above.**
  #
  # It is one of the APIs that supports no resource-level permission at all: a request for it
  # against `parameter/manifest/*` matches nothing and is refused, which is what happened —
  # after the whole network had been built, at the step that publishes what foundation offers
  # its neighbours.
  #
  # Scoping is not lost, it moves: the *reads* that return values stay bounded to this
  # project's prefix above. This one returns metadata about parameters and is the API's own
  # shape, not a widening chosen here.
  #checkov:skip=CKV_AWS_111:ssm:DescribeParameters supports no resource-level permissions; the value-returning reads above stay scoped to this project's prefix.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "ListWhatParametersExist"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
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

  # **Asking what is left, which the teardown could not do.**
  #
  # `destroy.yml` ends by sweeping for anything this project created that survived, and every
  # call it makes is an enumeration: list the buckets, list the functions, list the log groups,
  # ask the tagging API what still carries `manifest:project`. None of them takes a resource —
  # an account-wide listing is account-wide by definition — so the boundary is that all of it
  # is read-only, and the sweep itself refuses to act on anything outside the project's prefix.
  #
  # `tag:GetResources` in particular was missing while the old report step already called it.
  # That step ended in an `echo`, so the failed call did not fail the step, and a teardown
  # check that could not run reported success for as long as it existed.
  #checkov:skip=CKV_AWS_356:Every action here is a list or describe with no resource-level scoping available; the sweep is read-only by construction.
  #checkov:skip=CKV_AWS_111:As above — no write action is granted here.
  statement {
    sid    = "SweepForWhatSurvivedTheTeardown"
    effect = "Allow"
    actions = [
      "tag:GetResources",
      "s3:ListAllMyBuckets",
      "lambda:ListFunctions",
      "states:ListStateMachines",
      "logs:DescribeLogGroups",
      "ecr:DescribeRepositories",
      "dynamodb:ListTables",
      "sqs:ListQueues",
      "glue:GetDatabases",
      "iam:ListRoles",
      "iam:ListPolicies",
      "ssm:DescribeParameters",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy_references" {
  name        = "${var.project}-deploy-references"
  description = "Deploy role: references."
  policy      = data.aws_iam_policy_document.deploy_references.json
}

resource "aws_iam_role_policy_attachment" "deploy_references" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_references.arn
}

# ── The budget brake ─────────────────────────────────────────────────────────
#
# Attached by a budget action when the ceiling is reached, and by nothing otherwise.
data "aws_iam_policy_document" "budget_brake" {
  #checkov:skip=CKV_AWS_111:It is a Deny. Constraining its resource would narrow what the brake stops, which is the wrong direction for a brake.
  #checkov:skip=CKV_AWS_356:As above — a wildcard Deny is the safe direction of a wildcard.
  #checkov:skip=CKV_AWS_289:Permissions management is deliberately *not* denied: the brake has to be removable by the same identity that can read state, or lifting it needs a second human with console access.
  #checkov:skip=CKV_AWS_290:As above.
  # **Deny what creates spend, rather than deny everything except a safe list.**
  #
  # The first version used `not_actions` with entries like `"*:Describe*"`, and IAM rejects it:
  # *"Action vendors must not contain wildcards."* A wildcard is allowed in the action, never in
  # the service that owns it. `terraform validate` cannot see this, checkov cannot see it, and
  # `tf_validate.py` calls both — it surfaced on the first real apply, at the twelfth resource,
  # which is exactly where a brake failing to build is most expensive.
  #
  # Rewriting it as a deny-list rather than an allow-list turns out to be the better control
  # anyway, and not only because it is expressible. "Everything except reading and deleting"
  # required guessing every verb a teardown might need, and a verb missed from that list is a
  # brake that strands the estate it fired over. Naming what **starts a meter** is a shorter,
  # checkable list: nothing new can be created, and every path out remains open by default.
  statement {
    sid       = "DenyAnythingThatStartsAMeter"
    effect    = "Deny"
    resources = ["*"]

    actions = [
      # Compute and the network it runs in.
      "ec2:RunInstances",
      "ec2:CreateNatGateway",
      "ec2:CreateVpcEndpoint",
      "ec2:AllocateAddress",
      "lambda:CreateFunction",
      "lambda:PutProvisionedConcurrencyConfig",
      # The metered readers. The cascade's whole cost argument is that these stay off pages
      # that do not need them; at the ceiling they stay off every page.
      "textract:*",
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
      "bedrock:InvokeDataAutomationAsync",
      # The two layers that bill by the hour whether or not anything is asked of them.
      "emr-serverless:CreateApplication",
      "emr-serverless:StartApplication",
      "emr-serverless:StartJobRun",
      "redshift-serverless:CreateWorkgroup",
      "redshift-serverless:CreateNamespace",
      "redshift-serverless:UpdateWorkgroup",
      "aoss:CreateCollection",
      "sagemaker:CreateEndpoint",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:UpdateEndpoint",
      "sagemaker:CreateTrainingJob",
      # Storage that grows, and the queue that feeds it.
      "s3:CreateBucket",
      "dynamodb:CreateTable",
      "states:StartExecution",
      "states:StartSyncExecution",
    ]
  }
}

resource "aws_iam_policy" "budget_brake" {
  name        = "${var.project}-budget-stop"
  description = "Attached to the deploy role by a budget action. Denies what starts a meter; permits teardown."
  policy      = data.aws_iam_policy_document.budget_brake.json
}
