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

variable "reader_image_digest" {
  description = <<-EOT
    The **digest** of the reader image the functions run, as `sha256:...`.

    A digest, never a tag, and there is no default. A tag identifies what somebody meant; a
    digest identifies what is actually there. Every confidence threshold in this repository was
    derived from one specific build of the reader binary inside this image, so a deploy that
    moved to a different build without saying so would move every threshold silently — which is
    the exact failure `make ocr-record`'s ceremony exists to make loud at the other end.

    Supplied by the deploy workflow after it builds and pushes.
  EOT
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.reader_image_digest))
    error_message = "A full image digest, `sha256:` followed by 64 hex characters — not a tag."
  }
}

variable "publish_package_key" {
  description = <<-EOT
    Object key of the zip carrying the extraction handler and the contracts.

    An object key rather than a local path, deliberately. `filename` with `filebase64sha256()`
    reads the file at plan time — including during `terraform destroy`, where no zip has been
    built — so a local path makes the estate undeleteable from CI. That is the exact failure the
    teardown path exists to prevent.

    Built by the deploy workflow from the same commit as the image, so code and contracts cannot
    come from different revisions: a deployment where they did would apply one version's
    comparison rules to another version's fields, and nothing would report it.
  EOT
  type        = string
  default     = "artifacts/publish.zip"
}

variable "publish_package_hash" {
  description = <<-EOT
    Base64 SHA-256 of that zip, so an unchanged upload does not force a replacement and a
    changed one does. Empty during a teardown, where it decides nothing.
  EOT
  type        = string
  default     = ""
}

variable "escalation_model_arns" {
  description = <<-EOT
    The model ARNs the escalation tier may invoke. No default, and never `*`.

    `bedrock:InvokeModel` on `*` grants invocation of every model in the account — different
    prices, different data-handling terms, different regional footprints. The cascade calls one
    model, and naming it is what makes the budget guard a guard rather than a formality.

    An inference-profile ARN is the usual value where cross-Region inference is wanted; a
    foundation-model ARN pins the Region as well.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.escalation_model_arns) > 0 && alltrue([for arn in var.escalation_model_arns : can(regex("^arn:aws:bedrock:", arn)) && !strcontains(arn, "*")])
    error_message = "Name at least one Bedrock ARN, and no wildcards: a wildcard here is a grant over every model in the account."
  }
}

variable "document_automation_arns" {
  description = <<-EOT
    The data-automation project ARNs the pipeline may invoke, same reasoning as the models.

    May be empty: the document-automation tier is reachable only by a second escalation, which
    the routing model does not attempt today (`evals/scale/`). An empty list produces a
    statement with no resources, which grants nothing — which is the honest state.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for arn in var.document_automation_arns : can(regex("^arn:aws:bedrock:", arn)) && !strcontains(arn, "*")])
    error_message = "Bedrock ARNs only, and no wildcards."
  }
}
