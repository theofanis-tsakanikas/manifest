# The bootstrap layer's own state would be LOCAL, and that is not an oversight.
#
# This layer creates the remote backend, so it cannot store its state in a bucket it has not
# created yet. Every other layer under `infra/` uses the S3 backend this one creates, and is
# applied only from a gated workflow: a layer that can be applied from a laptop is a layer
# that will drift.
#
# **This layer is applied from a laptop, and only this one.** `docs/DECISIONS.md` 14. It was
# applied on 2026-08-10 and it is deliberately *not* torn down with the rest: it holds the
# state bucket, the lock table and the OIDC trust every other layer's apply depends on, so
# destroying it would leave the estate above it unreachable by the only path allowed to reach
# it. `destroy.yml` does not name it, and that is a decision rather than an omission.

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
