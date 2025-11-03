#!/bin/bash
set -e

# Setup AWS infrastructure for Terraform backend
# This script creates:
# 1. S3 bucket for Terraform state
# 2. DynamoDB table for state locking

PROJECT_NAME="sleap-rtc"
AWS_REGION="us-west-1"

echo "Setting up AWS infrastructure for $PROJECT_NAME..."

# Verify AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "Error: AWS CLI is not installed"
    exit 1
fi

# Verify AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "Error: AWS credentials are not configured"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Using AWS Account: $ACCOUNT_ID"

# Verify we're using the correct account
if [ "$ACCOUNT_ID" != "711387140753" ]; then
    echo "WARNING: Expected account 711387140753 but got $ACCOUNT_ID"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create S3 bucket for Terraform state
BUCKET_NAME="${PROJECT_NAME}-terraform-state-${ACCOUNT_ID}"
echo "Creating S3 bucket: $BUCKET_NAME..."

if aws s3 ls "s3://${BUCKET_NAME}" 2>&1 | grep -q 'NoSuchBucket'; then
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$AWS_REGION" \
        --create-bucket-configuration LocationConstraint="$AWS_REGION"

    # Enable versioning
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled

    echo "✓ S3 bucket created with versioning enabled"
else
    echo "✓ S3 bucket already exists"
fi

# Create DynamoDB table for state locking
TABLE_NAME="${PROJECT_NAME}-terraform-locks"
echo "Creating DynamoDB table: $TABLE_NAME..."

if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &> /dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"

    echo "✓ DynamoDB table created"
else
    echo "✓ DynamoDB table already exists"
fi

echo ""
echo "Infrastructure setup complete!"
echo ""
echo "Next steps:"
echo "1. Update terraform backend configuration with:"
echo "   bucket  = \"$BUCKET_NAME\""
echo "   region  = \"$AWS_REGION\""
echo "   dynamodb_table = \"$TABLE_NAME\""
echo ""
echo "2. Run: terraform init -backend-config=backend-dev.hcl"
