terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    key          = "lakehouse/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "manifest:project"    = var.project
      "manifest:layer"      = "lakehouse"
      "manifest:managed"    = "terraform"
      "manifest:expires-at" = var.expires_at
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
