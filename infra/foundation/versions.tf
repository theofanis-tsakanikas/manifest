# Every layer but `bootstrap` keeps its state in the backend `bootstrap` describes, and applies
# only from a gated workflow. A layer that can be applied from a laptop is a layer that will
# drift, and the drift is found by a plan that wants to destroy something nobody remembers
# creating.
#
# **Nothing here has been applied.** `docs/DECISIONS.md` 14. The backend block is written so the
# configuration is complete and validates as the real thing; `terraform init -backend=false` is
# what CI runs, and it never reaches the bucket.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    key          = "foundation/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "manifest:project" = var.project
      "manifest:layer"   = "foundation"
      "manifest:managed" = "terraform"
      # Read by the reaper. Every layer above bootstrap carries one, which is what makes an
      # estate that nobody remembers standing up an estate that tears itself down.
      "manifest:expires-at" = var.expires_at
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
