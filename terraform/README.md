# Terraform Infrastructure for SLEAP-RTC Signaling Server

This directory contains Terraform configuration for deploying the SLEAP-RTC signaling server infrastructure on AWS.

## Overview

The Terraform configuration provides:
- **Elastic IP** for stable DNS across instance replacements
- **EC2 instance** running the signaling server in Docker
- **Security groups** for controlled network access
- **IAM roles** for CloudWatch and ECR permissions
- **Automated startup** via user-data script
- **Health checks** with automatic container restart
- **Multi-environment support** (dev, staging, production)

## Prerequisites

### Required Tools

1. **Terraform** >= 1.5
   ```bash
   # macOS
   brew install terraform

   # Linux
   wget https://releases.hashicorp.com/terraform/1.6.0/terraform_1.6.0_linux_amd64.zip
   unzip terraform_1.6.0_linux_amd64.zip
   sudo mv terraform /usr/local/bin/
   ```

2. **AWS CLI**
   ```bash
   # macOS
   brew install awscli

   # Linux
   curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
   unzip awscliv2.zip
   sudo ./aws/install
   ```

### Required AWS Permissions

Your AWS user/role needs permissions for:
- EC2 (instances, security groups, elastic IPs)
- IAM (roles, policies, instance profiles)
- VPC (describe VPCs)

Example IAM policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:*",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRole",
        "iam:PassRole",
        "iam:CreateInstanceProfile",
        "iam:DeleteInstanceProfile",
        "iam:AddRoleToInstanceProfile",
        "iam:RemoveRoleFromInstanceProfile"
      ],
      "Resource": "*"
    }
  ]
}
```

## AWS Credentials Setup

Configure AWS credentials using the AWS CLI:

```bash
aws configure
```

You'll be prompted for:
- AWS Access Key ID
- AWS Secret Access Key
- Default region (e.g., `us-west-1`)
- Output format (e.g., `json`)

Verify configuration:
```bash
aws sts get-caller-identity
```

## Directory Structure

```
terraform/
├── modules/
│   └── signaling-server/       # Reusable server module
│       ├── main.tf             # EC2, EIP, security groups, IAM
│       ├── variables.tf        # Configurable inputs
│       ├── outputs.tf          # EIP, URLs, instance ID
│       └── user-data.sh        # Automated startup script
└── environments/
    ├── dev/                    # Development environment
    │   ├── main.tf
    │   ├── variables.tf
    │   └── terraform.tfvars.example
    └── production/             # Production environment
        ├── main.tf
        ├── variables.tf
        └── terraform.tfvars.example
```

## First-Time Deployment

### 1. Choose Environment

```bash
cd terraform/environments/dev  # or production
```

### 2. Create Configuration File

Copy the example and customize:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your values:
- Docker image
- Cognito configuration
- Network CIDR blocks (restrict in production!)
- Your admin IP for SSH access

**IMPORTANT**: Never commit `terraform.tfvars` if it contains sensitive data!

### 3. Initialize Terraform

```bash
terraform init
```

This downloads the AWS provider and initializes the backend.

### 4. Review Plan

```bash
terraform plan
```

Review what resources will be created. Look for:
- 1 EC2 instance
- 1 Elastic IP
- 1 Security group
- 1 IAM role + instance profile
- 2 IAM policies

### 5. Apply Configuration

```bash
terraform apply
```

Type `yes` to confirm. Deployment takes ~5 minutes.

### 6. Save Outputs

```bash
terraform output
```

Example output:
```
signaling_server_ip = "54.176.92.10"
websocket_url = "ws://54.176.92.10:8080"
http_url = "http://54.176.92.10:8001"
instance_id = "i-0123456789abcdef"
```

**Save the Elastic IP** - this is your stable address that won't change!

## Updating Infrastructure

### Updating Instance Size

1. Edit `terraform.tfvars`:
   ```hcl
   instance_type = "t3.medium"  # was t3.small
   ```

2. Apply changes:
   ```bash
   terraform apply
   ```

Terraform will:
- Create new instance with new size
- Move Elastic IP to new instance (same IP!)
- Destroy old instance

**Downtime**: ~30 seconds (while EIP moves)

### Updating Docker Image Version

1. Edit `terraform.tfvars`:
   ```hcl
   docker_image = "ghcr.io/talmolab/webrtc-server:new-version"
   ```

2. Apply changes:
   ```bash
   terraform apply
   ```

The instance will be recreated with the new image.

### Updating Security Rules

1. Edit `terraform.tfvars`:
   ```hcl
   allowed_cidr_blocks = ["192.168.1.0/24"]  # More restrictive
   ```

2. Apply changes:
   ```bash
   terraform apply
   ```

Security group rules update immediately (no instance recreation).

## Destroying Infrastructure

To tear down all resources:

```bash
terraform destroy
```

Type `yes` to confirm.

**WARNING**: This deletes:
- EC2 instance
- Elastic IP (will be released)
- Security group
- IAM roles

All data on the instance is lost. The Elastic IP can be reallocated when you redeploy.

## Verification and Testing

### Check Instance Status

```bash
# Get instance ID from terraform output
INSTANCE_ID=$(terraform output -raw instance_id)

# Check instance status
aws ec2 describe-instances --instance-ids $INSTANCE_ID
```

### SSH to Instance (for debugging)

```bash
# Get Elastic IP from terraform output
EIP=$(terraform output -raw signaling_server_ip)

# SSH as ubuntu user
ssh ubuntu@$EIP
```

Once connected:
```bash
# Check Docker container status
docker ps

# View container logs
docker logs sleap-rtc-signaling

# Check health check logs
cat /var/log/healthcheck.log
```

### Test Connectivity

```bash
# Get URLs from terraform output
WEBSOCKET_URL=$(terraform output -raw websocket_url)
HTTP_URL=$(terraform output -raw http_url)

# Test HTTP API (if health endpoint exists)
curl $HTTP_URL/health

# Test WebSocket (requires WebSocket client)
# Use your SLEAP-RTC client or wscat:
# wscat -c $WEBSOCKET_URL
```

## Cost Estimates

### Per Environment (Monthly, us-west-1)

**Development (t3.small)**:
- EC2 instance: ~$15/month
- Elastic IP: Free (while attached)
- Data transfer out: ~$0.09/GB
- **Total: ~$15-20/month**

**Production (t3.medium)**:
- EC2 instance: ~$30/month
- Elastic IP: Free (while attached)
- Data transfer out: ~$0.09/GB
- **Total: ~$30-40/month**

**Cost Optimizations**:
- Dev: Stop instance overnight (`aws ec2 stop-instances`) - saves ~60%
- Staging: Destroy when not testing (`terraform destroy`)
- Production: Run 24/7

**Note**: Elastic IP costs $3.60/month if allocated but not attached to a running instance.

## Troubleshooting

### Container Not Starting

1. SSH to instance:
   ```bash
   ssh ubuntu@<elastic-ip>
   ```

2. Check Docker status:
   ```bash
   systemctl status docker
   ```

3. Check user-data execution:
   ```bash
   cat /var/log/user-data-complete.log
   tail -50 /var/log/cloud-init-output.log
   ```

4. Try starting container manually:
   ```bash
   docker start sleap-rtc-signaling
   docker logs sleap-rtc-signaling
   ```

### Terraform Apply Fails

**Error: VPC not found**
- Solution: Ensure default VPC exists in your AWS account

**Error: IAM permissions denied**
- Solution: Check your AWS user has required permissions (see Prerequisites)

**Error: Instance type not available**
- Solution: Try different instance type or different AWS region

### Cannot Connect to Server

1. Check security group:
   ```bash
   # Verify your IP is in allowed_cidr_blocks
   curl ifconfig.me  # Get your IP
   ```

2. Test from different network to rule out firewall issues

3. Check container is running:
   ```bash
   ssh ubuntu@<elastic-ip>
   docker ps | grep sleap-rtc-signaling
   ```

### Elastic IP Changed

Elastic IPs should persist across `terraform apply`. If it changed:
- Check terraform state: `terraform show | grep aws_eip`
- Possible cause: Used `terraform destroy` (releases EIP)
- Next `terraform apply` allocates a new EIP

To keep same EIP: don't destroy, just update with `terraform apply`.

## Advanced: State Management

### Local State (Current Setup)

Terraform state is stored locally in `terraform.tfstate`.

**Pros**: Simple
**Cons**: Can't collaborate, no locking

### Remote State (Recommended for Teams)

Use S3 + DynamoDB for shared state:

1. Create S3 bucket and DynamoDB table:
   ```bash
   aws s3 mb s3://sleap-rtc-terraform-state
   aws dynamodb create-table \
     --table-name terraform-locks \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST
   ```

2. Add backend to `main.tf`:
   ```hcl
   terraform {
     backend "s3" {
       bucket         = "sleap-rtc-terraform-state"
       key            = "dev/terraform.tfstate"
       region         = "us-west-1"
       encrypt        = true
       dynamodb_table = "terraform-locks"
     }
   }
   ```

3. Migrate state:
   ```bash
   terraform init -migrate-state
   ```

## Multi-Environment Deployment

### Deploy to Multiple Environments

```bash
# Deploy dev
cd environments/dev
terraform apply

# Deploy production (isolated state)
cd ../production
terraform apply
```

Each environment:
- Has separate Elastic IP
- Has independent infrastructure
- Can be deployed/destroyed independently

### Staging Environment (Optional)

```bash
# Create staging from dev template
cp -r environments/dev environments/staging
cd environments/staging

# Edit terraform.tfvars for staging-specific values
# Edit main.tf to change environment = "staging"

terraform init
terraform apply
```

## Next Steps

1. **Test the deployment**: Deploy to dev, verify connectivity
2. **Update documentation**: Save your Elastic IPs in project README
3. **Set up monitoring**: Consider CloudWatch alarms for instance health
4. **Enable SSL** (future): Add SSL certificates for wss:// instead of ws://
5. **DNS setup** (future): Point a friendly domain to your Elastic IP

## Support

For issues with:
- Terraform configuration: Check this README
- Signaling server: See main repository README
- AWS permissions: Consult AWS documentation

Report infrastructure bugs at https://github.com/talmolab/webRTC-connect/issues
