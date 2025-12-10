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
