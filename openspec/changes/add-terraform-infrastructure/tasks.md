## 1. Terraform Module Foundation

- [ ] 1.1 Create `terraform/modules/signaling-server/` directory structure
- [ ] 1.2 Create `terraform/modules/signaling-server/main.tf` with provider configuration
- [ ] 1.3 Create `terraform/modules/signaling-server/variables.tf` with input variables (environment, instance_type, docker_image, cognito_*, vpc_id, allowed_cidr_blocks, admin_cidr_blocks, websocket_port, http_port)
- [ ] 1.4 Create `terraform/modules/signaling-server/outputs.tf` with outputs (public_ip, elastic_ip, websocket_url, http_url, instance_id)
- [ ] 1.5 Add `.gitignore` for terraform state files and `.terraform/` directory

## 2. EC2 Instance Configuration

- [ ] 2.1 Add data source for latest Ubuntu LTS AMI in main.tf
- [ ] 2.2 Define aws_instance resource with instance_type variable
- [ ] 2.3 Configure instance lifecycle (create_before_destroy = true)
- [ ] 2.4 Add instance tags (Name, Environment, ManagedBy: Terraform)
- [ ] 2.5 Attach security group to instance via vpc_security_group_ids
- [ ] 2.6 Attach IAM instance profile to instance

## 3. Elastic IP Resources

- [ ] 3.1 Create aws_eip resource with domain = "vpc"
- [ ] 3.2 Associate EIP with EC2 instance
- [ ] 3.3 Add EIP tags (Name, Environment)
- [ ] 3.4 Output EIP public_ip value
- [ ] 3.5 Output AWS-provided public_dns value

## 4. Security Group Configuration

- [ ] 4.1 Create aws_security_group resource
- [ ] 4.2 Add ingress rule for WebSocket port (8080 TCP) from allowed_cidr_blocks
- [ ] 4.3 Add ingress rule for HTTP API port (8001 TCP) from allowed_cidr_blocks
- [ ] 4.4 Add ingress rule for SSH (22 TCP) from admin_cidr_blocks only
- [ ] 4.5 Add egress rule allowing all outbound traffic (0.0.0.0/0)
- [ ] 4.6 Add security group tags and description

## 5. IAM Role and Instance Profile

- [ ] 5.1 Create aws_iam_role resource with EC2 assume role policy
- [ ] 5.2 Create aws_iam_instance_profile resource
- [ ] 5.3 Create aws_iam_role_policy for CloudWatch Logs write permissions
- [ ] 5.4 Create aws_iam_role_policy for ECR read permissions (ecr:GetAuthorizationToken, ecr:BatchCheckLayerAvailability, ecr:GetDownloadUrlForLayer, ecr:BatchGetImage)
- [ ] 5.5 Add IAM resource tags

## 6. User Data Script

- [ ] 6.1 Create `terraform/modules/signaling-server/user-data.sh` template file
- [ ] 6.2 Add Docker installation commands (apt-get install docker.io, systemctl start docker)
- [ ] 6.3 Add Docker daemon configuration for CloudWatch logs driver
- [ ] 6.4 Add docker pull command for signaling server image
- [ ] 6.5 Add docker run command with --restart unless-stopped, port mappings (-p 8080:8080 -p 8001:8001), and environment variables
- [ ] 6.6 Add health check script creation (/usr/local/bin/healthcheck.sh) that checks container status
- [ ] 6.7 Add cron job for health check (every 5 minutes)
- [ ] 6.8 Reference user-data.sh in EC2 instance via templatefile() function

## 7. Development Environment

- [ ] 7.1 Create `terraform/environments/dev/` directory
- [ ] 7.2 Create `terraform/environments/dev/main.tf` with terraform and provider blocks
- [ ] 7.3 Add signaling-server module instantiation with dev-specific variables
- [ ] 7.4 Create `terraform/environments/dev/variables.tf` with environment-specific inputs
- [ ] 7.5 Create `terraform/environments/dev/terraform.tfvars` example with dev values (instance_type = "t3.small", allowed_cidr_blocks = ["0.0.0.0/0"])
- [ ] 7.6 Add module outputs to dev environment
- [ ] 7.7 Add data source for default VPC

## 8. Production Environment

- [ ] 8.1 Create `terraform/environments/production/` directory
- [ ] 8.2 Create `terraform/environments/production/main.tf` with terraform and provider blocks
- [ ] 8.3 Add signaling-server module instantiation with production-specific variables
- [ ] 8.4 Create `terraform/environments/production/variables.tf` with environment-specific inputs
- [ ] 8.5 Create `terraform/environments/production/terraform.tfvars` example with production values (instance_type = "t3.medium", allowed_cidr_blocks = ["<specific-ips>"])
- [ ] 8.6 Add module outputs to production environment
- [ ] 8.7 Add data source for default VPC

## 9. Terraform Documentation

- [ ] 9.1 Create `terraform/README.md` with comprehensive deployment guide
- [ ] 9.2 Document prerequisites (Terraform >= 1.5, AWS CLI, AWS permissions)
- [ ] 9.3 Document AWS credential setup (aws configure)
- [ ] 9.4 Document first-time deployment workflow (terraform init, terraform plan, terraform apply)
- [ ] 9.5 Document how to update infrastructure (change variable, terraform apply)
- [ ] 9.6 Document how to update Docker image version
- [ ] 9.7 Document how to destroy infrastructure (terraform destroy)
- [ ] 9.8 Document cost estimates per environment
- [ ] 9.9 Add troubleshooting section (common errors, how to SSH for debugging)
- [ ] 9.10 Add examples for each environment deployment

## 10. Project Documentation Updates

- [ ] 10.1 Update main README.md with link to terraform deployment documentation
- [ ] 10.2 Add "Infrastructure" section to README.md explaining Terraform deployment
- [ ] 10.3 Update config.example.toml to use Elastic IP addresses instead of EC2 instance DNS
- [ ] 10.4 Add note to config.example.toml explaining EIP stability
- [ ] 10.5 Update DEVELOPMENT.md with infrastructure management section
- [ ] 10.6 Document when to recreate infrastructure vs. manual fixes

## 11. Validation and Testing

- [ ] 11.1 Run `terraform fmt` to format all .tf files
- [ ] 11.2 Run `terraform validate` in each environment directory
- [ ] 11.3 Deploy to dev environment and verify outputs
- [ ] 11.4 Test SSH access to dev instance
- [ ] 11.5 Verify Docker container is running on dev instance (docker ps)
- [ ] 11.6 Test WebSocket connection to dev EIP (ws://IP:8080)
- [ ] 11.7 Test HTTP API connection to dev EIP (http://IP:8001)
- [ ] 11.8 Run `terraform destroy` on dev and verify EIP is released
- [ ] 11.9 Run `terraform apply` again and verify same EIP is reallocated
- [ ] 11.10 Verify health check cron is running (crontab -l)
- [ ] 11.11 Test container auto-restart by stopping container manually (docker stop, wait 5 min, check restart)

## 12. .gitignore Updates

- [ ] 12.1 Add `terraform/.terraform/` to .gitignore
- [ ] 12.2 Add `terraform/**/.terraform/` to .gitignore
- [ ] 12.3 Add `terraform/**/.terraform.lock.hcl` to .gitignore (or commit it for dependency locking)
- [ ] 12.4 Add `terraform/**/*.tfstate` to .gitignore
- [ ] 12.5 Add `terraform/**/*.tfstate.backup` to .gitignore
- [ ] 12.6 Add `terraform/**/.terraform.tfvars` to .gitignore if containing secrets

## 13. Optional: Staging Environment

- [ ] 13.1 Create `terraform/environments/staging/` directory (copy from dev)
- [ ] 13.2 Update staging terraform.tfvars with staging-specific values
- [ ] 13.3 Deploy to staging and verify isolation from dev
