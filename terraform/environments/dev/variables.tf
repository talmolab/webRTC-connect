variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-west-1"
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

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access WebSocket and HTTP ports"
  type        = list(string)
  default     = ["0.0.0.0/0"] # Open for dev, restrict in production
}

variable "admin_cidr_blocks" {
  description = "CIDR blocks allowed SSH access"
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

# TURN Server Configuration
variable "enable_turn" {
  description = "Enable coturn TURN server on the instance"
  type        = bool
  default     = true
}

variable "turn_password" {
  description = "Password for TURN server authentication"
  type        = string
  sensitive   = true
  default     = ""
}

variable "turn_username" {
  description = "Username for TURN server authentication"
  type        = string
  default     = "sleap"
}

variable "turn_port" {
  description = "TURN server listening port"
  type        = number
  default     = 3478
}

# =============================================================================
# HTTPS Configuration (DuckDNS + Let's Encrypt via Caddy)
# =============================================================================

variable "enable_https" {
  description = "Enable HTTPS via Caddy + Let's Encrypt"
  type        = bool
  default     = false
}

variable "duckdns_subdomain" {
  description = "DuckDNS subdomain (without .duckdns.org)"
  type        = string
  default     = ""
}

variable "duckdns_token" {
  description = "DuckDNS API token for DNS updates"
  type        = string
  sensitive   = true
  default     = ""
}

variable "admin_email" {
  description = "Email for Let's Encrypt certificate notifications"
  type        = string
  default     = ""
}

# =============================================================================
# GitHub OAuth Configuration
# =============================================================================

variable "github_client_id" {
  description = "GitHub OAuth App Client ID"
  type        = string
  default     = ""
}

variable "github_client_secret" {
  description = "GitHub OAuth App Client Secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "jwt_private_key" {
  description = "RSA private key for JWT signing (newlines replaced with |)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "jwt_public_key" {
  description = "RSA public key for JWT verification (newlines replaced with |)"
  type        = string
  default     = ""
}
