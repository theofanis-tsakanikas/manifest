output "application_id" {
  description = "The application a plan would be submitted to. Nothing has ever been submitted, and this layer has never been applied — `include_expensive_layers` is off by default and has stayed off."
  value       = aws_emrserverless_application.reprocessing.id
}

output "job_role_arn" {
  value = aws_iam_role.job.arn
}
