# What this layer offers its neighbours. Same mechanism and same reasoning as
# `infra/foundation/published.tf`: a short, deliberate, enumerable list, published where a later
# layer can resolve it without holding read access to this layer's state.
#
# `infra/batch` needs the ledger, because claim 7's idempotence is a property of that table: a
# reprocessing run that could not see what a previous run recorded would redo the work and call
# it new.

locals {
  published = {
    review_queue_url    = aws_sqs_queue.review.url
    review_queue_arn    = aws_sqs_queue.review.arn
    ledger_table_arn    = aws_dynamodb_table.ledger.arn
    ledger_table_name   = aws_dynamodb_table.ledger.name
    decisions_table_arn = aws_dynamodb_table.decisions.arn
    state_machine_arn   = aws_sfn_state_machine.extraction.arn
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:Queue URLs, table ARNs and a state-machine ARN. None is a secret, and a SecureString would put a KMS grant in every consuming layer to encrypt facts already visible in the console.
  #checkov:skip=CKV_AWS_337:Same reason. Secrets live in Secrets Manager; this is a cross-layer reference table.
  for_each = local.published

  name        = "/${var.project}/extraction/${each.key}"
  description = "Cross-layer reference published by infra/extraction."
  type        = "String"
  value       = each.value
}
