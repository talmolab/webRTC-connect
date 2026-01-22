# Data source for latest Ubuntu LTS AMI
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security Group
resource "aws_security_group" "signaling" {
  name        = "sleap-rtc-signaling-${var.environment}"
  description = "Security group for SLEAP-RTC signaling server"
  vpc_id      = var.vpc_id

  # WebSocket port
  ingress {
    from_port   = var.websocket_port
    to_port     = var.websocket_port
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
    description = "WebSocket signaling"
  }

  # HTTP API port
  ingress {
    from_port   = var.http_port
    to_port     = var.http_port
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
    description = "HTTP API"
  }

  # SSH port
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_cidr_blocks
    description = "SSH admin access"
  }

  # TURN/STUN UDP port
  dynamic "ingress" {
    for_each = var.enable_turn ? [1] : []
    content {
      from_port   = var.turn_port
      to_port     = var.turn_port
      protocol    = "udp"
      cidr_blocks = ["0.0.0.0/0"]
      description = "TURN/STUN UDP"
    }
  }

  # TURN/STUN TCP port
  dynamic "ingress" {
    for_each = var.enable_turn ? [1] : []
    content {
      from_port   = var.turn_port
      to_port     = var.turn_port
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
      description = "TURN/STUN TCP"
    }
  }

  # TURN relay ports (UDP)
  dynamic "ingress" {
    for_each = var.enable_turn ? [1] : []
    content {
      from_port   = 49152
      to_port     = 65535
      protocol    = "udp"
      cidr_blocks = ["0.0.0.0/0"]
      description = "TURN relay ports"
    }
  }

  # HTTPS access (Caddy)
  dynamic "ingress" {
    for_each = var.enable_https ? [1] : []
    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidr_blocks
      description = "HTTPS"
    }
  }

  # HTTP for Let's Encrypt ACME challenge
  dynamic "ingress" {
    for_each = var.enable_https ? [1] : []
    content {
      from_port   = 80
      to_port     = 80
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
      description = "HTTP for ACME"
    }
  }

  # Allow all outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = {
    Name        = "sleap-rtc-signaling-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# IAM Role for EC2 instance
resource "aws_iam_role" "signaling" {
  name = "sleap-rtc-signaling-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = {
    Name        = "sleap-rtc-signaling-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# IAM Instance Profile
resource "aws_iam_instance_profile" "signaling" {
  name = "sleap-rtc-signaling-${var.environment}"
  role = aws_iam_role.signaling.name

  tags = {
    Name        = "sleap-rtc-signaling-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# IAM Policy for CloudWatch Logs
resource "aws_iam_role_policy" "cloudwatch_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.signaling.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ]
      Resource = "arn:aws:logs:*:*:*"
    }]
  })
}

# IAM Policy for ECR access
resource "aws_iam_role_policy" "ecr_read" {
  name = "ecr-read"
  role = aws_iam_role.signaling.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ]
      Resource = "*"
    }]
  })
}

# EC2 Instance
resource "aws_instance" "signaling" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.signaling.id]
  iam_instance_profile   = aws_iam_instance_profile.signaling.name

  user_data = templatefile("${path.module}/user-data.sh", {
    docker_image         = var.docker_image
    cognito_region       = var.cognito_region
    cognito_user_pool_id = var.cognito_user_pool_id
    cognito_client_id    = var.cognito_app_client_id
    websocket_port       = var.websocket_port
    http_port            = var.http_port
    environment          = var.environment
    # TURN configuration
    turn_port     = var.turn_port
    turn_username = var.turn_username
    turn_password = var.turn_password
    # HTTPS configuration
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
  })

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "sleap-rtc-signaling-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# Elastic IP
resource "aws_eip" "signaling" {
  instance = aws_instance.signaling.id
  domain   = "vpc"

  tags = {
    Name        = "sleap-rtc-signaling-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}
