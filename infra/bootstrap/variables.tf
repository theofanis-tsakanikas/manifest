variable "project" {
  description = "Prefix for every resource name and the value of the manifest:project tag."
  type        = string
  default     = "manifest"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "The project prefix is lower-case letters, digits and hyphens, 3 to 21 characters — it becomes part of an S3 bucket name."
  }
}

variable "aws_region" {
  description = "Region for the state backend and the OIDC role."
  type        = string
  default     = "eu-central-1"
}

variable "github_owner" {
  description = "GitHub account that owns the repository CI runs from."
  type        = string
}

variable "github_repo" {
  description = "Repository name. Together with the owner and an environment this is the whole of what the deploy role trusts."
  type        = string
  default     = "manifest"
}

variable "github_owner_id" {
  description = <<-EOT
    The account's **numeric** id, from `https://api.github.com/users/<owner>`.

    Required, with no default. A repository name can be released and re-registered by somebody
    else; a numeric id cannot, so a trust scoped to names is one whoever claims the name after
    you inherits. This is the value that closes that.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "A numeric id, not the account name."
  }
}

variable "github_repository_id" {
  description = <<-EOT
    The repository's **numeric** id, from `https://api.github.com/repos/<owner>/<repo>`.

    Same reason as the owner id, and the same lack of a default: a value that could be guessed
    wrong and default to something is a trust policy that silently trusts the wrong thing.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "A numeric id, not the repository name."
  }
}

variable "deploy_environments" {
  description = <<-EOT
    GitHub environments allowed to assume the deploy role.

    Every trusted subject names this repository AND one environment, with no wildcard. A
    subject of `repo:owner/repo:*` trusts every branch and every pull request in the
    repository, including one opened by a stranger, which is the difference between OIDC and
    a long-lived key written down in public.

    `destroy` is on this list from the first commit, not added later. A repository with a
    deploy path and no teardown path is how an estate gets left standing, and the identity
    that would tear it down is part of the deploy path, not a follow-up to it.
  EOT
  type        = list(string)
  default     = ["deploy", "destroy"]

  validation {
    condition     = length(var.deploy_environments) > 0 && alltrue([for e in var.deploy_environments : can(regex("^[a-z][a-z0-9-]*$", e))])
    error_message = "Name at least one environment, in lower case, with no wildcard characters."
  }
}

variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub OIDC provider, or reference the one that is already there.

    An account holds at most one IAM OIDC provider per issuer URL, and this portfolio has more
    than one repository that would deploy into an account. Creating a second is not a warning,
    it is `EntityAlreadyExists` partway through an apply, at which point half the layer is up.
    Set this to false where another project got there first.
  EOT
  type        = bool
  default     = true
}

variable "github_oidc_thumbprints" {
  description = <<-EOT
    Certificate thumbprints for the GitHub OIDC endpoint.

    Empty by default. AWS verifies GitHub's OIDC endpoint against its own trust store rather
    than against a thumbprint supplied here, and a pinned thumbprint is a value that rotates
    without telling you and breaks every deploy on the day it does. Set it only if a policy
    requires pinning, and then own the rotation.
  EOT
  type        = list(string)
  default     = []
}

variable "state_retention_days" {
  description = "How long a superseded state version is kept. State history is how a bad apply is understood after the fact."
  type        = number
  default     = 90

  validation {
    condition     = var.state_retention_days >= 30
    error_message = "Keep at least 30 days of state history; a shorter window loses the record of the apply that caused the incident."
  }
}

variable "budget_notification_email" {
  description = <<-EOT
    Where the budget alarm and the expiry notice go.

    Taken here rather than in `foundation` because it is the one input the deploy cannot derive
    from anything, and publishing it as a SecureString is what stops it becoming a fifth
    transcribed repository variable. A guard nobody is told about is a guard that fires into a
    log.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_notification_email))
    error_message = "Give an address the alarm can actually reach."
  }
}

variable "monthly_budget_eur" {
  description = <<-EOT
    The ceiling at which the deploy role loses its ability to create anything.

    A design constraint, not a forecast. `CLAUDE.md` puts the whole-run ceiling under EUR 150
    and that figure is a constraint on what may be built, never a result — nothing here has
    been applied and no euro has been spent.
  EOT
  type        = number
  default     = 150

  validation {
    condition     = var.monthly_budget_eur > 0 && var.monthly_budget_eur <= 500
    error_message = "A budget above 500 is not a guard; it is a formality."
  }
}

variable "escalation_model_arn" {
  description = <<-EOT
    The Bedrock model or inference-profile ARN the cascade's escalation tier may invoke.

    Taken here, with the other value nothing can derive, and published for the deploy to
    resolve. It is a deployment decision — which model, in which Region, under which terms — and
    it is the one grant in the estate that could otherwise be written as `*`.

    `bedrock:InvokeModel` on `*` is permission to invoke every model in the account: different
    prices, different data-handling terms, different regional footprints. Naming one is what
    makes the budget guard a guard rather than a formality.
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:bedrock:", var.escalation_model_arn)) && !strcontains(var.escalation_model_arn, "*")
    error_message = "A Bedrock ARN with no wildcard. A wildcard here grants every model in the account."
  }
}
