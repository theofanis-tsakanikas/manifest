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

    **Reached, as of 2026-08-13.** This said the tier "is reachable only by a second escalation,
    which the routing model does not attempt today" — accurate, and the reason was that
    `handlers/escalate.py` called `route` once with `current_tier=0` and stopped. `route` returns
    the *cheapest* tier above the current one, so English always went to tier 1 and tiers 2 and 3
    were reachable in the contract and unreachable in the estate. The handler climbs now, so this
    list is supplied.

    Both the project and the profile go in it: `InvokeDataAutomationAsync` is authorised against
    each. An empty list produces a statement with no resources, which grants nothing — the
    honest state for a deploy with the tiers switched off.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for arn in var.document_automation_arns : can(regex("^arn:aws:bedrock:", arn)) && !strcontains(arn, "*")])
    error_message = "Bedrock ARNs only, and no wildcards."
  }
}

variable "enable_classifier" {
  description = <<-EOT
    Stand up the tariff-classification endpoint. Off by default.

    Serverless inference scales to nothing, so the cost is per request rather than per hour —
    but an endpoint that exists is an endpoint somebody can call, and `hs_code` is declared
    always-review, so every classification it produces lands in the review queue. Standing it up
    is therefore a decision about **queue capacity**, not only about spend, and doctrine rule 1
    says a queue past capacity is a failure of the system rather than of the reviewers.
  EOT
  type        = bool
  default     = false

  # **The flag was unreachable and the feature is also unbuildable, and those are two problems.**
  #
  # `deploy.yml` now passes this — before, no dispatch could set it, so the endpoint was not
  # "off by default", it was unbuildable-by-omission. Making it reachable immediately showed the
  # second problem: with it on, the layer does not plan, because a SageMaker model needs an
  # artefact and `classifier_model_data_url` is empty. **That was true when it was written and
  # is not any more**: `scripts/train_classifier.py` fits one from
  # `contracts/classification/training.yaml` and `deploy.yml` uploads it before this layer
  # applies. The validation stays, because the flag can still be set by a dispatch that did not
  # produce an artefact, and an endpoint over nothing is the failure it was written for.
  #
  # Refused here, by name, rather than four minutes later as a SageMaker API error about a model
  # data URL. An operator who sets this flag is asking for something that does not exist yet, and
  # the message is the place to say what is missing.
  validation {
    condition     = !var.enable_classifier || var.classifier_model_data_url != ""
    error_message = <<-EOT
      enable_classifier is on and classifier_model_data_url is empty. `deploy.yml` fits the
      artefact and uploads it before this layer applies, so an empty value here means that step
      did not run or did not produce one — most likely because the abstention gate refused the
      model. Standing up an endpoint over nothing would be a service in the estate that no
      request can be answered by.
    EOT
  }
}

variable "classifier_image_uri" {
  description = <<-EOT
    Override the serving container. Empty means the standard scikit-learn image for this region,
    assembled in `classification.tf` from a registry map with its source and its date on it.

    Left as an override rather than removed because the day this project serves a model that is
    not a linear one, the image changes and nothing else does.
  EOT
  type        = string
  default     = ""
}

variable "classifier_model_data_url" {
  description = <<-EOT
    S3 URI of the trained artefact, under the `models/` prefix the endpoint's role can read.

    Whatever accuracy this artefact has is a statement about a distribution **this repository
    generated**. It is labelled that way wherever it appears, it appears on no scoreboard, and
    "the classifier is N% accurate" is a sentence this project does not have.
  EOT
  type        = string
  default     = ""
}

variable "reader_repository_url" {
  description = <<-EOT
    The registry the reader image was pushed to, published by `infra/foundation`.

    Owned there rather than here because the image must exist before a function can be created
    from it — a registry owned by the consuming layer has to be created by the same apply that
    needs it already populated, and the first run fails at `docker push`.
  EOT
  type        = string
}

# **Off by default, and the default is the point — same rule as the endpoints it needs.**
#
# When this is false the escalation states are not in the state machine, the function that calls
# the upper tiers is not created, and the VPC endpoints those services answer on are not stood
# up (`infra/foundation`). Nothing is present-and-disabled: a page cannot escalate because there
# is nowhere for it to go.
#
# Turning it on is what a deploy that intends to spend money on reading does, and it is also the
# deploy that can finally produce an accuracy figure for the escalated fraction — which does not
# exist today and may not be implied until one does.
variable "enable_escalation_tiers" {
  description = "Create the escalation function and its states. Off: the cascade stops at tier 0."
  type        = bool
  default     = false
}

variable "escalation_model_id" {
  description = "The model the tier-3 escalation invokes. Named, never a wildcard."
  type        = string
  default     = ""
}

variable "bda_profile_arn" {
  description = <<-EOT
    The Bedrock Data Automation profile tier 2 submits to. Empty means the tier cannot run.

    Assembled by `deploy.yml` from the account and region rather than transcribed: a profile ARN
    is `data-automation-profile/<region-prefix>.data-automation-v1` and the prefix is the one
    thing about it that varies.
  EOT
  type        = string
  default     = ""
}

variable "bda_project_arn" {
  description = <<-EOT
    The document-automation project tier 2 reads through. Empty means the tier cannot run.

    **Created by `scripts/bda_project.py`, not by Terraform, and that is an exception with a
    reason.** The AWS provider declares no resource for a Bedrock Data Automation project —
    checked against `hashicorp/aws ~> 6.0` on 2026-08-13 — and neither does CloudFormation. The
    choice was a tier that cannot run or a resource the deploy creates itself; `destroy.yml`
    deletes it, because a create path with no delete path is how an estate gets left standing.

    AWS's own `public-default` project will not do: it returns `PAGE` and `ELEMENT` granularity
    with bounding boxes **disabled**, and a reading with no boxes carries no provenance. Claim 2
    is that every published field traces to a page and a box.
  EOT
  type        = string
  default     = ""
}

# ── What the lakehouse published, resolved by the deploy ─────────────────────
#
# The landing function writes the published record into the Iceberg table, so this layer now
# takes three references from the layer that owns it. They arrive as variables, never as a
# remote state read: that would make one layer's internals another layer's contract and would
# stop the two being destroyed separately, which is the property `destroy.yml` depends on.

variable "glue_database" {
  description = "The Glue database holding the record lake's tables."
  type        = string
}

variable "lake_table" {
  description = "The Iceberg table one row per published field lands in."
  type        = string
  default     = "document_version"
}

variable "athena_workgroup" {
  description = <<-EOT
    The workgroup the landing query runs in.

    Not a detail: the workgroup carries the result location, the KMS key results are written
    with, and the bytes ceiling per query. A statement run outside it would write results to a
    default location this estate does not own.
  EOT
  type        = string
}

variable "lake_bucket" {
  description = "Where the Iceberg table's data and the Athena results live."
  type        = string
}

variable "search_endpoint" {
  description = <<-EOT
    The OpenSearch Serverless collection endpoint, or empty when search is off.

    Empty is a value rather than an absence: `enable_search` lives in the lakehouse layer and
    this one cannot see it, so the endpoint being empty *is* how this layer learns the surface
    was not stood up. The indexer's `count` reads it, and a function that would have nowhere to
    write is not created.
  EOT
  type        = string
  default     = ""
}
