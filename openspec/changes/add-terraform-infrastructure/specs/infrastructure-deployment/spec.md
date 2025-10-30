## ADDED Requirements

### Requirement: Terraform Module Structure

The system SHALL provide a reusable Terraform module for signaling server deployment located at `terraform/modules/signaling-server/` that can be instantiated across multiple environments.

#### Scenario: Module instantiation
- **WHEN** an environment configuration imports the signaling-server module
- **THEN** the module provisions EC2 instance, Elastic IP, security groups, and IAM resources

#### Scenario: Environment isolation
- **WHEN** deploying to multiple environments (dev, staging, production)
- **THEN** each environment has isolated infrastructure with separate Terraform state

#### Scenario: Variable customization
- **WHEN** environment provides instance_type, docker_image, and environment name variables
- **THEN** the module creates resources with those specific configurations

### Requirement: Elastic IP Persistence

The system SHALL allocate an AWS Elastic IP that persists across EC2 instance replacements, providing a stable IP address for client configurations.

#### Scenario: Initial EIP allocation
- **WHEN** terraform apply is run for the first time
- **THEN** an Elastic IP is allocated and attached to the EC2 instance

#### Scenario: EIP persistence across recreation
- **WHEN** EC2 instance is destroyed and recreated via terraform
- **THEN** the same Elastic IP is reattached to the new instance

#### Scenario: EIP in outputs
- **WHEN** terraform apply completes
- **THEN** the Elastic IP address is displayed in terraform outputs

#### Scenario: Stable client configuration
- **WHEN** Elastic IP is allocated (e.g., 54.176.92.10)
- **THEN** users can configure ws://54.176.92.10:8080 and it remains valid across instance changes

### Requirement: Automated Container Startup

The system SHALL automatically start the signaling server container on EC2 instance boot using a user-data script, requiring no manual SSH or Docker commands.

#### Scenario: First boot container startup
- **WHEN** EC2 instance boots for the first time
- **THEN** user-data script installs Docker, pulls container image, and starts the signaling server

#### Scenario: Container restart policy
- **WHEN** signaling server container is started
- **THEN** it is configured with --restart unless-stopped to auto-restart on crash or reboot

#### Scenario: Environment variable configuration
- **WHEN** user-data script starts the container
- **THEN** it passes COGNITO_REGION, COGNITO_USER_POOL_ID, and COGNITO_APP_CLIENT_ID as environment variables

#### Scenario: Port mapping
- **WHEN** container is started
- **THEN** ports 8080 (WebSocket) and 8001 (HTTP) are exposed on the host

### Requirement: Security Group Configuration

The system SHALL create AWS security groups that control network access to the signaling server with appropriate ingress and egress rules.

#### Scenario: WebSocket port access
- **WHEN** security group is created
- **THEN** port 8080 TCP is open for inbound traffic from allowed CIDR blocks

#### Scenario: HTTP API port access
- **WHEN** security group is created
- **THEN** port 8001 TCP is open for inbound traffic from allowed CIDR blocks

#### Scenario: SSH access restriction
- **WHEN** security group is created
- **THEN** port 22 TCP is open only from admin_cidr_blocks (not 0.0.0.0/0)

#### Scenario: Outbound access
- **WHEN** security group is created
- **THEN** all outbound traffic is allowed (for Docker image pulls, package installations)

#### Scenario: Environment-specific rules
- **WHEN** deploying to development environment
- **THEN** allowed_cidr_blocks can be 0.0.0.0/0 for unrestricted access

### Requirement: IAM Role and Permissions

The system SHALL create an IAM instance profile with appropriate permissions for CloudWatch logging and container registry access.

#### Scenario: Instance profile attachment
- **WHEN** EC2 instance is created
- **THEN** an IAM instance profile is attached to the instance

#### Scenario: CloudWatch Logs permissions
- **WHEN** IAM role policy is created
- **THEN** it grants permissions for writing logs to CloudWatch

#### Scenario: ECR/GHCR access
- **WHEN** IAM role policy is created
- **THEN** it grants permissions for pulling Docker images from registries

### Requirement: Health Check Monitoring

The system SHALL implement automated health checking via cron job that monitors container status and restarts if necessary.

#### Scenario: Container status check
- **WHEN** health check cron runs every 5 minutes
- **THEN** it verifies the signaling server container is running

#### Scenario: Automatic restart on failure
- **WHEN** health check detects container is not running
- **THEN** it automatically starts the container

#### Scenario: Health check logging
- **WHEN** health check runs
- **THEN** it logs status to /var/log/healthcheck.log for debugging

### Requirement: Infrastructure Outputs

The system SHALL output essential connection information after terraform apply completes, including IP addresses and service URLs.

#### Scenario: Elastic IP output
- **WHEN** terraform apply completes
- **THEN** output displays the Elastic IP address

#### Scenario: WebSocket URL output
- **WHEN** terraform apply completes
- **THEN** output displays the WebSocket URL (ws://IP:8080)

#### Scenario: HTTP API URL output
- **WHEN** terraform apply completes
- **THEN** output displays the HTTP API URL (http://IP:8001)

### Requirement: Environment-Specific Configuration

The system SHALL support separate configurations for development, staging, and production environments with environment-specific variables.

#### Scenario: Development environment
- **WHEN** deploying to environments/dev
- **THEN** uses smaller instance type (t3.small) and relaxed security settings

#### Scenario: Production environment
- **WHEN** deploying to environments/production
- **THEN** uses larger instance type (t3.medium) and restricted security settings

#### Scenario: Terraform state isolation
- **WHEN** each environment directory contains terraform state
- **THEN** changes to one environment do not affect other environments

### Requirement: Deployment Documentation

The system SHALL provide comprehensive documentation for deploying and managing Terraform infrastructure.

#### Scenario: Prerequisites documentation
- **WHEN** README.md is consulted
- **THEN** it lists required tools (Terraform, AWS CLI) and AWS permissions

#### Scenario: Deployment instructions
- **WHEN** README.md is consulted
- **THEN** it provides step-by-step terraform init, plan, apply workflow

#### Scenario: Configuration example
- **WHEN** README.md is consulted
- **THEN** it shows example terraform.tfvars with required variables

#### Scenario: Troubleshooting guide
- **WHEN** README.md is consulted
- **THEN** it provides common issues and solutions

### Requirement: Cost Transparency

The system SHALL document estimated AWS costs for running the signaling server infrastructure.

#### Scenario: Cost breakdown
- **WHEN** documentation is consulted
- **THEN** it shows EC2, EIP, and data transfer costs per environment

#### Scenario: Cost optimization guidance
- **WHEN** documentation is consulted
- **THEN** it explains how to reduce costs (stopping dev instances, destroying unused environments)

### Requirement: Disaster Recovery

The system SHALL enable quick disaster recovery through infrastructure-as-code reproducibility.

#### Scenario: Complete infrastructure loss
- **WHEN** an AWS region becomes unavailable
- **THEN** running terraform apply in a different region recreates the entire infrastructure

#### Scenario: Configuration drift prevention
- **WHEN** manual changes are made to infrastructure
- **THEN** terraform plan detects the drift and shows what changed

#### Scenario: Version control
- **WHEN** infrastructure changes are made
- **THEN** they are committed to git for audit trail and rollback capability
