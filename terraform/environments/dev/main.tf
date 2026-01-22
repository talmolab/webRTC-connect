terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend configuration is provided via -backend-config flag
  # See backend-dev.hcl for S3 backend settings
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "sleap-rtc"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

# Get default VPC
data "aws_vpc" "default" {
  default = true
}

# Signaling Server Module
module "signaling_server" {
  source = "../../modules/signaling-server"

  environment   = "dev"
  instance_type = var.instance_type

  docker_image = var.docker_image

  cognito_region        = var.cognito_region
  cognito_user_pool_id  = var.cognito_user_pool_id
  cognito_app_client_id = var.cognito_app_client_id

  vpc_id              = data.aws_vpc.default.id
  allowed_cidr_blocks = var.allowed_cidr_blocks
  admin_cidr_blocks   = var.admin_cidr_blocks

  websocket_port = var.websocket_port
  http_port      = var.http_port

  # TURN server configuration
  enable_turn   = var.enable_turn
  turn_password = var.turn_password
  turn_username = var.turn_username
  turn_port     = var.turn_port

  # HTTPS configuration (DuckDNS + Let's Encrypt via Caddy)
  enable_https      = var.enable_https
  duckdns_subdomain = var.duckdns_subdomain
  duckdns_token     = var.duckdns_token
  admin_email       = var.admin_email

  # GitHub OAuth configuration
  github_client_id     = var.github_client_id
  github_client_secret = var.github_client_secret

  # JWT configuration
  jwt_private_key = var.jwt_private_key
  jwt_public_key  = var.jwt_public_key
}

# Outputs
output "signaling_server_ip" {
  description = "Public IP of signaling server (Elastic IP)"
  value       = module.signaling_server.public_ip
}

output "websocket_url" {
  description = "WebSocket URL for clients"
  value       = module.signaling_server.websocket_url
}

output "http_url" {
  description = "HTTP API URL"
  value       = module.signaling_server.http_url
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = module.signaling_server.instance_id
}

output "https_url" {
  description = "HTTPS URL for signaling server (if enabled)"
  value       = module.signaling_server.https_url
}
