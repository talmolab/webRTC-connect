# GitHub Actions Deployment Setup

This guide explains how to deploy your Terraform infrastructure using GitHub Actions with OIDC authentication, following the same approach as the lablink-template project.

## Why This Approach?

**Problem:** Your IAM user lacks permissions and updating user permissions is difficult in your organization.

**Solution:** Instead of using personal IAM credentials, GitHub Actions assumes an IAM role to deploy infrastructure. This is:
- Easier to get approved by AWS admins
- More secure (no static credentials)
- The modern standard for CI/CD on AWS

## Overview of Changes

| Old Approach | New Approach |
|--------------|--------------|
| Manual `terraform apply` from laptop | GitHub Actions runs Terraform automatically |
| IAM user credentials | OIDC role assumption (no credentials in repo) |
| Local state file | S3 backend with DynamoDB locking |
| Need personal IAM permissions | Need one-time IAM role creation |

## Prerequisites

- GitHub repository: `talmolab/webRTC-connect`
- AWS account: `490004650932`
- Admin access to AWS (to create OIDC provider and IAM role)

## Step-by-Step Setup

### Step 1: Ask AWS Admin to Create OIDC Provider

If not already created, your AWS admin needs to add the GitHub OIDC provider:

**Via AWS Console:**
1. Go to IAM → Identity providers → Add provider
2. Provider type: OpenID Connect
3. Provider URL: `https://token.actions.githubusercontent.com`
4. Audience: `sts.amazonaws.com`
5. Click "Add provider"

**Via AWS CLI:**
```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

### Step 2: Ask AWS Admin to Create IAM Role

Your AWS admin needs to create a role that GitHub Actions can assume.

**Trust Policy** (save as `github-actions-trust-policy.json`):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::490004650932:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:talmolab/webRTC-connect:*"
        }
      }
    }
  ]
}
```

**Create the role:**
```bash
# Create the role with trust policy
aws iam create-role \
  --role-name sleap-rtc-github-actions-role \
  --assume-role-policy-document file://github-actions-trust-policy.json

# Attach the Terraform policy (from iam-policy-terraform.json)
aws iam put-role-policy \
  --role-name sleap-rtc-github-actions-role \
  --policy-name sleap-rtc-terraform-policy \
  --policy-document file://iam-policy-terraform.json
```

**Save the Role ARN:** You'll need this for GitHub Secrets.
```
arn:aws:iam::490004650932:role/sleap-rtc-github-actions-role
```

### Step 3: Create S3 Backend Resources

Run the setup script to create the S3 bucket and DynamoDB table:

```bash
./scripts/setup-aws-infrastructure.sh
```

This creates:
- S3 bucket: `sleap-rtc-terraform-state-490004650932`
- DynamoDB table: `sleap-rtc-terraform-locks`

**Note:** You need AWS credentials configured locally to run this script. If you can't run it, ask your AWS admin to run it or create these resources manually.

### Step 4: Configure GitHub Secrets

Add these secrets to your GitHub repository:

1. Go to: `https://github.com/talmolab/webRTC-connect/settings/secrets/actions`
2. Click "New repository secret"
3. Add:
   - Name: `AWS_ROLE_ARN`
   - Value: `arn:aws:iam::490004650932:role/sleap-rtc-github-actions-role`
   - Name: `AWS_REGION`
   - Value: `us-west-1`

### Step 5: Test the Deployment

**Option A: Manual trigger (recommended for first test)**

1. Go to: `https://github.com/talmolab/webRTC-connect/actions`
2. Select "Deploy Terraform (Dev)" workflow
3. Click "Run workflow"
4. Select environment: `dev`
5. Click "Run workflow"

Watch the workflow run and check the outputs!

**Option B: Push to trigger**

The workflow automatically runs when you push changes to:
- `terraform/**`
- `.github/workflows/terraform-deploy-dev.yml`

```bash
git add .
git commit -m "feat: add GitHub Actions deployment"
git push
```

### Step 6: Verify Deployment

After the workflow completes:

1. Check the workflow summary for outputs
2. Test the endpoints:
   ```bash
   # Get the IP from workflow output
   curl http://<elastic-ip>:8001/health
   ```

## Daily Workflow

### Deploying Changes

1. Make changes to Terraform files
2. Commit and push to your branch
3. Create a PR
4. After merge to `main`, deployment runs automatically

Or manually trigger from Actions tab.

### Destroying Infrastructure

1. Go to Actions tab
2. Select "Destroy Terraform (Dev)"
3. Click "Run workflow"
4. Type `destroy` to confirm
5. Run workflow

## Troubleshooting

### Error: "Could not assume role"

**Cause:** OIDC provider or role not configured correctly.

**Solution:**
1. Verify OIDC provider exists: AWS Console → IAM → Identity providers
2. Verify role exists and has correct trust policy
3. Check repository name matches exactly: `talmolab/webRTC-connect`

### Error: "Access Denied" when accessing S3/DynamoDB

**Cause:** Role doesn't have permissions for backend resources.

**Solution:** Add these permissions to the IAM role:
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:ListBucket",
    "s3:GetObject",
    "s3:PutObject",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:DeleteItem"
  ],
  "Resource": [
    "arn:aws:s3:::sleap-rtc-terraform-state-490004650932",
    "arn:aws:s3:::sleap-rtc-terraform-state-490004650932/*",
    "arn:aws:dynamodb:us-west-1:490004650932:table/sleap-rtc-terraform-locks"
  ]
}
```

### Error: "Backend initialization required"

**Cause:** Trying to run Terraform locally with old local state.

**Solution:**
```bash
cd terraform/environments/dev
terraform init -backend-config=backend-dev.hcl -migrate-state
```

This migrates your local state to S3.

## Migrating from Local to Remote State

If you already have local Terraform state:

```bash
cd terraform/environments/dev

# Initialize with new backend (will prompt to migrate)
terraform init -backend-config=backend-dev.hcl

# When prompted, type 'yes' to migrate state to S3
```

Your local state will be uploaded to S3 and future operations will use remote state.

## Comparing to LabLink Approach

Your setup is now similar to lablink-template:

| Feature | LabLink | Your Project |
|---------|---------|--------------|
| Authentication | OIDC with GitHub Actions | ✅ Same |
| State Backend | S3 + DynamoDB | ✅ Same |
| Deployment | GitHub Actions workflows | ✅ Same |
| Manual trigger | Yes | ✅ Same |
| Auto-deploy on push | Yes (to `test` branch) | ✅ Yes (to `main` branch) |
| Destroy workflow | Yes with confirmation | ✅ Same |

## Benefits

1. **No more permission issues** - Don't need personal IAM credentials
2. **Audit trail** - All deployments logged in GitHub Actions
3. **Collaboration** - Team members can trigger deployments
4. **Safety** - Terraform plan shown before apply
5. **State locking** - Prevents concurrent modifications
6. **Versioning** - S3 versioning protects against state corruption

## Next Steps

1. Test deployment to dev environment
2. Create similar workflows for production environment
3. Document your Elastic IP addresses
4. Set up monitoring/alerts (optional)
5. Consider adding Terraform drift detection (optional)
