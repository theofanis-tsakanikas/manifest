output "application_id" {
  description = "The application a plan would be submitted to. Nothing has ever been submitted."
  value       = aws_emrserverless_application.reprocessing.id
}

output "job_role_arn" {
  value = aws_iam_role.job.arn
}
