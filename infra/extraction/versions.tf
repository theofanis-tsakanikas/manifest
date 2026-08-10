# The extraction pipeline: a state machine, a review queue, and the roles that reach the
# readers. Applied only from a gated workflow, and first applied on 2026-08-10 (decision 14).
# The readers it reaches are the tier-0 image; no managed extraction engine has been called.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    key          = "extraction/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "manifest:project"    = var.project
      "manifest:layer"      = "extraction"
      "manifest:managed"    = "terraform"
      "manifest:expires-at" = var.expires_at
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
