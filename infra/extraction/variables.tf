variable "project" {
  type    = string
  default = "manifest"
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "expires_at" {
  description = "ISO date after which the reaper may destroy this layer. No default that means never."
  type        = string
  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.expires_at))
    error_message = "Give an ISO date (YYYY-MM-DD)."
  }
}

# Cross-layer references are variables fed from `foundation`'s outputs, never a remote state
# read. A remote state read makes one layer's internals another layer's contract, and the two
# can then not be applied, destroyed or reasoned about separately.
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "endpoint_security_group_id" { type = string }
variable "data_key_arn" { type = string }
variable "logs_key_arn" { type = string }
variable "landing_bucket" { type = string }
variable "records_bucket" { type = string }
variable "evidence_bucket" { type = string }

variable "review_visibility_seconds" {
  description = <<-EOT
    How long a queued item is invisible after a reviewer takes it.

    Longer than the declared twenty seconds a decision by a wide margin, because a reviewer who
    is interrupted must not have their item handed to somebody else while they are still
    looking at it — two humans deciding the same field is how a queue produces a conflict it
    then has to resolve without either of them knowing.
  EOT
  type        = number
  default     = 900
}

variable "review_max_receives" {
  description = <<-EOT
    How many times an item may be taken before it goes to the dead-letter queue.

    Three. An item that has been picked up three times and never decided is not a hard item; it
    is an item something is wrong with, and leaving it circulating means a reviewer meets it
    again every shift and the queue's depth stops meaning anything.
  EOT
  type        = number
  default     = 3
}
