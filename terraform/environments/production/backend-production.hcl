# Backend configuration for production environment
# This file configures remote state storage in S3 with DynamoDB locking

bucket         = "sleap-rtc-terraform-state-711387140753"
key            = "production/terraform.tfstate"
region         = "us-west-1"
encrypt        = true
dynamodb_table = "sleap-rtc-terraform-locks"
