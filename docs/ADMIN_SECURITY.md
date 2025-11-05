# Admin Dashboard Security Architecture

## Overview

This document addresses security considerations for admin visibility into rooms, workers, and peers.

---

## Security Principles

### 1. **Principle of Least Privilege**
Admins should only see what's necessary for system operations, not user data.

### 2. **Data Privacy**
User-identifiable information should be protected even from admins.

### 3. **Audit Trail**
All admin actions must be logged for accountability.

---

## What Should Admins See?

### ✅ Safe to Expose (System Health)

```json
{
  "system_metrics": {
    "active_connections": 1247,
    "active_rooms": 42,
    "total_peers": 1289,
    "peers_by_role": {
      "worker": 15,
      "client": 27
    },
    "server_instances": [
      {
        "server_id": "i-0083248104cb64d08",
        "connections": 623,
        "cpu_usage": 45.2,
        "memory_usage": 67.8,
        "uptime_hours": 48
      }
    ],
    "message_throughput": {
      "messages_per_second": 120,
      "average_latency_ms": 15
    }
  }
}
```

**Why it's safe:** Aggregate statistics without identifying individual users.

---

### ✅ Safe to Expose (Room Metadata - Anonymized)

```json
{
  "rooms": [
    {
      "room_id": "room_abc123",
      "created_at": "2025-11-03T10:30:00Z",
      "expires_at": "2025-11-03T12:30:00Z",
      "peer_count": 5,
      "peer_roles": {
        "worker": 3,
        "client": 2
      },
      "message_count": 1250,
      "status": "active"
    }
  ]
}
```

**Why it's safe:** Room-level aggregates without exposing peer identities or payloads.

---

### ⚠️ Requires Authorization (Worker Details)

Some worker information may be useful for debugging but should require authentication:

```json
{
  "workers": [
    {
      "peer_id": "Worker-3108",  // Pseudonymous ID
      "room_id": "room_abc123",
      "status": "busy",
      "capabilities": {
        "gpu_memory_mb": 16384,
        "model_types": ["base", "centroid"]
      },
      "current_job_count": 1,
      "connected_duration_seconds": 1830,
      "last_heartbeat": "2025-11-03T10:45:00Z"
    }
  ]
}
```

**Authorization required:** Only show to room creator or system admins with valid credentials.

---

### ❌ Never Expose (Privacy Violations)

```json
{
  // NEVER show these:
  "cognito_id_token": "eyJraWQiOiJ...",  // Authentication tokens
  "user_email": "user@example.com",      // Personal identifiable info
  "message_payload": {...},              // User data/communications
  "dataset_path": "/data/user_123/...",  // File paths with usernames
  "ip_address": "192.168.1.100"          // Network identifiers
}
```

**Why it's dangerous:** Violates user privacy, exposes credentials, enables impersonation.

---

## Admin API Implementation

### File: `app/admin.py`

```python
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Dict, Any
import boto3
from datetime import datetime, timedelta

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBearer()

# Admin authentication using AWS Cognito groups
async def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify user has admin privileges"""
    token = credentials.credentials

    # Verify JWT token with Cognito
    cognito_client = boto3.client('cognito-idp', region_name='us-west-1')

    try:
        response = cognito_client.get_user(AccessToken=token)

        # Check if user is in 'admins' group
        groups = response.get('UserAttributes', [])
        user_groups = [g['Value'] for g in groups if g['Name'] == 'cognito:groups']

        if 'admins' not in user_groups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required"
            )

        return response['Username']

    except cognito_client.exceptions.NotAuthorizedException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

# Audit logging
async def audit_log(admin_username: str, action: str, details: dict):
    """Log all admin actions"""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "admin": admin_username,
        "action": action,
        "details": details,
        "source_ip": "REDACTED"  # Could add request.client.host if needed
    }

    # Store in CloudWatch Logs
    logs_client = boto3.client('logs', region_name='us-west-1')
    logs_client.put_log_events(
        logGroupName='/aws/ec2/sleap-rtc-admin-audit',
        logStreamName=datetime.utcnow().strftime('%Y-%m-%d'),
        logEvents=[{
            'timestamp': int(datetime.utcnow().timestamp() * 1000),
            'message': json.dumps(log_entry)
        }]
    )

@router.get("/metrics")
async def get_system_metrics(admin: str = Depends(verify_admin)):
    """Get system-wide metrics (no privacy concerns)"""
    await audit_log(admin, "view_metrics", {})

    # Aggregate from Redis
    total_rooms = await redis_state.redis.dbsize()  # Approximate
    all_room_keys = await redis_state.redis.keys("room:*:peers")

    total_peers = 0
    roles_count = {"worker": 0, "client": 0, "other": 0}

    for room_key in all_room_keys:
        peers = await redis_state.redis.hgetall(room_key)
        total_peers += len(peers)

        for peer_data in peers.values():
            peer_info = json.loads(peer_data.decode())
            role = peer_info.get("role", "other")
            roles_count[role] = roles_count.get(role, 0) + 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "active_rooms": len(all_room_keys),
        "total_peers": total_peers,
        "peers_by_role": roles_count,
        "server_id": redis_state.server_id
    }

@router.get("/rooms")
async def list_rooms(admin: str = Depends(verify_admin)) -> List[Dict[str, Any]]:
    """List all rooms with anonymized metadata"""
    await audit_log(admin, "list_rooms", {})

    all_room_keys = await redis_state.redis.keys("room:*:peers")
    rooms = []

    for room_key in all_room_keys:
        room_id = room_key.decode().split(":")[1]
        peers = await redis_state.get_room_peers(room_id)

        # Aggregate role counts (no individual peer IDs)
        role_counts = {}
        for peer_data in peers.values():
            role = peer_data.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        # Get room metadata
        ttl = await redis_state.redis.ttl(room_key)

        rooms.append({
            "room_id": room_id,  # Pseudonymous ID is OK
            "peer_count": len(peers),
            "peer_roles": role_counts,
            "ttl_seconds": ttl,
            "expires_at": (datetime.utcnow() + timedelta(seconds=ttl)).isoformat()
        })

    return rooms

@router.get("/rooms/{room_id}/workers")
async def get_room_workers(
    room_id: str,
    admin: str = Depends(verify_admin)
) -> List[Dict[str, Any]]:
    """Get worker details for a specific room (requires admin auth)"""
    await audit_log(admin, "view_room_workers", {"room_id": room_id})

    peers = await redis_state.get_room_peers(room_id)

    # Filter to workers only
    workers = []
    for peer_id, peer_data in peers.items():
        if peer_data.get("role") != "worker":
            continue

        # Return limited worker info (no sensitive data)
        workers.append({
            "peer_id": peer_id,  # Pseudonymous ID (Worker-3108)
            "status": peer_data.get("metadata", {}).get("properties", {}).get("status", "unknown"),
            "capabilities": peer_data.get("metadata", {}).get("properties", {}),
            "connected_at": peer_data.get("connected_at"),
            "connected_duration_seconds": int(time.time() - peer_data.get("connected_at", 0))
        })

    return workers

@router.delete("/rooms/{room_id}")
async def force_close_room(
    room_id: str,
    admin: str = Depends(verify_admin)
):
    """Force close a room (emergency use only)"""
    await audit_log(admin, "force_close_room", {"room_id": room_id})

    # Get all peers in room
    peers = await redis_state.get_room_peers(room_id)

    # Send disconnect messages to all peers
    for peer_id in peers.keys():
        try:
            await redis_state.route_message(peer_id, {
                "type": "room_closed",
                "reason": "Closed by administrator",
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.error(f"Failed to notify peer {peer_id}: {e}")

    # Delete room from Redis
    await redis_state.redis.delete(f"room:{room_id}:peers")

    return {"status": "closed", "room_id": room_id}

@router.get("/audit-log")
async def get_audit_log(
    admin: str = Depends(verify_admin),
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get admin action audit log"""
    await audit_log(admin, "view_audit_log", {"limit": limit})

    # Fetch from CloudWatch Logs
    logs_client = boto3.client('logs', region_name='us-west-1')

    response = logs_client.filter_log_events(
        logGroupName='/aws/ec2/sleap-rtc-admin-audit',
        limit=limit,
        startTime=int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000)
    )

    return [json.loads(event['message']) for event in response.get('events', [])]
```

---

## Admin Dashboard UI (Optional)

### Security Features

1. **Authentication:**
   - Require AWS Cognito login
   - Multi-factor authentication (MFA) for admin accounts
   - Session timeout after 30 minutes

2. **Authorization:**
   - Role-based access control (RBAC)
   - Separate roles: `viewer` (read-only), `admin` (full access)

3. **Rate Limiting:**
   ```python
   from slowapi import Limiter
   from slowapi.util import get_remote_address

   limiter = Limiter(key_func=get_remote_address)

   @router.get("/metrics")
   @limiter.limit("10/minute")  # Max 10 requests per minute
   async def get_system_metrics(...):
       ...
   ```

4. **IP Allowlisting:**
   ```python
   ALLOWED_ADMIN_IPS = os.getenv("ADMIN_IP_ALLOWLIST", "").split(",")

   async def verify_admin_ip(request: Request):
       client_ip = request.client.host
       if client_ip not in ALLOWED_ADMIN_IPS:
           raise HTTPException(status_code=403, detail="Access denied")
   ```

---

## Privacy-Preserving Analytics

### What if you need more detailed insights?

Use **differential privacy** to analyze usage patterns without exposing individual users:

```python
@router.get("/analytics/worker-utilization")
async def get_worker_utilization(admin: str = Depends(verify_admin)):
    """Get anonymized worker utilization statistics"""

    # Aggregate worker activity over time buckets
    now = datetime.utcnow()
    buckets = []

    for i in range(24):  # Last 24 hours
        bucket_start = now - timedelta(hours=i+1)
        bucket_end = now - timedelta(hours=i)

        # Count workers active during this time bucket
        # (no individual worker IDs stored)
        worker_count = await redis_state.redis.get(
            f"analytics:worker_count:{bucket_start.strftime('%Y%m%d%H')}"
        )

        buckets.append({
            "hour": bucket_start.strftime('%Y-%m-%d %H:00'),
            "active_workers": int(worker_count or 0)
        })

    return {"utilization_by_hour": buckets}
```

---

## Comparison: Admin Visibility Levels

| Information | Public API | Authenticated User | Room Creator | System Admin |
|-------------|-----------|-------------------|--------------|--------------|
| System uptime | ✅ | ✅ | ✅ | ✅ |
| Total connection count | ✅ | ✅ | ✅ | ✅ |
| Own room details | ❌ | ✅ | ✅ | ✅ |
| Worker metadata in own room | ❌ | ❌ | ✅ | ✅ |
| All room list | ❌ | ❌ | ❌ | ✅ |
| Worker details in any room | ❌ | ❌ | ❌ | ✅ |
| Force close room | ❌ | ❌ | ❌ | ✅ |
| Audit log | ❌ | ❌ | ❌ | ✅ |
| User tokens/credentials | ❌ | ❌ | ❌ | ❌ |
| Message payloads | ❌ | ❌ | ❌ | ❌ |

---

## GDPR / Compliance Considerations

### Data Retention

```python
# Automatically expire sensitive data
ROOM_TTL = 7200  # 2 hours
AUDIT_LOG_RETENTION_DAYS = 90  # 3 months

# Delete old audit logs
@app.on_event("startup")
async def cleanup_old_logs():
    while True:
        await asyncio.sleep(86400)  # Daily

        cutoff = datetime.utcnow() - timedelta(days=AUDIT_LOG_RETENTION_DAYS)
        # Delete logs older than retention period
        # ... (implementation)
```

### User Rights

Provide API endpoints for users to:
1. **Export their data** (rooms they created, connections made)
2. **Delete their data** (close all rooms, clear metadata)
3. **Opt-out of analytics** (if you add any user-level tracking)

---

## Recommendations

### For Small Lab (5-10 users)
- **Admin Dashboard:** Not needed initially
- **Monitoring:** Basic CloudWatch metrics
- **Access:** Team leads can view aggregate stats via public `/metrics` endpoint

### For Research Team (50-100 users)
- **Admin Dashboard:** Simple read-only dashboard
- **Authentication:** Cognito with `admins` group
- **Visibility:** System metrics + room list (no individual peer details)

### For Enterprise (1000+ users)
- **Admin Dashboard:** Full-featured with RBAC
- **Authentication:** SSO with MFA required
- **Visibility:** Tiered access (viewer vs admin)
- **Compliance:** GDPR/HIPAA audit trails
- **Security:** IP allowlisting + rate limiting

---

## Answer to Your Question

> "Would an administrator page compromise security?"

**No, if done correctly:**
- ✅ Show aggregate metrics (connection counts, room counts)
- ✅ Show room metadata (peer counts by role)
- ✅ Require authentication for any detailed views
- ✅ Log all admin actions for accountability
- ❌ Never show user credentials, message payloads, or PII

**Recommended approach:**
Start with **minimal visibility** (system health only), then add more detailed views as needed with appropriate authentication.

The key is **anonymization** - admins can see that "Room abc123 has 3 workers and 2 clients" without knowing who those users are or what data they're processing.
