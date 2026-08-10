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

