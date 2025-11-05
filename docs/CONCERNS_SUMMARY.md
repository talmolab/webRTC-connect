# Addressing Your Concerns - Summary

## Your Questions

1. **Does a room require a "central source" of synchronousity?**
   - Specifically: Worker status updates so they don't accept jobs when busy?

2. **What about room creation and deletion?**
   - Room TTL (~2 hours) might be too short
   - Anonymous Cognito users accumulate during testing

---

## Answer 1: Worker Status - YES, Central Source Exists

### The Central Source

**The signaling server's `ROOMS` dictionary** is the central source of truth.

```python
# Server-side state (in memory)
ROOMS = {
    "room-abc123": {
        "peers": {
            "Worker-A": {
                "websocket": <WebSocket>,
                "role": "worker",
                "metadata": {
                    "properties": {
                        "status": "available"  # ← Central truth
                    }
                },
                "connected_at": 1699120000
            }
        }
    }
}
```

### How Synchronization Works

#### Step 1: Worker Accepts Job
```python
# In sleap-rtc worker code
async def on_job_accepted(self, job_id):
    # Update central state immediately
    await self.signaling_ws.send(json.dumps({
        "type": "update_metadata",
        "peer_id": self.peer_id,
        "metadata": {
            "properties": {"status": "busy"}
        }
    }))
```

#### Step 2: Server Updates State
```python
# Server updates ROOMS dict (server.py)
ROOMS[room_id]["peers"]["Worker-A"]["metadata"]["properties"]["status"] = "busy"
```

#### Step 3: Future Discoveries See Updated State
```python
# Client-2 discovers workers
await client2.discover_peers(filters={
    "role": "worker",
    "properties": {"status": "available"}
})

# Server filters out Worker-A (status is "busy")
# Returns: [] or [other available workers]
```

### Is This Synchronous?

**Yes**, from the server's perspective:
- Worker sends `update_metadata` → Server updates `ROOMS` dict immediately
- Server responds with confirmation before processing next message
- Any subsequent discovery sees the updated state

**Timing:**
```
T+0ms:  Worker accepts job
T+5ms:  Worker sends update_metadata
T+10ms: Server updates ROOMS dict
T+11ms: Server responds: metadata_updated
T+15ms: Client-2 discovers → Worker-A filtered out ✓
```

**Race window:** ~10-15ms (acceptable)

---

## Answer 2: Room & Cleanup Management

### Current Architecture

**Room Creation:**
```http
POST /anonymous-signin → get id_token
POST /create-room (with id_token) → get room_id + token
```

**Room Deletion:**
- **Automatic:** 2-hour TTL (DynamoDB)
- **Manual:** `/delete-peers-and-room` endpoint
- **On disconnect:** When last peer leaves (server.py)

### Recommended Approach

#### For Production Use

**Create one persistent room per lab/team:**

```bash
# Lab admin creates shared room (one-time setup)
python scripts/create_lab_room.py --name "talmolab-gpu-pool"

# Output:
Room created successfully!
Room ID: a7f3d2e1
Token: f9g2h5
Expires: 2025-11-05 12:30:00 UTC (2 hours)

Add these to your worker/client configs:
  SLEAP_RTC_ROOM_ID="a7f3d2e1"
  SLEAP_RTC_ROOM_TOKEN="f9g2h5"
```

**All workers and clients join this shared room:**
```bash
# Worker 1 startup
export SLEAP_RTC_ROOM_ID="a7f3d2e1"
export SLEAP_RTC_ROOM_TOKEN="f9g2h5"
sleap-rtc worker --gpu 0

# Worker 2 startup (same room)
sleap-rtc worker --gpu 1

# Client submits job (same room)
sleap-rtc train data.slp
```

**Room stays alive as long as workers are connected** (disconnect-based cleanup).

#### For Testing

**Option 1: Test Fixture (Recommended)**
```python
# tests/conftest.py
import pytest
import requests

@pytest.fixture
def test_room():
    """Create and cleanup test room."""
    # Create room
    signin = requests.post("http://localhost:8001/anonymous-signin")
    id_token = signin.json()["id_token"]

    room = requests.post(
        "http://localhost:8001/create-room",
        headers={"Authorization": f"Bearer {id_token}"}
    )

    room_id = room.json()["room_id"]
    token = room.json()["token"]

    yield {"room_id": room_id, "token": token, "id_token": id_token}

    # Cleanup after test
    requests.post(
        "http://localhost:8001/delete-peers-and-room",
        json={"peer_id": "test-peer"}  # Any peer in room triggers cleanup
    )
```

**Option 2: Periodic Cleanup Script**
```bash
# Run after test suite
pytest tests/
python scripts/cleanup_cognito_users.py  # Delete users older than 1 hour
```

### Cognito User Accumulation

**Problem:** Each anonymous sign-in creates a new Cognito user.

**Solution 1: Automatic Cleanup on Disconnect** (Already implemented)
```python
# server.py (enhanced)
finally:
    if cognito_username:
        cognito_client.admin_delete_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=cognito_username
        )
```

**Solution 2: Periodic Cleanup Script**
```python
#!/usr/bin/env python3
# scripts/cleanup_cognito_users.py

import boto3
from datetime import datetime, timedelta

cognito = boto3.client('cognito-idp', region_name='us-west-1')

def cleanup_old_users(hours=24):
    """Delete anonymous users older than N hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    paginator = cognito.get_paginator('list_users')
    deleted = 0

    for page in paginator.paginate(UserPoolId='us-west-1_6SgBicvOm'):
        for user in page['Users']:
            if user['UserCreateDate'].replace(tzinfo=None) < cutoff:
                cognito.admin_delete_user(
                    UserPoolId='us-west-1_6SgBicvOm',
                    Username=user['Username']
                )
                deleted += 1

    print(f"Deleted {deleted} old users")

if __name__ == "__main__":
    cleanup_old_users(hours=1)  # For testing: 1 hour
    # cleanup_old_users(hours=24)  # For production: 24 hours
```

**Add to CI:**
```yaml
# .github/workflows/test.yml
- name: Run tests
  run: pytest tests/

- name: Cleanup test users
  if: always()
  run: python scripts/cleanup_cognito_users.py
```

### Room TTL Concerns

**Problem:** 2-hour TTL might expire during long training jobs.

**Solution Options:**

#### Option A: Extend TTL (Add New Endpoint)
```python
@app.post("/extend-room")
async def extend_room(json_data: dict):
    """Extend room TTL by 2 hours."""
    room_id = json_data.get("room_id")

    # Update DynamoDB
    new_expires = int((datetime.utcnow() + timedelta(hours=2)).timestamp())
    rooms_table.update_item(
        Key={"room_id": room_id},
        UpdateExpression="SET expires_at = :exp",
        ExpressionAttributeValues={":exp": new_expires}
    )

    # Update in-memory
    if room_id in ROOMS:
        ROOMS[room_id]["expires_at"] = new_expires

    return {"room_id": room_id, "expires_at": new_expires}
```

**Worker calls periodically:**
```python
# In sleap-rtc worker
async def keep_room_alive(self):
    while self.connected:
        await asyncio.sleep(3600)  # Every hour
        await self.extend_room()
```

#### Option B: Disconnect-Based Cleanup (Current)
Room is deleted when last peer disconnects (regardless of TTL).

**Pros:**
- No TTL worries
- Rooms live as long as needed
- Simpler

**Cons:**
- If all workers crash, room persists in DynamoDB until TTL expires
- Requires periodic DynamoDB cleanup for orphaned rooms

**Recommendation:** Use Option B (current behavior) + periodic DynamoDB cleanup.

---

## Summary Table

| Concern | Solution | Implementation |
|---------|----------|----------------|
| **Worker Status Sync** | Real-time metadata updates | ✅ Added `update_metadata` message type |
| **Central Source** | Server's `ROOMS` dict | ✅ Already exists |
| **Race Conditions** | Proactive updates + optimistic rejection | 📝 Worker implements in sleap-rtc |
| **Room Creation** | One shared room per team | 📝 Admin script |
| **Room Lifetime** | Disconnect-based cleanup | ✅ Already implemented |
| **Cognito Cleanup** | Disconnect + periodic script | ✅ Disconnect done, 📝 Script needed |
| **Testing Cleanup** | pytest fixtures | 📝 Add to test suite |

---

## What's Already Done (Signaling Server)

✅ **Central state management** (`ROOMS` dict)
✅ **Metadata update handler** (`handle_update_metadata`)
✅ **Discovery with filtering** (status-aware)
✅ **Disconnect cleanup** (removes peers, deletes empty rooms)
✅ **Backward compatible** (existing clients still work)

---

## What You Need to Do (sleap-rtc)

### In Worker Code
```python
class SleapRTCWorker:
    async def update_status(self, status: str):
        """Update worker status in signaling server."""
        await self.signaling_ws.send(json.dumps({
            "type": "update_metadata",
            "peer_id": self.peer_id,
            "metadata": {
                "properties": {"status": status}
            }
        }))

    async def accept_job(self, job_id):
        """Accept job and update status."""
        # Update status first
        await self.update_status("busy")

        # Then execute
        await self.execute_training(job_id)

        # Update status when done
        await self.update_status("available")
```

### In Client Code
```python
class SleapRTCClient:
    async def find_available_workers(self):
        """Find workers that are currently available."""
        await self.signaling_ws.send(json.dumps({
            "type": "discover_peers",
            "from_peer_id": self.peer_id,
            "filters": {
                "role": "worker",
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {
                    "status": "available",  # ← Key filter
                    "gpu_memory_mb": {"$gte": 8192}
                }
            }
        }))

        response = await self.signaling_ws.recv()
        return json.loads(response)["peers"]
```

### Add Cleanup Script
```python
# scripts/cleanup_cognito_users.py
# (See full implementation in OPERATIONAL_CONCERNS.md)

# Run daily or after tests
python scripts/cleanup_cognito_users.py --older-than 24h
```

---

## Testing Strategy

### Unit Tests
```python
def test_worker_status_update():
    """Test worker can update status."""
    worker.update_status("busy")
    assert server.get_peer_status("Worker-A") == "busy"

def test_discovery_filters_busy_workers():
    """Test discovery excludes busy workers."""
    worker.update_status("busy")
    peers = client.discover_peers(filters={"status": "available"})
    assert "Worker-A" not in [p["peer_id"] for p in peers]
```

### Integration Tests
```python
@pytest.mark.integration
def test_multi_client_scenario():
    """Test two clients competing for same worker."""
    # Both clients discover
    peers1 = client1.discover_peers()
    peers2 = client2.discover_peers()

    # Both see Worker-A
    assert "Worker-A" in peers1 and "Worker-A" in peers2

    # Client-1 assigns job
    client1.assign_job("Worker-A")

    # Worker-A updates status
    # (happens automatically in worker code)

    # Client-2 discovers again
    peers2 = client2.discover_peers()

    # Worker-A no longer available
    assert "Worker-A" not in peers2
```

---

## Next Steps

1. **✅ Done:** Enhanced signaling server with metadata updates
2. **📝 Your TODO:** Implement status updates in sleap-rtc worker
3. **📝 Your TODO:** Add status filter to sleap-rtc client discovery
4. **📝 Your TODO:** Create cleanup script for Cognito users
5. **📝 Your TODO:** Add pytest fixtures for test room cleanup
6. **🔄 Optional:** Add `/extend-room` endpoint for long jobs

---

## Questions?

**Q: Is the `ROOMS` dict durable?**
A: No, it's in-memory. If server restarts, rooms are lost. DynamoDB has persistent copy, but active connections need to re-register.

**Q: What happens if worker updates status while client is discovering?**
A: Depends on timing:
- If update happens before discovery → client sees updated status ✓
- If update happens during discovery → client might see stale status, but worker will reject job

**Q: Should workers send heartbeats?**
A: Not necessary. Disconnect handler cleans up crashed workers automatically.

**Q: Can clients update their own metadata?**
A: Yes! Same `update_metadata` message works for any peer. Could be useful for tracking client activity.

---

See full details in:
- [`docs/OPERATIONAL_CONCERNS.md`](./OPERATIONAL_CONCERNS.md) - Comprehensive solutions
- [`docs/STATUS_SYNC_DIAGRAM.md`](./STATUS_SYNC_DIAGRAM.md) - Visual timing diagrams
