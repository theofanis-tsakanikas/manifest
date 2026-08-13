# What this layer offers its neighbours — the lake itself, and the catalog that describes it.
#
# `infra/batch` writes to the lake and `infra/analytics` reads from it, so both need the bucket
# name. Published rather than recomputed: the name is derived from the account id in one place,
# and a value two layers can compute is a value that will be computed differently in one of them.

locals {
  # **Absent when search is off, not empty.** This published `search_endpoint = ""` on the
  # argument that the extraction layer's own `count` decides whether anything uses it. Two things
  # were wrong with that: `one([])` is `null` rather than `""`, and an SSM parameter takes neither
  # — *"one of insecure_value, value, value_wo must be specified"*, at apply, on every deploy
  # with the flag off. The resolver in `deploy.yml` reads a path and sets what it finds, so a
  # parameter that is not there is a variable that keeps its default, which is exactly the
  # meaning wanted.
  published = merge(
    {
      lake_bucket      = aws_s3_bucket.lake.id
      glue_database    = aws_glue_catalog_database.records.name
      athena_workgroup = aws_athena_workgroup.analysis.name
    },
    var.enable_search ? {
      search_endpoint = one(aws_opensearchserverless_collection.records[*].collection_endpoint)
    } : {},
  )
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:A bucket name, a database name and a workgroup name. Not secrets.
  #checkov:skip=CKV_AWS_337:Same reason — this is a cross-layer reference table, not a secret store.
  for_each = local.published

  name        = "/${var.project}/lakehouse/${each.key}"
  description = "Cross-layer reference published by infra/lakehouse."
  type        = "String"
  value       = each.value
}
