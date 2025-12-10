variable "environment" {
  description = "Environment name (e.g., dev, staging, production)"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "docker_image" {
  description = "Docker image for signaling server"
  type        = string
}

variable "cognito_region" {
  description = "AWS Cognito region"
  type        = string
}

variable "cognito_user_pool_id" {
  description = "AWS Cognito User Pool ID"
  type        = string
}

variable "cognito_app_client_id" {
  description = "AWS Cognito App Client ID"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where resources will be created"
  type        = string
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access WebSocket and HTTP ports"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "admin_cidr_blocks" {
  description = "CIDR blocks allowed SSH access for administration"
  type        = list(string)
}

variable "websocket_port" {
  description = "WebSocket signaling port"
  type        = number
  default     = 8080
}

variable "http_port" {
  description = "HTTP API port"
  type        = number
  default     = 8001
}
