# Design: Terraform Infrastructure for Signaling Server

## Problem Statement

The current manual deployment process for the signaling server has several limitations:
1. **Unstable DNS**: EC2 instance DNS changes on recreation (ec2-54-176-92-10 → ec2-52-8-123-45)
2. **Manual Operations**: Requires SSH access and manual Docker commands
3. **No Reproducibility**: Deployment steps exist only in operator's memory
4. **Configuration Drift**: No guarantee that dev/staging/production are identical
5. **No Disaster Recovery**: Lost instance = manual reconstruction from scratch

## Solution Architecture

### Infrastructure-as-Code with Terraform

Use Terraform to declaratively define infrastructure that can be:
- Version controlled (git)
- Reviewed (pull requests)
- Reproduced (terraform apply)
- Destroyed and recreated identically
- Deployed across multiple environments

### Elastic IP for DNS Stability

**Problem**: EC2 instance DNS changes when instance is replaced
**Solution**: Allocate an Elastic IP that persists across instance lifecycles

```
User config.toml → ws://54.176.92.10:8080 (EIP)
                        ↓
              Terraform attaches EIP to current EC2 instance
                        ↓
              Instance can be replaced without changing EIP
```

**Benefits**:
- EIP address never changes (54.176.92.10 stays the same)
- Users never need to update their config files
- Instance can be recreated/resized without service interruption
- Future: can add DNS name pointing to EIP (ws://signaling.sleap-rtc.io → 54.176.92.10)

### Module Structure

```
terraform/
├── modules/
│   └── signaling-server/      # Reusable server module
│       ├── main.tf            # EC2, EIP, security groups, IAM
│       ├── variables.tf       # Configurable inputs
│       ├── outputs.tf         # EIP, instance ID, etc.
│       └── user-data.sh       # Automated container startup
└── environments/
    ├── dev/                   # Development environment
    │   ├── main.tf            # Uses signaling-server module
    │   ├── variables.tf
    │   └── terraform.tfvars   # Dev-specific values
    ├── staging/
    └── production/
```

**Rationale**: Modules allow reuse across environments while keeping environment-specific configuration separate.

### Component Design

#### 1. EC2 Instance
- **AMI**: Latest Ubuntu LTS (data source for auto-selection)
- **Instance Type**: Configurable (t3.small for dev, t3.medium for production)
- **User Data**: Shell script that runs on boot to install Docker and start container
- **Lifecycle**: `create_before_destroy = true` for zero-downtime updates

#### 2. Elastic IP
- **Allocation**: One per environment
- **Association**: Attached to EC2 instance via Terraform
- **Persistence**: Survives instance replacement
- **Cost**: Free while attached; $3.60/month if detached

#### 3. Security Groups
- **Inbound**:
  - Port 8080 (WebSocket signaling)
  - Port 8001 (HTTP API)
  - Port 22 (SSH for debugging, restricted to admin IPs)
- **Outbound**: Allow all (for Docker image pulls, etc.)

#### 4. IAM Role & Instance Profile
- **Permissions**:
  - CloudWatch Logs (for centralized logging)
  - ECR (for pulling Docker images from private registries)
- **Attachment**: Instance profile attached to EC2

#### 5. User Data Script
- **Phase 1**: Install Docker, AWS CLI
- **Phase 2**: Pull signaling server container
- **Phase 3**: Start container with:
  - `--restart unless-stopped` (auto-restart on crash/reboot)
  - Cognito environment variables
  - Port mappings
- **Phase 4**: Set up health check cron job

### Automated Startup Flow

```
terraform apply
      ↓
Creates EC2 instance
      ↓
EC2 boots and runs user-data script
      ↓
Script installs Docker
      ↓
Script pulls ghcr.io/talmolab/webrtc-server:tag
      ↓
Script runs: docker run -d --restart unless-stopped ...
      ↓
Signaling server is running (no manual intervention)
      ↓
Cron healthcheck monitors container every 5 minutes
```

### State Management

**Terraform State**: Stores current infrastructure state
- **Local Backend** (Phase 1): State files in git (not ideal but simple)
- **S3 Backend** (Future): Centralized state storage with locking
  - Prevents concurrent modifications
  - Enables team collaboration
  - Adds state history

**Rationale for Local First**: Start simple, migrate to S3 when team grows or when state conflicts occur.

### Environment Isolation

Each environment gets:
- Separate Elastic IP
- Separate EC2 instance
- Separate security groups
- Separate Terraform state

**Dev Environment**:
- Smaller instance (t3.small)
- Relaxed security (0.0.0.0/0 allowed for testing)
- Can be destroyed to save costs

**Production Environment**:
- Larger instance (t3.medium)
- Restricted security (specific IP ranges)
- Always running

### Deployment Workflow

```bash
# Initial setup (one-time)
cd terraform/environments/dev
terraform init

# Deploy/update infrastructure
terraform plan    # Preview changes
terraform apply   # Apply changes

# Outputs show:
# - Elastic IP: 54.176.92.10
# - WebSocket URL: ws://54.176.92.10:8080
# - HTTP URL: http://54.176.92.10:8001

# Tear down (when needed)
terraform destroy
```

### Cost Considerations

**Per Environment**:
- EC2 t3.small: ~$15/month (24/7)
- Elastic IP: Free (while attached)
- Data transfer: ~$0.09/GB
- **Total**: ~$15-20/month per environment

**Cost Optimizations**:
- Dev: Can stop instance overnight (saves ~60%)
- Staging: Can destroy when not testing
- Production: Run 24/7

### Security Design

1. **Credentials**: Passed via user-data (Cognito config)
   - Future: Use AWS Secrets Manager
2. **Network**: Security groups restrict access
3. **SSH**: Only from specific admin IPs
4. **IAM**: Principle of least privilege (only CloudWatch + ECR)

### Future Enhancements (Out of Scope)

1. **DNS with Route53**: Add friendly domain names
2. **Auto-scaling**: Multiple instances with load balancer
3. **Monitoring**: CloudWatch alarms for high CPU, container down
4. **SSL/TLS**: Serve over wss:// instead of ws://
5. **Multi-region**: Deploy to us-east-1 and us-west-2
6. **S3 State Backend**: Team collaboration support

## Trade-offs

### Chosen: Elastic IP over Load Balancer
- **Pro**: Simple, cheap ($0/month vs $16/month for ALB)
- **Pro**: Single instance sufficient for current scale
- **Con**: No built-in redundancy
- **Con**: ~30 second downtime when recreating instance (EIP reattachment)
- **Rationale**: Start simple, add ALB when scale requires it

### Chosen: User Data over Configuration Management
- **Pro**: No additional tools (Ansible, Chef, etc.)
- **Pro**: Simple shell script, easy to debug
- **Con**: Not idempotent (runs only on first boot)
- **Con**: Changes require instance replacement
- **Rationale**: Sufficient for immutable infrastructure pattern

### Chosen: Module Pattern over Flat Structure
- **Pro**: Reusable across environments
- **Pro**: Changes propagate to all environments
- **Con**: Slightly more complex structure
- **Rationale**: Essential for maintainability with multiple environments

## Validation

### Success Criteria
1. ✅ `terraform apply` creates working signaling server
2. ✅ Elastic IP persists across `terraform destroy && terraform apply`
3. ✅ Container starts automatically without SSH
4. ✅ Health check restarts crashed container
5. ✅ Can update Docker image version via Terraform variable
6. ✅ Dev and production environments are isolated

### Testing Plan
1. Deploy dev environment
2. Test client connection to EIP
3. Destroy and recreate, verify same EIP
4. Update Docker image version, verify rollout
5. Deploy production environment
6. Verify environments are isolated

## Dependencies

### Tools Required
- Terraform >= 1.5
- AWS CLI (for authentication)
- AWS account with appropriate permissions

### AWS Resources Required
- EC2 instance launch permission
- Elastic IP allocation permission
- Security group creation permission
- IAM role/policy creation permission

### Existing Infrastructure
- Relies on: Cognito user pool (already exists)
- Relies on: Docker image in GHCR (already exists)
- Modifies: None (new infrastructure only)

## Documentation Requirements

1. **terraform/README.md**: Deployment guide
   - Prerequisites
   - AWS credential setup
   - First-time deployment
   - Updating infrastructure
   - Troubleshooting

2. **README.md updates**: Link to terraform docs

3. **config.example.toml updates**: Use Elastic IPs instead of instance DNS

4. **DEVELOPMENT.md**: Add infrastructure section
