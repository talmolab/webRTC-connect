## Why

The signaling server is currently deployed manually via SSH to an EC2 instance with hardcoded DNS that changes on each instance replacement. This creates several problems:
- Manual SSH and Docker command execution required for deployment
- No version control or audit trail for infrastructure state
- DNS changes (ec2-X-X-X-X.region.compute.amazonaws.com) when instance is recreated, breaking user configurations
- Difficult to replicate across environments (dev, staging, production)
- No automated health checks or recovery
- Configuration drift between deployments
- No disaster recovery plan

## What Changes

- Add Terraform infrastructure-as-code for signaling server deployment
- Implement Elastic IP (EIP) for stable IP address across instance replacements
- Create reusable Terraform modules for signaling server infrastructure
- Support multiple environments (dev, staging, production) with environment-specific configurations
- Add automated container startup via EC2 user-data script
- Implement security groups for controlled network access
- Add IAM roles for instance permissions (CloudWatch, ECR access)
- Document deployment procedures and cost estimates
- Start with basic Elastic IP setup (DNS/Route53 support to be added in future proposal)

## Impact

- Affected specs: infrastructure-deployment (new capability)
- New files:
  - `terraform/` directory structure
  - `terraform/modules/signaling-server/` module
  - `terraform/environments/{dev,staging,production}/` configurations
  - `terraform/README.md` deployment documentation
- Infrastructure changes:
  - AWS Elastic IP allocation
  - EC2 instance managed by Terraform
  - Security groups for ports 8080, 8001, 22
  - IAM instance profile for CloudWatch/ECR
- Documentation updates:
  - README.md with terraform deployment section
  - config.example.toml updated with stable EIP addresses
- No code changes to sleap_rtc package
- Backward compatible: existing manual deployments continue to work
