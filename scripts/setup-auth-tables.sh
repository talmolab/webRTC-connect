#!/bin/bash
set -e

# Setup DynamoDB tables for SLEAP-RTC authentication
# This script creates:
# 1. sleap_users - GitHub user accounts
# 2. sleap_worker_tokens - API keys and OTP secrets
# 3. sleap_room_memberships - User ↔ Room authorization

PROJECT_NAME="sleap-rtc"
AWS_REGION="us-west-1"

echo "Setting up authentication DynamoDB tables for $PROJECT_NAME..."

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
echo "Region: $AWS_REGION"
echo ""

# =============================================================================
# Table 1: sleap_users
# Stores GitHub user accounts
# =============================================================================
TABLE_NAME="sleap_users"
echo "Creating DynamoDB table: $TABLE_NAME..."

if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &> /dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions \
            AttributeName=user_id,AttributeType=S \
            AttributeName=username,AttributeType=S \
        --key-schema \
            AttributeName=user_id,KeyType=HASH \
        --global-secondary-indexes \
            '[{
                "IndexName": "username-index",
                "KeySchema": [{"AttributeName": "username", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}
            }]' \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"

    echo "✓ Table $TABLE_NAME created"

    # Wait for table to be active
    echo "  Waiting for table to become active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$AWS_REGION"
    echo "  ✓ Table is active"
else
    echo "✓ Table $TABLE_NAME already exists"
fi

# =============================================================================
# Table 2: sleap_worker_tokens
# Stores API keys and OTP secrets for workers
# =============================================================================
TABLE_NAME="sleap_worker_tokens"
echo "Creating DynamoDB table: $TABLE_NAME..."

if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &> /dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions \
            AttributeName=token_id,AttributeType=S \
            AttributeName=user_id,AttributeType=S \
            AttributeName=room_id,AttributeType=S \
        --key-schema \
            AttributeName=token_id,KeyType=HASH \
        --global-secondary-indexes \
            '[{
                "IndexName": "user_id-index",
                "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}
            },
            {
                "IndexName": "room_id-index",
                "KeySchema": [{"AttributeName": "room_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}
            }]' \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"

    echo "✓ Table $TABLE_NAME created"

    # Wait for table to be active
    echo "  Waiting for table to become active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$AWS_REGION"
    echo "  ✓ Table is active"
else
    echo "✓ Table $TABLE_NAME already exists"
fi

# =============================================================================
# Table 3: sleap_room_memberships
# Stores user ↔ room authorization with composite key
# =============================================================================
TABLE_NAME="sleap_room_memberships"
echo "Creating DynamoDB table: $TABLE_NAME..."

if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &> /dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions \
            AttributeName=user_id,AttributeType=S \
            AttributeName=room_id,AttributeType=S \
        --key-schema \
            AttributeName=user_id,KeyType=HASH \
            AttributeName=room_id,KeyType=RANGE \
        --global-secondary-indexes \
            '[{
                "IndexName": "room_id-index",
                "KeySchema": [{"AttributeName": "room_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}
            }]' \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"

    echo "✓ Table $TABLE_NAME created"

    # Wait for table to be active
    echo "  Waiting for table to become active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$AWS_REGION"
    echo "  ✓ Table is active"
else
    echo "✓ Table $TABLE_NAME already exists"
fi

echo ""
echo "============================================"
echo "Authentication tables setup complete!"
echo "============================================"
echo ""
echo "Tables created:"
echo "  - sleap_users (PK: user_id, GSI: username-index)"
echo "  - sleap_worker_tokens (PK: token_id, GSI: user_id-index, room_id-index)"
echo "  - sleap_room_memberships (PK: user_id, SK: room_id, GSI: room_id-index)"
echo ""
echo "Table schemas documented in: docs/auth-tables.md"
echo ""
