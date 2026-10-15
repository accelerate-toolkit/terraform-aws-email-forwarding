terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.68.0, < 7.0.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.7.1, < 3.0.0" }
  }
}
