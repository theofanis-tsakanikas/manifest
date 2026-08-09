# The bootstrap layer's own state would be LOCAL, and that is not an oversight.
#
# This layer creates the remote backend, so it cannot store its state in a bucket it has not
# created yet. Every other layer under `infra/` uses the S3 backend this one creates, and is
# applied only from a gated workflow: a layer that can be applied from a laptop is a layer
# that will drift.
#
# **And this layer is not applied either.** `docs/DECISIONS.md` 14 — nothing in this
# repository is ever applied to AWS, including the one layer whose design permits a laptop
# apply. What is claimed is `terraform validate` against real provider schemas and checkov at
# zero findings, with the limits of that stated in `scripts/tf_validate.py`.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "manifest:project" = var.project
      "manifest:layer"   = "bootstrap"
      "manifest:managed" = "terraform"

      # Every other layer carries an expiry the reaper enforces. This one must not: the state
      # bucket and the role CI assumes are what every other layer is destroyed *by*. A reaper
      # that eats the backend leaves an estate nothing can reach, which is the one failure
      # worse than paying for an idle one.
      "manifest:expires-at" = "never"
    }
  }
}
