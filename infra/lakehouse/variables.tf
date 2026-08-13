variable "project" {
  type    = string
  default = "manifest"
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "expires_at" {
  description = "ISO date after which the reaper may destroy this layer."
  type        = string
  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.expires_at))
    error_message = "Give an ISO date (YYYY-MM-DD)."
  }
}

# Fed from `foundation`'s outputs. Never a remote state read across layers.
variable "data_key_arn" { type = string }
variable "logs_key_arn" { type = string }
variable "records_bucket" { type = string }

variable "access_logs_bucket" {
  description = "The foundation layer's access-log bucket."
  type        = string
}

variable "enable_search" {
  description = <<-EOT
    Stand up the document search surface. **Off by default, and the default is the point.**

    OpenSearch Serverless bills an always-on 2-OCU floor whether anybody searches or not
    (`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09) — a constant, not a per-use cost. Decision 5
    says this surface is useful and is not a claim, and an estate that stood it up because a
    deploy ran would have its bill decided by a default rather than by a person.
  EOT
  type        = bool
  default     = false
}

variable "search_principals" {
  description = <<-EOT
    IAM principal ARNs allowed to read and write the index. Never `*`.

    A data-access policy is the only thing between an index of published customs records and
    every principal in the account. Empty by default so that enabling search without naming
    anybody produces a collection nobody can reach — which is the safe direction to get wrong.
  EOT
  type        = list(string)
  # **Constructed, not transcribed, and not a remote state read.**
  #
  # The indexer's role is created in `infra/extraction`, which applies *after* this layer — so a
  # reference would be circular and a state read would make one layer's internals another's
  # contract. The role's name is a constant this project owns, exactly like the deploy role ARN
  # the workflow builds, so the ARN is assembled from the account id and that name.
  #
  # Still never `*`: an empty list would produce a collection nobody can reach, and a wildcard
  # would put an index of published customs records in front of every principal in the account.
  default = null

  validation {
    condition     = alltrue([for arn in var.search_principals : !strcontains(arn, "*")])
    error_message = "Name principals explicitly; a wildcard here grants the whole account."
  }
}

variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "endpoint_security_group_id" { type = string }
