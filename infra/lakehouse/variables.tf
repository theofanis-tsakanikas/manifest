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
