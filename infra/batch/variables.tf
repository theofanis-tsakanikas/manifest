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

variable "emr_release" {
  description = "The EMR release the application runs. Pinned: a release that moves under a job is a job whose Spark version changed without a diff."
  type        = string
  default     = "emr-7.5.0"
}

variable "max_vcpu" {
  description = "Ceiling on total vCPU. A cap, not a target — it is what stops a runaway plan from being a runaway bill."
  type        = number
  default     = 100
}

variable "max_memory_gb" {
  description = <<-EOT
    Ceiling on total memory.

    Proportional to the vCPU ceiling at the documented ratios: a 4-vCPU worker takes 8 to 30 GB,
    so 100 vCPU of 4-vCPU workers is at most 750 GB. A ceiling that is not proportional caps one
    dimension and leaves the other free, which is not a ceiling.
  EOT
  type        = number
  default     = 750
}

variable "private_subnet_ids" { type = list(string) }
variable "endpoint_security_group_id" { type = string }
variable "lake_bucket" { type = string }
variable "ledger_table_arn" { type = string }

variable "landing_bucket" {
  description = "Where source documents arrive. Listed to map a document id onto the key it came in under."
  type        = string
}

variable "state_machine_arn" {
  description = <<-EOT
    The per-document pipeline each executor starts.

    The job distributes and records; the machine reads, thresholds, gates, publishes and lands.
    A bulk path that re-implemented that sequence would be a second copy of it in the one place
    no offline test reaches.
  EOT
  type        = string
}

variable "job_image_uri" {
  description = <<-EOT
    The custom image the application runs. Empty means the release's own, which carries Python
    3.9 — and `pipelines/reprocess.py` refuses to start on it, by name, because
    `manifest.core.scale` needs 3.12.

    Empty is still allowed rather than refused: an application with no image is a valid thing to
    stand up, and the job it cannot run says so itself, on the driver, before an executor is
    allocated.
  EOT
  type        = string
  default     = ""
}
