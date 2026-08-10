# The document search surface.
#
# **It indexes published records. It never indexes raw document text.** That is the whole design
# and it is not a performance decision: a commercial invoice is a document a counterparty wrote,
# and `src/manifest/security/injection.py` treats its text as data everywhere else in this
# system. An index of raw page text is that same text, retained, ranked and surfaced to a human
# who is about to make a customs decision — with the retention class its contract declares
# quietly dropped on the way in. What goes in here is what has already been published: values
# that cleared a derived threshold and passed the provenance gate.
#
# **It is not a claim.** Decision 5: three projects in this portfolio already prove retrieval,
# so this appears on no scoreboard and nothing here is offered as evidence of anything. It is
# built because an operator with 3,000 documents needs to find one, and that is a real need.
#
# **It is opt-in, and the reason is money.** OpenSearch Serverless has a documented always-on
# floor of 2 OCUs (`docs/AWS-CONSTRAINTS.md`), which bills whether anybody searches or not — a
# constant, not a per-use cost. An estate that stood this up because a deploy ran would have its
# bill decided by a default.

resource "aws_opensearchserverless_security_policy" "encryption" {
  count = var.enable_search ? 1 : 0

  name = "${var.project}-records-encryption"
  type = "encryption"

  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${var.project}-records"]
    }]
    # The estate's own key, not the service-managed one. Same rule as every other store here:
    # the customs record and everything derived from it is encrypted with a key this account
    # owns and can revoke.
    AWSOwnedKey = false
    KmsARN      = var.data_key_arn
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  count = var.enable_search ? 1 : 0

  name = "${var.project}-records-network"
  type = "network"

  # No public access, for the collection or for its dashboard. The subnets have no internet
  # route and there is no NAT, so a public collection would be reachable from the internet and
  # unreachable from the pipeline — which is exactly backwards.
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project}-records"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/${var.project}-records"]
      },
    ]
    AllowFromPublic = false
    SourceVPCEs     = [aws_opensearchserverless_vpc_endpoint.records[0].id]
  }])
}

resource "aws_opensearchserverless_vpc_endpoint" "records" {
  count = var.enable_search ? 1 : 0

  name               = "${var.project}-records"
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [var.endpoint_security_group_id]
}

# Who may read and write the index.
#
# Named principals, never `*`. A data-access policy is the only thing between an index of
# published customs records and every principal in the account, and it is the control most
# likely to be written permissively "for now".
resource "aws_opensearchserverless_access_policy" "records" {
  count = var.enable_search ? 1 : 0

  name = "${var.project}-records-access"
  type = "data"

  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/${var.project}-records/*"]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
          "aoss:UpdateIndex",
        ]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project}-records"]
        Permission   = ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems"]
      },
    ]
    Principal = var.search_principals
  }])
}

resource "aws_opensearchserverless_collection" "records" {
  count = var.enable_search ? 1 : 0

  name        = "${var.project}-records"
  description = "Published records only. Never raw document text — see the comment at the top of this file."
  type        = "SEARCH"

  tags = { "${var.project}:expires-at" = var.expires_at }

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}
