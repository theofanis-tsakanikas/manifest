output "application_id" {
  description = "The application a plan would be submitted to. This layer was first applied on 2026-08-15 and torn down the same day; no job has been submitted to it, because claim 7's proof is the pure planner and its ledger running on a laptop — the batch layer is an adapter over that planner rather than the thing being proved."
  value       = aws_emrserverless_application.reprocessing.id
}

output "job_role_arn" {
  value = aws_iam_role.job.arn
}
