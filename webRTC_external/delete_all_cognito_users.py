#!/usr/bin/env python3
"""Delete all users from a Cognito user pool.

Required IAM Permissions:
-------------------------
Your AWS credentials must have these permissions on the target user pool:

    cognito-idp:ListUsers        - To enumerate all users in the pool
    cognito-idp:AdminDeleteUser  - To delete each user

Example IAM policy:
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "cognito-idp:ListUsers",
                "cognito-idp:AdminDeleteUser"
            ],
            "Resource": "arn:aws:cognito-idp:REGION:ACCOUNT_ID:userpool/POOL_ID"
        }
    ]
}

Environment Variables:
----------------------
    COGNITO_USER_POOL_ID  - The user pool ID (required)
    COGNITO_REGION        - AWS region (default: us-east-1)

Usage:
------
    # Dry run (default) - shows what would be deleted
    python delete_all_cognito_users.py

    # Actually delete users
    python delete_all_cognito_users.py --confirm
"""

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


def get_all_users(client, user_pool_id: str) -> list[dict]:
    """List all users in the Cognito user pool with pagination."""
    users = []
    pagination_token = None

    while True:
        kwargs = {
            "UserPoolId": user_pool_id,
            "Limit": 60,  # Max allowed by API
        }
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token

        response = client.list_users(**kwargs)
        users.extend(response.get("Users", []))

        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break

    return users


def delete_user(client, user_pool_id: str, username: str) -> bool:
    """Delete a single user from the pool. Returns True if successful."""
    try:
        client.admin_delete_user(
            UserPoolId=user_pool_id,
            Username=username,
        )
        return True
    except client.exceptions.UserNotFoundException:
        print(f"  [SKIP] User {username} not found (already deleted)")
        return True
    except ClientError as e:
        print(f"  [ERROR] Failed to delete {username}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Delete all users from a Cognito user pool"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete users (without this flag, runs in dry-run mode)",
    )
    parser.add_argument(
        "--pool-id",
        default=os.environ.get("COGNITO_USER_POOL_ID"),
        help="Cognito user pool ID (default: $COGNITO_USER_POOL_ID)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("COGNITO_REGION", "us-east-1"),
        help="AWS region (default: $COGNITO_REGION or us-east-1)",
    )
    args = parser.parse_args()

    if not args.pool_id:
        print("ERROR: No user pool ID provided.")
        print("Set COGNITO_USER_POOL_ID env var or use --pool-id")
        sys.exit(1)

    client = boto3.client("cognito-idp", region_name=args.region)

    print(f"User Pool: {args.pool_id}")
    print(f"Region: {args.region}")
    print()

    # List all users
    print("Fetching users...")
    try:
        users = get_all_users(client, args.pool_id)
    except ClientError as e:
        print(f"ERROR: Failed to list users: {e}")
        print("\nMake sure you have cognito-idp:ListUsers permission.")
        sys.exit(1)

    if not users:
        print("No users found in the pool.")
        return

    print(f"Found {len(users)} user(s)")
    print()

    if not args.confirm:
        print("=" * 50)
        print("DRY RUN - No users will be deleted")
        print("Run with --confirm to actually delete")
        print("=" * 50)
        print()
        print("Users that would be deleted:")
        for user in users:
            username = user["Username"]
            created = user.get("UserCreateDate", "unknown")
            status = user.get("UserStatus", "unknown")
            print(f"  - {username} (status: {status}, created: {created})")
        return

    # Confirm deletion
    print("=" * 50)
    print(f"WARNING: About to delete {len(users)} users!")
    print("=" * 50)
    response = input("Type 'DELETE ALL' to confirm: ")
    if response != "DELETE ALL":
        print("Aborted.")
        sys.exit(1)

    print()
    print("Deleting users...")
    deleted = 0
    failed = 0

    for i, user in enumerate(users, 1):
        username = user["Username"]
        print(f"[{i}/{len(users)}] Deleting {username}...")

        if delete_user(client, args.pool_id, username):
            deleted += 1
        else:
            failed += 1

        # Rate limiting - Cognito has API limits
        if i % 10 == 0:
            time.sleep(0.5)

    print()
    print("=" * 50)
    print(f"Completed: {deleted} deleted, {failed} failed")
    print("=" * 50)


if __name__ == "__main__":
    main()
