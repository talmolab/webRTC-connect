# Authentication DynamoDB Tables

This document describes the DynamoDB tables used for SLEAP-RTC authentication.

## Overview

| Table | Purpose | Primary Key | GSIs |
|-------|---------|-------------|------|
| `sleap_users` | GitHub user accounts | `user_id` | `username-index` |
| `sleap_worker_tokens` | API keys + OTP secrets | `token_id` | `user_id-index`, `room_id-index` |
| `sleap_room_memberships` | User ↔ Room authorization | `user_id` + `room_id` | `room_id-index` |

## Table Schemas

### sleap_users

Stores GitHub user accounts after OAuth login.

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | String (PK) | GitHub user ID (numeric, stored as string) |
| `username` | String | GitHub username |
| `email` | String | GitHub email (optional, if user:email scope) |
| `avatar_url` | String | GitHub avatar URL |
| `created_at` | String | ISO 8601 timestamp |
| `last_login` | String | ISO 8601 timestamp |

**Global Secondary Indexes:**
- `username-index`: Query users by GitHub username

**Example Item:**
```json
{
  "user_id": "12345678",
  "username": "researcher1",
  "email": "researcher1@example.com",
  "avatar_url": "https://avatars.githubusercontent.com/u/12345678",
  "created_at": "2024-01-15T10:30:00Z",
  "last_login": "2024-01-20T14:22:00Z"
}
```

---

### sleap_worker_tokens

Stores API keys and TOTP secrets for worker authentication.

| Attribute | Type | Description |
|-----------|------|-------------|
| `token_id` | String (PK) | API key with `slp_` prefix |
| `user_id` | String | Owner's GitHub user ID |
| `room_id` | String | Room this token grants access to |
| `worker_name` | String | Human-readable name for the token |
| `otp_secret` | String | Base32-encoded TOTP secret (160-bit) |
| `created_at` | String | ISO 8601 timestamp |
| `expires_at` | String | ISO 8601 timestamp (null = never) |
| `revoked_at` | String | ISO 8601 timestamp (null = active) |

**Global Secondary Indexes:**
- `user_id-index`: List all tokens owned by a user
- `room_id-index`: List all tokens for a room

**Example Item:**
```json
{
  "token_id": "slp_dGhpcyBpcyBhIHRlc3QgdG9rZW4gZm9y",
  "user_id": "12345678",
  "room_id": "a1b2c3d4",
  "worker_name": "lab-gpu-1",
  "otp_secret": "JBSWY3DPEHPK3PXP4WTNKFQW5ZJMHQ2T",
  "created_at": "2024-01-15T10:30:00Z",
  "expires_at": "2024-01-22T10:30:00Z",
  "revoked_at": null
}
```

**Token Lifecycle:**
1. Created → `revoked_at` is null, `expires_at` may be set
2. Active → Current time < `expires_at` (if set) AND `revoked_at` is null
3. Expired → Current time > `expires_at`
4. Revoked → `revoked_at` is not null

---

### sleap_room_memberships

Tracks which users have access to which rooms.

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | String (PK) | GitHub user ID |
| `room_id` | String (SK) | Room ID |
| `role` | String | `owner` or `member` |
| `invited_by` | String | User ID who invited (null for owner) |
| `joined_at` | String | ISO 8601 timestamp |

**Global Secondary Indexes:**
- `room_id-index`: List all members of a room

**Example Items:**
```json
{
  "user_id": "12345678",
  "room_id": "a1b2c3d4",
  "role": "owner",
  "invited_by": null,
  "joined_at": "2024-01-15T10:30:00Z"
}
```

```json
{
  "user_id": "87654321",
  "room_id": "a1b2c3d4",
  "role": "member",
  "invited_by": "12345678",
  "joined_at": "2024-01-16T09:00:00Z"
}
```

---

## Setup

Run the setup script to create these tables:

```bash
cd webRTC-connect
./scripts/setup-auth-tables.sh
```

The script:
1. Verifies AWS credentials
2. Creates tables with proper indexes
3. Uses PAY_PER_REQUEST billing (no capacity planning needed)
4. Waits for tables to become active

## Query Patterns

### User Operations
```python
# Get user by ID
users_table.get_item(Key={"user_id": "12345678"})

# Get user by username
users_table.query(
    IndexName="username-index",
    KeyConditionExpression=Key("username").eq("researcher1")
)
```

### Token Operations
```python
# Validate API key
tokens_table.get_item(Key={"token_id": "slp_xxx..."})

# List user's tokens
tokens_table.query(
    IndexName="user_id-index",
    KeyConditionExpression=Key("user_id").eq("12345678")
)

# List tokens for a room
tokens_table.query(
    IndexName="room_id-index",
    KeyConditionExpression=Key("room_id").eq("a1b2c3d4")
)
```

### Membership Operations
```python
# Check user's room access
memberships_table.get_item(Key={"user_id": "12345678", "room_id": "a1b2c3d4"})

# List user's rooms
memberships_table.query(KeyConditionExpression=Key("user_id").eq("12345678"))

# List room members
memberships_table.query(
    IndexName="room_id-index",
    KeyConditionExpression=Key("room_id").eq("a1b2c3d4")
)
```
