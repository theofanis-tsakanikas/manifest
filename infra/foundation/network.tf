# A private VPC with no internet gateway and no NAT.
#
# **No NAT gateway, on purpose, and it is a cost decision as much as a security one.** A NAT
# gateway is billed per hour and per gigabyte whether or not anything uses it, and it is the
# single most common way a "small" estate quietly costs more than the compute in it. Every AWS
# service this system talks to is reachable through an interface or gateway endpoint, so the
# subnets stay private and nothing routes out.
#
# The consequence is real and worth stating: a Lambda in these subnets cannot reach the public
# internet at all. That is the intended posture — the only things it needs to talk to are AWS
# services — and any dependency that needs to be fetched at run time is a dependency that
# belongs in the deployment package.

locals {
  # Three availability zones, because two is the minimum for most managed services and three is
  # what stops a single-AZ event from halving capacity. Sliced from the VPC's /16 into /20s,
  # which leaves room for a second tier of subnets without renumbering.
  azs = slice(data.aws_availability_zones.available.names, 0, 3)
}

data "aws_availability_zones" "available" {
  state = "available"
}

# The default security group a VPC is created with permits all traffic between its members.
# Nothing is ever placed in it here — every resource names a group explicitly — but leaving it
# permissive is leaving a door open for whatever is added next by somebody who did not read
# this file. Emptied rather than deleted, because a VPC's default group cannot be deleted.
resource "aws_default_security_group" "locked" {
  vpc_id = aws_vpc.main.id
  # No ingress and no egress blocks: an empty default group denies everything.
  tags = { Name = "${var.project}-default-locked" }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project}-vpc" }
}

resource "aws_subnet" "private" {
  for_each = { for index, zone in local.azs : zone => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, each.value)

  # Explicit, and false. The default is false too; writing it down is what makes a future
  # change to it a visible diff rather than a provider default nobody re-reads.
  map_public_ip_on_launch = false

  tags = { Name = "${var.project}-private-${each.key}" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  # No routes. Nothing leaves this VPC except through the endpoints below.
  tags = { Name = "${var.project}-private" }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

# ── VPC endpoints ────────────────────────────────────────────────────────────
#
# Gateway endpoints for S3 and DynamoDB (free, route-table based). Interface endpoints for the
# rest (billed per hour per AZ, which is why the list is exactly what the estate uses and not a
# catalogue).
#
# The failure mode this list prevents is the expensive shape: a service whose endpoint is
# missing does not refuse the connection, it waits — and the control plane reports READY while
# nothing runs. `scripts/check_vpc_endpoints.py` in a sibling project exists because of that,
# and the same discipline applies here.

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${var.project}-s3" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${var.project}-dynamodb" }
}

resource "aws_security_group" "endpoints" {
  name        = "${var.project}-endpoints"
  description = "Ingress to the interface endpoints from inside the VPC only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project}-endpoints" }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  security_group_id = aws_security_group.endpoints.id
  description       = "HTTPS from inside the VPC. Nothing outside it can route here at all."
  cidr_ipv4         = aws_vpc.main.cidr_block
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "endpoints_out" {
  security_group_id = aws_security_group.endpoints.id
  description       = "Return traffic inside the VPC. There is no route out of it."
  cidr_ipv4         = aws_vpc.main.cidr_block
  ip_protocol       = "-1"
}

# **A gateway endpoint is not an address inside the VPC, and the rule above does not cover it.**
#
# This cost the first four documents that ever reached the deployed pipeline. Each one hung for
# the full ten-minute Lambda timeout using 106 MB — not slow work, no work at all — and produced
# no log line beyond `START`. The route table sent the packet to the S3 gateway endpoint
# correctly; the security group then dropped it, because S3 answers on a **public** prefix and
# egress was allowed only to `10.42.0.0/16`.
#
# Nothing errors in that arrangement. A dropped packet is not a refusal: there is no RST, no
# 403, no message naming a permission — just a socket that never answers and a function that
# times out. It is the quietest possible failure and the most expensive to diagnose, and only a
# real document could have found it.
#
# The prefix lists are the precise fix. `0.0.0.0/0` would also work and would silently give the
# subnets a general exit — which is the property this network is built to not have.
data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${data.aws_region.current.region}.s3"
}

data "aws_prefix_list" "dynamodb" {
  name = "com.amazonaws.${data.aws_region.current.region}.dynamodb"
}

resource "aws_vpc_security_group_egress_rule" "to_s3" {
  security_group_id = aws_security_group.endpoints.id
  description       = "S3 through its gateway endpoint, which answers on a public prefix."
  prefix_list_id    = data.aws_prefix_list.s3.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "to_dynamodb" {
  security_group_id = aws_security_group.endpoints.id
  description       = "DynamoDB through its gateway endpoint, same reason as S3."
  prefix_list_id    = data.aws_prefix_list.dynamodb.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# Every service the private subnets talk to needs an endpoint, because there is no NAT gateway
# and no internet route: a service that is missing from this list does not fail at deploy time,
# it hangs until its client times out, and the error names a socket rather than a cause.
#
# Changed 2026-08-10, and the reasons are worth keeping:
#
# - **`comprehend` removed.** Nothing calls it any more; entity recognition here is
#   deterministic code, and the service reads neither Greek nor Dutch. An endpoint for a service
#   nothing calls is an hourly charge and a permanent puzzle for the next reader.
# - **`ecr.api`, `ecr.dkr` added.** The tier-0 reader and the provenance gate are container
#   functions in these subnets, and a Lambda that cannot pull its image never starts. This is
#   the classic omission: everything validates, the deploy succeeds, and the first invocation
#   fails with an image-pull error nobody attributes to a missing endpoint.
# - **`bedrock-data-automation` and `bedrock-data-automation-runtime` added**, so the document
#   tier is reachable if it is ever enabled.
resource "aws_vpc_endpoint" "interface" {
  for_each = toset([
    "textract",
    "bedrock-runtime",
    "bedrock-data-automation",
    "bedrock-data-automation-runtime",
    "ecr.api",
    "ecr.dkr",
    "states",
    "sqs",
    "kms",
    "logs",
    "monitoring",
    "secretsmanager",
    "sts",
  ])

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for subnet in aws_subnet.private : subnet.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.project}-${each.key}" }
}

# ── Flow logs ────────────────────────────────────────────────────────────────

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}

resource "aws_cloudwatch_log_group" "flow" {
  # Flow logs are operational telemetry about an estate that is expected to be short-lived, not
  # a customs record. `retention_days` defaults to 30, and the scanner's one-year floor is a
  # rule written for production audit logs — applying it here would keep a year of packet
  # metadata about an estate whose own expiry tag is measured in weeks, at a cost, for nobody.
  # The customs retention obligation attaches to `records/`, which is why that bucket is the
  # one with no expiry at all.
  #checkov:skip=CKV_AWS_338:Flow logs are operational telemetry on a short-lived estate; the customs record lives in the records bucket, which has no expiry.
  name              = "/aws/vpc/${var.project}"
  retention_in_days = var.retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "${var.project}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

data "aws_iam_policy_document" "flow_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs.json
}
