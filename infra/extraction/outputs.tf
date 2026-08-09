output "review_queue_url" {
  description = "Where a field below its threshold goes. Ours, because A2I closed to new customers on 2026-07-30."
  value       = aws_sqs_queue.review.url
}

output "review_queue_arn" {
  value = aws_sqs_queue.review.arn
}

output "decisions_table" {
  description = "The recorded human decisions claim 5 rests on."
  value       = aws_dynamodb_table.decisions.name
}

output "ledger_table" {
  description = "Claim 7's idempotence, keyed by (document, reader)."
  value       = aws_dynamodb_table.ledger.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.extraction.arn
}
