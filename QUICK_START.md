# Quick Start: Deploy with GitHub Actions

## Summary

You need **two separate IAM roles**:

1. **`sleapRTC-SignalingServerRole`** ✅ Already exists
   - For your EC2 instance (the application)
   - Has Cognito + DynamoDB permissions
   - Keep as-is

2. **`sleap-rtc-github-actions-role`** ❌ Need to create
   - For GitHub Actions (the deployment process)
   - Needs EC2 + IAM + VPC permissions
   - Create following steps below

## Step-by-Step Instructions

### 1. Create the GitHub Actions IAM Role

Run these commands:

```bash
# Create OIDC provider (skip if already exists)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# Create the role
aws iam create-role \
  --role-name sleap-rtc-github-actions-role \
  --assume-role-policy-document file://github-actions-trust-policy.json

# Attach Terraform permissions
aws iam put-role-policy \
  --role-name sleap-rtc-github-actions-role \
  --policy-name terraform-permissions \
  --policy-document file://iam-policy-terraform.json

# Get the role ARN (save this!)
aws iam get-role --role-name sleap-rtc-github-actions-role --query 'Role.Arn' --output text
```

### 2. Create S3 Backend

Run the setup script:

```bash
./scripts/setup-aws-infrastructure.sh
```

This creates:
- S3 bucket: `sleap-rtc-terraform-state-711387140753`
- DynamoDB table: `sleap-rtc-terraform-locks`

### 3. Add GitHub Secrets

1. Go to: https://github.com/talmolab/webRTC-connect/settings/secrets/actions
2. Add these secrets:
   - `AWS_ROLE_ARN`: The ARN from step 1 (arn:aws:iam::711387140753:role/sleap-rtc-github-actions-role)
   - `AWS_REGION`: `us-west-1`

### 4. Deploy!

**Option A: Via GitHub UI**
1. Go to: https://github.com/talmolab/webRTC-connect/actions
2. Click "Deploy Terraform (Dev)"
3. Click "Run workflow"
4. Select environment: `dev`
5. Click "Run workflow"

**Option B: Push changes**
```bash
git add .
git commit -m "feat: add GitHub Actions deployment"
git push
```

The workflow will automatically run!

## Verify Deployment

Check the workflow output for:
- `signaling_server_ip` - Your server's public IP
- `websocket_url` - WebSocket endpoint
- `http_url` - HTTP API endpoint
- `instance_id` - EC2 instance ID

Test it:
```bash
# Replace with your actual IP from the output
curl http://<elastic-ip>:8001/health
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ GitHub Actions                                       │
│                                                      │
│ 1. Assumes: sleap-rtc-github-actions-role           │
│ 2. Permissions: EC2, IAM, VPC (deploy infra)        │
│ 3. Runs: terraform apply                            │
│                                                      │
│    └──> Creates EC2 instance                        │
│         └──> Assigns: sleapRTC-SignalingServerRole  │
│              (allows app to use Cognito/DynamoDB)   │
└─────────────────────────────────────────────────────┘
```

## Troubleshooting

### "Could not assume role"
- Verify OIDC provider exists: `aws iam list-open-id-connect-providers`
- Verify role trust policy matches repository name exactly

### "Access Denied to S3/DynamoDB"
- Add S3/DynamoDB permissions to the GitHub Actions role
- See `terraform/GITHUB_ACTIONS_SETUP.md` for details

### "Backend initialization required"
If you have local state, migrate it:
```bash
cd terraform/environments/dev
terraform init -backend-config=backend-dev.hcl -migrate-state
```

## Daily Workflow

**Deploy changes:**
1. Edit Terraform files
2. Push to main branch
3. Deployment runs automatically

**Destroy infrastructure:**
1. Go to Actions → "Destroy Terraform (Dev)"
2. Type "destroy" to confirm
3. Run workflow

## Files Created

- `github-actions-trust-policy.json` - Trust policy for GitHub Actions role
- `scripts/setup-aws-infrastructure.sh` - Creates S3/DynamoDB backend
- `terraform/environments/dev/backend-dev.hcl` - Backend config
- `.github/workflows/terraform-deploy-dev.yml` - Deploy workflow
- `.github/workflows/terraform-destroy-dev.yml` - Destroy workflow

## Need Help?

See detailed documentation: `terraform/GITHUB_ACTIONS_SETUP.md`
