terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40.0"
    }
  }
}

# Local state by default — single operator, ephemeral benchmark deployments.
# To share state across operators, swap in an S3 + DynamoDB backend block.
