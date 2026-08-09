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

variable "admin_username" {
  description = "The namespace admin. Its password is managed by Secrets Manager — there is no password in this repository and there will not be one."
  type        = string
  default     = "manifest_admin"
}

variable "base_capacity_rpu" {
  description = <<-EOT
    Base capacity in RPUs. **Explicit, because the service default is 128** — thirty-two times
    the minimum, and a workgroup created without setting this is provisioned for a workload
    nothing in this scenario has.

    The floor is 8 rather than 4: `docs/AWS-CONSTRAINTS.md` (read 2026-08-09) records that
    4-RPU workgroups are available only in a documented list of regions that does **not**
    include eu-central-1, which is this estate's default. Moving the analytics layer to
    eu-west-1 would make 4 available; that is a decision with a data-residency consequence and
    it is not taken quietly in a variable default.
  EOT
  type        = number
  default     = 8

  validation {
    condition     = var.base_capacity_rpu == 8 || (var.base_capacity_rpu >= 8 && var.base_capacity_rpu % 8 == 0)
    error_message = "RPUs are 4, or multiples of 8 from 8 upwards. 4 is unavailable in eu-central-1 — see docs/AWS-CONSTRAINTS.md."
  }
}

variable "max_capacity_rpu" {
  description = "Ceiling the workgroup may scale to. The control that bounds a single unbounded scan."
  type        = number
  default     = 32
}

variable "private_subnet_ids" { type = list(string) }
variable "endpoint_security_group_id" { type = string }
variable "lake_bucket" { type = string }
