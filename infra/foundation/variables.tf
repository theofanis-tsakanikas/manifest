variable "project" {
  description = "Prefix for every resource name."
  type        = string
  default     = "manifest"
}

variable "aws_region" {
  description = "Region for the estate."
  type        = string
  default     = "eu-central-1"
}

variable "expires_at" {
  description = <<-EOT
    ISO date after which the reaper may destroy this layer.

    Required, with no default that means "never". An estate whose expiry is optional is an
    estate that outlives the reason it was created — and the reaper is the only thing standing
    between a portfolio piece and a monthly bill.
  EOT
  type        = string

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.expires_at))
    error_message = "Give an ISO date (YYYY-MM-DD). 'never' is not an expiry."
  }
}

variable "budget_notification_email" {
  description = "Where the budget alarm goes. A guard nobody is told about is a guard that fires into a log."
  type        = string
}

variable "retention_days" {
  description = <<-EOT
    How long derived records are kept in the working zones.

    NOT the customs retention period. UCC Art. 51 sets a floor of three years from the end of
    the relevant year and national law may extend it (`docs/REGULATORY.md`), and that
    obligation attaches to the *record*, not to a working copy of a raster. Conflating the two
    would either delete a record early or keep every page image for years.
  EOT
  type        = number
  default     = 30
}

variable "vpc_cidr" {
  description = "The estate's address space. Private subnets only; nothing here is reachable from the internet."
  type        = string
  default     = "10.42.0.0/16"
}


# **Off by default, and the default is the point.**
#
# The four interface endpoints for Textract and the Bedrock services cost $0.132/hour together
# — three network interfaces each, one per availability zone, charged whether or not a packet
# crosses them. On the first real deploy that was 31% of the estate's hourly bill, for tiers the
# state machine has no state to reach: nothing routes a page upward, and CloudTrail records zero
# Textract calls in the account.
#
# Same shape as `include_expensive_layers` in `deploy.yml`: the expensive thing stands up because
# somebody asked for it, never because a default said so. Turning this on is what a deploy that
# actually intends to escalate does, and that deploy is also the one that would produce the first
# accuracy figure for the escalated fraction — which does not exist today and may not be implied.
variable "enable_escalation_tiers" {
  description = "Stand up the VPC endpoints for Textract and Bedrock. Off: nothing calls them."
  type        = bool
  default     = false
}
