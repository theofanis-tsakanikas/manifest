# Cross-layer references are outputs → data sources. Never a remote state read across layers:
# that makes one layer's internals another layer's contract, and the two then cannot be
# applied, destroyed or reasoned about separately.

output "state_bucket" {
  description = "Backend bucket for every other layer's state."
  value       = aws_s3_bucket.state.id
}

output "state_kms_key_arn" {
  description = "The key the backend encrypts state with."
  value       = aws_kms_key.state.arn
}

output "deploy_role_arn" {
  description = "Role assumed by GitHub Actions. Goes into the workflows as a repository variable, not a secret — it is an ARN, and a role ARN with no trust for the caller is not a credential."
  value       = aws_iam_role.deploy.arn
}

output "published_parameters" {
  description = <<-EOT
    Where the deploy reads everything else from.

    The deploy needs exactly one repository variable — the account id — and resolves the rest
    from these. See `published.tf` for why: a transcribed value looks like an independent
    setting, and the fix for a mismatch ends up in a settings page rather than in a diff.
  EOT
  value       = [for parameter in aws_ssm_parameter.published : parameter.name]
}

output "backend_configuration" {
  description = "Paste into each layer's backend block. Written out because a hand-typed backend key is how two layers end up sharing one state file."
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.state.id}"
        key          = "<layer>/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        kms_key_id   = "${aws_kms_key.state.arn}"
        use_lockfile = true
      }
    }
  EOT
}
