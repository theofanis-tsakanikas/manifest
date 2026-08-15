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
      # **EMR Serverless validates the caller's network permission, not the job role's.**
      #
      # `CreateApplication` with a `network_configuration` was refused with
      # `ValidationException: Unauthorized to create network interface` — a message about the
      # application, produced by a check on *this* role. The interfaces are made later, by the
      # service, for a job that has not been submitted; what happens at create time is that EMR
      # Serverless refuses to accept a network configuration the caller could not use.
      #
      # It is the same grant Lambda needed one layer along, at a different moment and against a
      # different principal, which is why neither found it for the other: the deploy role builds
      # the network and had never been asked to *attach* anything to it.
      "ec2:CreateNetworkInterface",
      "ec2:DeleteNetworkInterface",
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
      # **The network interfaces Lambda made, which no Terraform in this repository created.**
      #
      # A function attached to a VPC gets ENIs, allocated by the Lambda service. They are not in
      # any state file, no resource block mentions them, and a permission audit driven by the
      # resource list cannot see them — which is exactly how the first teardown got as far as
      # the subnets and then stopped: *deleting Lambda ENI (eni-0a0c…): 403*.
      #
      # The subnet cannot go while they are in it, so a teardown that cannot delete them leaves
      # the whole network standing: two subnets, a security group, the VPC, and every endpoint
      # attached to it. The most expensive thing in the estate, held up by an object nothing
      # declared.
      "ec2:DeleteNetworkInterface",
      "ec2:DetachNetworkInterface",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/manifest:project"
      values   = [var.project]
    }
  }

  # **The endpoint OpenSearch made, which carries no tag because we did not make it.**
  #
  # Same family as the Lambda ENIs above and found the same way. An `aoss` VPC endpoint is
  # created by the OpenSearch service using *this* role's credentials, and OpenSearch Serverless
  # VPC endpoints take no tags at all — so `aws:ResourceTag/manifest:project` is a condition that
  # can never be true for it, and the statement above excludes it permanently rather than
  # temporarily.
  #
  # What that costs is not a failed delete; it is an estate that cannot be torn down. The
  # endpoint holds the subnets, the subnets hold the VPC, and the most expensive thing here would
  # be left standing behind an object nothing in this repository declared and nobody could
  # remove. It also broke *apply*: a previous run left one in `FAILED`, and replacing it needs
  # the delete first.
  #
  # Region rather than tag, because the API offers no better boundary here and a boundary that
  # can never match is not a boundary. Written as its own statement so that what was given up is
  # one paragraph rather than a widened condition on ten actions.
  #checkov:skip=CKV_AWS_111:Scoped by region because a tag condition cannot match a resource the service creates untagged; see the comment above.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "DeleteEndpointsCreatedOnOurBehalf"
    effect    = "Allow"
    actions   = ["ec2:DeleteVpcEndpoints"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
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
        # **The serverless flavour is a different principal, and the list had only the other.**
        #
        # `CreateNamespace` was refused with *"no identity-based policy allows the iam:PassRole
        # action"* — the message a **condition mismatch** produces, not an absent grant, which
        # sends the reader to look for a statement that is already there. Redshift Serverless
        # passes the namespace's role as `redshift-serverless.amazonaws.com`; the provisioned
        # service uses `redshift.amazonaws.com`, and one of them being present made the other
        # look present too.
        #
        # Third time a service's *spelling* has cost a cycle here: Glue reads tags as `GetTags`,
        # EMR Serverless validates the caller's network permission rather than the job role's,
        # and now this. The pattern is that a permission list is written from the console's
        # vocabulary and enforced against the API's.
        "redshift-serverless.amazonaws.com",
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
      # **Reading the decisions, for the same reason the role holds `athena:StartQueryExecution`
      # below.** The deploy applies the analytics layer and then *loads* it, and the load now
      # joins each abstention to the human decision recorded against it — so the principal that
      # creates the table is also the one that reads it. A read, and only a read: nothing in a
      # deploy may write a decision, because a decision written by a pipeline is doctrine rule 5
      # with a service principal holding the pen.
      "dynamodb:Scan",
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

  # **The lineage SageMaker writes for an endpoint and removes for nobody.** Deleting the
  # endpoint does not remove it, Terraform does not manage it, and it accumulates one set per
  # deployment. `scripts/sagemaker_lineage.py` removes it by name prefix during the teardown.
  #
  # **These four verbs were already in this file and granted nothing.** They sat inside
  # `TheQueueAndTheRecords` above, whose resources name SQS queues, DynamoDB tables and state
  # machines — so a `sagemaker:` action there could match no resource at all. The teardown of
  # 2026-08-15 tore down all five layers, reported success, and left thirty-three lineage
  # entities standing; the estate sweep is what refused. `scripts/check_policy_actions_can_match.py`
  # now fails the build on an action whose statement cannot reach it, because the permission was
  # spelled correctly, reviewed, and inert — and nothing in the estate could tell.
  #
  # Split in two because the two halves scope differently, and collapsing them into one `["*"]`
  # would be the easy version of this fix and a worse one.
  #checkov:skip=CKV_AWS_356:`ListActions` and `ListContexts` are account-level enumerations with no resource-level scoping in IAM; the delete half below is prefix-scoped, which is where the constraint belongs.
  statement {
    sid    = "FindTheLineageBeforeNamingIt"
    effect = "Allow"
    actions = [
      "sagemaker:ListActions",
      "sagemaker:ListContexts",
      # Lineage is a graph, and a node with edges refuses to delete: `DeleteAction` on an
      # associated entity answers `ValidationException: Cannot delete entity with associations`.
      # The edges have to be enumerated before they can be cut, and `ListAssociations` filters by
      # the ARN at one end rather than being scoped by IAM to it.
      "sagemaker:ListAssociations",
    ]
    resources = ["*"]
  }

  # **Cutting an edge, where IAM cannot express what the script can.** An association joins two
  # entities, and the far end of ours is usually an *artifact* — keyed by the S3 URI of a model
  # artefact, carrying no name this project chose. So the grant has to reach `artifact/*`, and
  # the constraint that it only ever touches edges incident to a `manifest-*` action or context
  # lives in `scripts/sagemaker_lineage.py`, which enumerates from our entities outward.
  #
  # Stated plainly rather than folded into the statement above, because it is the one grant here
  # whose scoping is weaker than its sid would suggest, and a reader deserves to see that rather
  # than discover it. Deleting an association removes an edge and never what is on the end of it:
  # no artifact is deleted by anything in this file, which is why the reach is acceptable.
  #checkov:skip=CKV_AWS_111:Constrained to association edges by the caller; the artifact end of an edge carries no name IAM can pattern-match.
  statement {
    sid    = "CutTheEdgesTouchingThisProjectsLineage"
    effect = "Allow"
    actions = [
      "sagemaker:DeleteAssociation",
    ]
    resources = [
      "arn:aws:sagemaker:*:${data.aws_caller_identity.current.account_id}:action/${var.project}-*",
      "arn:aws:sagemaker:*:${data.aws_caller_identity.current.account_id}:context/${var.project}-*",
      "arn:aws:sagemaker:*:${data.aws_caller_identity.current.account_id}:artifact/*",
    ]
  }

  # The delete half, scoped by name to this project. An account holding a sibling project's
  # lineage comes out of this teardown untouched, and that is enforced here rather than trusted
  # to the script — `sagemaker_lineage.py` filters by prefix, and this makes the filter binding.
  statement {
    sid    = "DeleteOnlyTheLineageThisProjectCaused"
    effect = "Allow"
    actions = [
      "sagemaker:DeleteAction",
      "sagemaker:DeleteContext",
    ]
    resources = [
      "arn:aws:sagemaker:*:${data.aws_caller_identity.current.account_id}:action/${var.project}-*",
      "arn:aws:sagemaker:*:${data.aws_caller_identity.current.account_id}:context/${var.project}-*",
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
      # **Running a query, not only creating the workgroup that runs them.** The deploy builds
      # the analytics layer and then *loads* it — `scripts/load_warehouse.py` reads the lake
      # through Athena and writes the rows into Redshift — so the role that applies the layer is
      # also the principal that asks the question. It held every workgroup verb and none of the
      # query ones, and the loader failed on `athena:StartQueryExecution` after the schema had
      # been created and all four marts had run against it.
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
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

  # **Lake Formation, which this account enforces because a sibling project turned it on.**
  #
  # `CreateDatabaseDefaultPermissions` and `CreateTableDefaultPermissions` are both `[]` rather
  # than `IAM_ALLOWED_PRINCIPALS`, and the data lake admins are `watermark-deploy` and the
  # author's own user. Neither was set by this repository. The consequence is that IAM alone
  # does not open the catalogue: the landing function held every `glue:` action it needed and
  # Athena still answered `Access Denied when accessing database manifest_records, table
  # document_version`.
  #
  # This role created the database, so Lake Formation gave it `ALL` **with grant option** — it
  # can pass what it holds to the function it also creates. What it could not do is *issue* the
  # grant, because holding a permission in Lake Formation and being allowed to call
  # `GrantPermissions` are, as ever, two different facts.
  #
  # Recorded as a decision rather than a fix: an estate does not exist in isolation, and this is
  # the second time an account-level setting from a neighbouring project has changed what this
  # one has to declare. The first was `create_oidc_provider = false`.
  statement {
    sid    = "GrantTheCatalogueToWhatThisEstateBuilds"
    effect = "Allow"
    actions = [
      "lakeformation:GrantPermissions",
      "lakeformation:RevokePermissions",
      "lakeformation:ListPermissions",
      "lakeformation:GetDataLakeSettings",
      "lakeformation:GetResourceLFTags",
    ]
    resources = ["*"]
  }

  # **The namespace's admin secret, which Redshift creates and Terraform then reads back.**
  #
  # `manage_admin_password` means the service mints the credential rather than the configuration
  # carrying one — which is the point of it, and the reason no password appears anywhere in this
  # repository. The provider still describes the secret to record its ARN in state, and the
  # teardown still deletes it, so the deploy role needs to see and remove a secret it never
  # writes the contents of.
  #
  # Scoped by name to what Redshift calls it: `redshift!<namespace>-*`. A grant on every secret
  # in the account would put this role one bug away from reading credentials belonging to
  # everything else in it.
  statement {
    sid    = "TheNamespaceCredentialRedshiftMints"
    effect = "Allow"
    actions = [
      # **`CreateSecret` was the one missing, and its absence read as the service's fault.**
      #
      # Redshift mints the credential *on the caller's behalf*, so the permission is checked
      # against this role — and the error AWS returns is `Unable to create namespace credential
      # secret: Amazon Redshift can't access the secret`, which names Redshift. The first fix
      # attempt granted five actions around the secret and not the one that makes it, and the
      # message afterwards was character for character the same.
      # **The one that finds the others.** Every action below names the secret by ARN pattern,
      # and `ListSecrets` names nothing — so it was not granted, and the deploy step that has to
      # *discover* the ARN Redshift chose failed on it: `not authorized to perform:
      # secretsmanager:ListSecrets`. A resource-scoped grant list is complete for every call
      # that already knows the resource.
      "secretsmanager:CreateSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:ListSecretVersionIds",
      "secretsmanager:TagResource",
      "secretsmanager:DeleteSecret",
      # Rotation is configured by Redshift when it manages the password. Not granting it leaves
      # a namespace whose credential can never be rotated, which is a security control missing
      # rather than a deploy that fails — the quieter of the two.
      "secretsmanager:RotateSecret",
      # **`GetSecretValue` was deliberately absent, and the reasoning held until the warehouse
      # needed a schema.**
      #
      # The sentence here read: *"a deploy role that could read it would put the password one
      # compromised workflow away, in exchange for nothing the apply needs"*. That was true of an
      # apply that only created a workgroup. It is false of one that has to create tables in it:
      # Redshift maps an IAM caller to a database user with no privileges on `dev`, and
      # `analytics/schema.sql` came back `permission denied for database dev`. DDL needs a
      # privileged session and the only privileged credential is the one Redshift minted.
      #
      # So the trade is taken and stated rather than reversed quietly. What is bought is a
      # warehouse whose schema is created by the deploy that creates the warehouse; the
      # alternative is a human running DDL by hand, which is undocumented state in the one layer
      # whose whole argument is that its contents are declared. What is given up is real: this
      # role can read the warehouse admin password.
      #
      # Narrowed as far as the API allows — one secret, named by the pattern Redshift chooses —
      # and it stays out of `manifest-deploy-references`, where every other read lives, so that
      # `grep GetSecretValue` finds it here with this paragraph attached.
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:redshift!${var.project}-*",
    ]
  }

  # **Running the schema against the warehouse, which is a different service from creating it.**
  #
  # `redshift-serverless:*` builds the workgroup; `redshift-data:*` talks to it. The analytics
  # job creates the schema and then runs every mart against it, and none of that is reachable
  # through the resource-management API.
  #
  # Missed because it was probed with the wrong principal — by hand, as a user that already held
  # these, which answers a different question from the one being asked. The same mistake as
  # running an Athena `INSERT` as myself and reading a schema error where the truth was
  # reachability, and it is the standing hazard of probing: the probe must run as the identity
  # that will run in earnest.
  statement {
    sid    = "RunStatementsAgainstTheWarehouse"
    effect = "Allow"
    actions = [
      "redshift-data:BatchExecuteStatement",
      "redshift-data:ExecuteStatement",
      "redshift-data:DescribeStatement",
      "redshift-data:GetStatementResult",
      "redshift-data:CancelStatement",
    ]
    resources = ["arn:aws:redshift-serverless:*:${data.aws_caller_identity.current.account_id}:workgroup/*"]
  }

  # `DescribeStatement` and `GetStatementResult` are addressed by statement id rather than by
  # workgroup, so they take no resource this role can name. Read-only over statements this role
  # itself submitted.
  #checkov:skip=CKV_AWS_111:Statement reads are addressed by statement id and support no resource-level scoping.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "ReadBackTheStatementsItSubmitted"
    effect    = "Allow"
    actions   = ["redshift-data:DescribeStatement", "redshift-data:GetStatementResult"]
    resources = ["*"]
  }

  # **The call that finds the secret the calls below name.**
  #
  # Every action in the statement above is scoped to `redshift!manifest-*`, which is complete for
  # any call that already knows the ARN — and the deploy step has to *discover* it, because
  # Redshift chooses the suffix. `ListSecrets` takes no resource at all, so it could not go in
  # that statement, and its absence surfaced as `not authorized to perform:
  # secretsmanager:ListSecrets` after the workgroup had already been built.
  #
  # A list is a read over names, not over contents: this grants seeing that a secret exists, and
  # `GetSecretValue` stays scoped to the one this estate creates.
  #checkov:skip=CKV_AWS_111:ListSecrets supports no resource-level scoping; it returns names and no secret material.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid       = "FindTheSecretRedshiftNamed"
    effect    = "Allow"
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"]
  }

  # **Reading the inference profile, so the escalation's policy can name what it routes to.**
  #
  # `Converse` against a cross-Region profile is authorised on the profile *and* on the
  # foundation model in whichever Region Bedrock picks — the first Greek page was refused on
  # `eu-north-1`, a Region this estate does not deploy into and never names. `infra/extraction`
  # derives those ARNs from the profile rather than transcribing six of them, and this is the
  # read that makes the derivation possible.
  #
  # A read, and only a read. `bedrock:InvokeModel` is not here and must not be: the deploy role
  # builds the thing that calls the model, it does not call it.
  statement {
    sid       = "ReadTheInferenceProfileTheEscalationRoutesThrough"
    effect    = "Allow"
    actions   = ["bedrock:GetInferenceProfile", "bedrock:ListInferenceProfiles"]
    resources = ["*"]
  }

  # **The data-automation project's whole lifecycle, on the role rather than in a state file.**
  #
  # The AWS provider declares no resource for one, so `scripts/bda_project.py` creates it from
  # the deploy and `destroy.yml` deletes it. That is an exception to "IaC only" with its reason
  # written in `infra/extraction/variables.tf`, and this is what it costs: the lifecycle lives in
  # an IAM policy instead of in Terraform state.
  #
  # `List` is here because the script is idempotent **by name** — BDA chooses the identifier, so
  # finding the project that already exists means listing them. Creating a project starts no
  # meter, which is why these are not in the budget brake; `InvokeDataAutomationAsync` is the
  # metered call and that one is already there.
  #checkov:skip=CKV_AWS_111:The project does not exist until this role creates it, so there is no ARN to scope to.
  #checkov:skip=CKV_AWS_356:As above.
  statement {
    sid    = "OwnTheDocumentAutomationProjectTerraformCannot"
    effect = "Allow"
    actions = [
      "bedrock:ListDataAutomationProjects",
      "bedrock:GetDataAutomationProject",
      "bedrock:CreateDataAutomationProject",
      "bedrock:UpdateDataAutomationProject",
      "bedrock:DeleteDataAutomationProject",
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

      # **Route 53, for an OpenSearch collection.** Not an oversight in the grouping: an
      # `aoss` VPC endpoint is a private DNS name, so the service creates a private hosted zone
      # in *this* account and associates it with the VPC — using the caller's credentials, not
      # its own. The endpoint therefore reaches `FAILED` rather than `ACTIVE` with a Route 53
      # 403, roughly seven minutes into an apply, which is where this list came from.
      #
      # The delete half is here for the same reason it is everywhere else in this file: a
      # repository with a create path and no delete path is how an estate gets left standing,
      # and a hosted zone nobody can delete outlives the collection that caused it.
      "route53:CreateHostedZone",
      "route53:DeleteHostedZone",
      "route53:GetHostedZone",
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
      "route53:ListHostedZonesByVPC",
      "route53:AssociateVPCWithHostedZone",
      "route53:DisassociateVPCFromHostedZone",
      "route53:ChangeResourceRecordSets",
      "route53:ListResourceRecordSets",
      "route53:ChangeTagsForResource",
      "route53:ListTagsForResource",
      "route53:GetChange",
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
      # **Found by the check above rather than by a teardown, which is the point of writing it.**
      #
      # The sweep confirms a tagged leftover is really still there before reporting it, and for a
      # queue that confirmation is `GetQueueUrl`. `gone()` treats anything that is not a
      # `NotFound` as *present*, so a refusal here does not hide a survivor — it invents one, and
      # a report with a phantom in it is a report somebody stops reading.
      #
      # It never fired because the estate has always torn its queues down cleanly, so the probe
      # had nothing to probe. A permission that is only reached on the bad day is one no amount of
      # running the good day will find.
      "sqs:GetQueueUrl",
      "glue:GetDatabases",
      "iam:ListRoles",
      "iam:ListPolicies",
      "ssm:DescribeParameters",
      # **The action the first real teardown died on, and the only one it died on.**
      #
      # `_keys_pending_deletion` enumerates the account's keys so that a key already scheduled
      # for deletion is reported as removed rather than as a survivor — KMS enforces a seven to
      # thirty day waiting period and refuses to delete sooner, so without that distinction every
      # teardown ends red for doing exactly what it was told.
      #
      # It was missing because the grants were derived from what the *layers declare* and this
      # call belongs to a script, which is a different list nobody was keeping. All five layer
      # teardowns succeeded and the run still reported failure, which is the worst available
      # combination: the estate was down and the report said otherwise.
      #
      # `kms:DescribeKey` is deliberately NOT widened to match. It stays scoped to keys carrying
      # this project's tag, and the sweep already treats a key it can list and cannot describe as
      # somebody else's — listing every key is a read this account can do about itself, describing
      # every key is a read about everybody.
      "kms:ListKeys",
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


# ── The teardown's own permissions ────────────────────────────────────────────
#
# **Fifteen actions no deploy ever performs, found in three passes and then misplaced once.**
#
# A deploy exercises `Put` and never `Delete`, so a permission set can be complete for every
# apply anybody runs and short for the one teardown that gets the money back. The first real
# teardown found that in three distinct shapes: deletes whose `Put` twin was granted, *reads*
# the provider performs while planning a removal, and resources a service created on our behalf.
#
# They are here rather than appended to the layer statements because that is where the second
# mistake happened: `iam:ListInstanceProfilesForRole` was added to a statement whose resources
# are S3 bucket ARNs, where an IAM action can never match, and `ec2:DeleteNetworkInterface` to
# one conditioned on `manifest:project` — a tag Lambda's own ENIs do not carry. Both were
# granted and both were denied, which is the least useful combination available.
data "aws_iam_policy_document" "deploy_teardown" {
  # IAM reads and deletes, scoped by name. Roles and policies this project creates are named
  # `manifest-*`; nothing else in the account is in reach.
  statement {
    sid    = "RemoveThisProjectsIdentities"
    effect = "Allow"
    actions = [
      "iam:ListInstanceProfilesForRole",
      "iam:ListEntitiesForPolicy",
      "iam:ListRoleTags",
      "iam:ListPolicyTags",
      "iam:DeletePolicyVersion",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-*",
    ]
  }

  # **The network interfaces Lambda made, and why they carry no condition.**
  #
  # A function attached to a VPC gets ENIs allocated by the Lambda service. They are in no state
  # file, no resource block mentions them, and — the part that matters here — **they carry none
  # of this project's tags**, because this project did not create them. A `ResourceTag`
  # condition therefore never matches, and the teardown stops at the subnets with the whole
  # network behind them: two subnets, a security group, the VPC and every endpoint on it.
  #
  # The boundary that remains is the deploy role's trust — `workflow_dispatch` from one
  # environment in one repository — plus the narrowness of the action itself. Deleting a network
  # interface is not a way to reach anything; it is a way to remove one.
  #checkov:skip=CKV_AWS_111:Lambda-created ENIs carry no project tag, so a ResourceTag condition matches nothing and leaves the network undeletable.
  #checkov:skip=CKV_AWS_356:As above — the API offers no resource pattern for an ENI this role can predict.
  statement {
    sid       = "RemoveTheInterfacesLambdaMade"
    effect    = "Allow"
    actions   = ["ec2:DeleteNetworkInterface", "ec2:DetachNetworkInterface"]
    resources = ["*"]
  }

  # An alias has no tags of its own, so it is scoped by name instead of by condition.
  statement {
    sid       = "RemoveThisProjectsAliases"
    effect    = "Allow"
    actions   = ["kms:DeleteAlias", "kms:DisableKey"]
    resources = ["arn:aws:kms:*:${data.aws_caller_identity.current.account_id}:alias/${var.project}-*"]
  }

  statement {
    sid    = "RemoveWhatTheLayersLeave"
    effect = "Allow"
    actions = [
      "ecr:BatchDeleteImage",
      "ecr:ListImages",
      "lambda:DeleteEventSourceMapping",
      "logs:DeleteSubscriptionFilter",
      "logs:ListTagsLogGroup",
    ]
    resources = [
      "arn:aws:ecr:*:${data.aws_caller_identity.current.account_id}:repository/${var.project}-*",
      "arn:aws:lambda:*:${data.aws_caller_identity.current.account_id}:function:${var.project}-*",
      "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:*",
    ]
  }
}

resource "aws_iam_policy" "deploy_teardown" {
  name        = "${var.project}-deploy-teardown"
  description = "Deploy role: the actions only a teardown performs."
  policy      = data.aws_iam_policy_document.deploy_teardown.json
}

resource "aws_iam_role_policy_attachment" "deploy_teardown" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_teardown.arn
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
