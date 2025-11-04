# Operational Concerns & Solutions

## Concern 1: Worker Status Synchronization

### The Problem

**Scenario:**
```
T+0    Worker-A registers: status="available"
T+10   Client-1 discovers workers → sees Worker-A
T+15   Client-2 discovers workers → ALSO sees Worker-A
T+20   Client-1 assigns job to Worker-A
T+21   Client-2 tries to assign job to Worker-A ← PROBLEM!
```

**Question:** Is there a "central source of truth" for worker availability?

---

### Solution: Real-Time Metadata Updates

**New Message Type: `update_metadata`**

Workers can update their metadata in real-time when their status changes.

#### Implementation

**Step 1: Worker Accepts Job**
```python
# In sleap-rtc worker code
async def handle_job_assignment(self, job_id):
    # Update status to busy
    await self.signaling_ws.send(json.dumps({
        "type": "update_metadata",
        "peer_id": self.peer_id,
        "metadata": {
            "properties": {
                "status": "busy",
                "current_job_id": job_id
            }
        }
    }))

    # Execute training
    await self.execute_training(job_id)
```

**Step 2: Server Updates State**
```python
# Server automatically merges properties
ROOMS[room_id]["peers"]["Worker-A"]["metadata"]["properties"]["status"] = "busy"
```

**Step 3: Future Discoveries Don't See Busy Worker**
```python
# Client-2 discovers workers
await ws.send({
    "type": "discover_peers",
    "filters": {
        "role": "worker",
        "properties": {"status": "available"}  # Worker-A won't match!
    }
})

# Response: Only Worker-B (if available)
```

**Step 4: Worker Completes Job**
```python
# In sleap-rtc worker code
async def on_job_complete(self, job_id):
    # Update status back to available
    await self.signaling_ws.send(json.dumps({
        "type": "update_metadata",
        "peer_id": self.peer_id,
        "metadata": {
            "properties": {
                "status": "available",
                "current_job_id": None
            }
        }
    }))
```

---

### Alternative Approaches (Not Recommended)

#### Option A: Optimistic - Worker Rejects
```python
# Worker receives job request while busy
await self.send_peer_message(client_id, {
    "app_message_type": "job_response",
    "accepted": false,
    "reason": "busy"
})
```

**Pros:** Simple, no server changes needed
**Cons:** Wasted round-trip, poor user experience

#### Option B: Reservation System
```python
# Client reserves worker for 30 seconds
await ws.send({
    "type": "reserve_worker",
    "worker_id": "Worker-A",
    "duration_sec": 30
})
```

**Pros:** Guarantees exclusive access
**Cons:** Complex, requires server-side locks, timeout management

---

### Recommended Pattern

**Use metadata updates for status, optimistic rejection as fallback:**

```python
class SleapRTCWorker:
    async def handle_job_request(self, job_request):
        # Check current status
        if self.status != "available":
            # Fallback: reject if somehow received while busy
            await self.respond_job_request(
                job_request["job_id"],
                accepted=False,
                reason="busy"
            )
            return

        # Accept and immediately update status
        await self.respond_job_request(
            job_request["job_id"],
            accepted=True
        )

        # Update metadata BEFORE waiting for assignment
        await self.update_metadata({"properties": {"status": "reserved"}})

        # Wait for assignment (with timeout)
        assignment = await self.wait_for_assignment(
            job_request["job_id"],
            timeout=10
        )

        if assignment:
            await self.update_metadata({"properties": {"status": "busy"}})
            await self.execute_job(assignment)
        else:
            # Assignment never came, go back to available
            await self.update_metadata({"properties": {"status": "available"}})
```

---

## Concern 2: Room & Cognito User Cleanup

### The Problem

**Cognito User Accumulation:**
```
# Each test run creates new anonymous users
Test 1: user-uuid-001
Test 2: user-uuid-002
Test 3: user-uuid-003
...
Result: 100s of orphaned Cognito users
```

**Room Lifetime Issues:**
- 2-hour TTL might expire during long training jobs
- Rooms aren't deleted when last peer disconnects
- DynamoDB accumulates expired rooms

---

### Solution 1: Graceful Cleanup on Disconnect

**Enhanced Disconnect Handler:**

The signaling server already removes peers from `ROOMS` on disconnect. Let's add Cognito cleanup:

```python
# In server.py (already partially implemented)
async def handle_client(websocket):
    peer_id = None
    cognito_username = None  # Track for cleanup

    try:
        async for message in websocket:
            if msg_type == "register":
                peer_id = data.get('peer_id')
                # Extract username from Cognito token claims
                claims = verify_cognito_token(data.get('id_token'))
                cognito_username = claims.get("cognito:username")
                # ... rest of registration

    finally:
        # Cleanup on disconnect
        if peer_id:
            room_id = PEER_TO_ROOM.get(peer_id)

            # Remove from room
            if room_id and room_id in ROOMS:
                del ROOMS[room_id]["peers"][peer_id]

                # If room is now empty, delete it
                if not ROOMS[room_id]["peers"]:
                    del ROOMS[room_id]

                    # Delete from DynamoDB
                    rooms_table.delete_item(Key={"room_id": room_id})

            del PEER_TO_ROOM[peer_id]

            # Delete Cognito user
            if cognito_username:
                try:
                    cognito_client.admin_delete_user(
                        UserPoolId=COGNITO_USER_POOL_ID,
                        Username=cognito_username
                    )
                    logging.info(f"Deleted Cognito user: {cognito_username}")
                except Exception as e:
                    logging.error(f"Failed to delete Cognito user: {e}")
```

**Result:** Clean disconnects automatically clean up users and rooms.

---

### Solution 2: Periodic Cleanup Script

**For orphaned users that slip through:**

```python
#!/usr/bin/env python3
"""Cleanup orphaned Cognito users from testing."""

import boto3
from datetime import datetime, timedelta

cognito_client = boto3.client('cognito-idp', region_name='us-west-1')
USER_POOL_ID = 'us-west-1_6SgBicvOm'

def cleanup_old_anonymous_users():
    """Delete anonymous users older than 24 hours."""

    # List all users
    paginator = cognito_client.get_paginator('list_users')

    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    deleted_count = 0

    for page in paginator.paginate(UserPoolId=USER_POOL_ID):
        for user in page['Users']:
            username = user['Username']
            created = user['UserCreateDate'].replace(tzinfo=None)

            # Only delete old anonymous users (UUID format)
            if created < cutoff_time:
                try:
                    cognito_client.admin_delete_user(
                        UserPoolId=USER_POOL_ID,
                        Username=username
                    )
                    deleted_count += 1
                    print(f"Deleted: {username} (created {created})")
                except Exception as e:
                    print(f"Failed to delete {username}: {e}")

    print(f"\nDeleted {deleted_count} orphaned users")

if __name__ == "__main__":
    cleanup_old_anonymous_users()
```

**Run as cron job:**
```bash
# Add to crontab (runs daily at 3 AM)
0 3 * * * python3 /path/to/cleanup_cognito_users.py
```

**Or add to CI/CD:**
```yaml
# .github/workflows/cleanup.yml
name: Cleanup Cognito Users
on:
  schedule:
    - cron: '0 3 * * *'  # Daily at 3 AM UTC

jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: us-west-1

      - name: Cleanup old users
        run: |
          python3 -c "
          import boto3
          from datetime import datetime, timedelta
          cognito = boto3.client('cognito-idp')
          # ... cleanup logic
          "
```

---

### Solution 3: Room Lifecycle Management

**Problem:** 2-hour TTL might expire during long jobs.

#### Option A: Extend TTL for Active Rooms

```python
@app.post("/extend-room")
async def extend_room(json_data: dict, authorization: str = Header(...)):
    """Extend room TTL by 2 hours."""

    # Verify token
    token = authorization.replace("Bearer ", "")
    claims = verify_cognito_token(token)

    room_id = json_data.get("room_id")

    # Update DynamoDB TTL
    new_expires_at = int((datetime.utcnow() + timedelta(hours=2)).timestamp())

    rooms_table.update_item(
        Key={"room_id": room_id},
        UpdateExpression="SET expires_at = :new_expiry",
        ExpressionAttributeValues={":new_expiry": new_expires_at}
    )

    # Update in-memory state
    if room_id in ROOMS:
        ROOMS[room_id]["expires_at"] = new_expires_at

    return {"room_id": room_id, "expires_at": new_expires_at}
```

**Worker calls this every hour:**
```python
# In sleap-rtc worker
async def room_keepalive_task(self):
    while True:
        await asyncio.sleep(3600)  # Every hour

        try:
            response = requests.post(
                f"{self.signaling_http}/extend-room",
                headers={"Authorization": f"Bearer {self.id_token}"},
                json={"room_id": self.room_id}
            )
            logging.info(f"Extended room {self.room_id}")
        except Exception as e:
            logging.error(f"Failed to extend room: {e}")
```

#### Option B: Disconnect-Based Cleanup (Simpler)

Instead of time-based expiration, delete room when last peer disconnects:

```python
# Already implemented in server.py finally block
if not ROOMS[room_id]["peers"]:  # Last peer left
    del ROOMS[room_id]
    rooms_table.delete_item(Key={"room_id": room_id})
    logging.info(f"Room {room_id} deleted (no more peers)")
```

**Pros:**
- No TTL worries
- Rooms live as long as needed
- Automatic cleanup

**Cons:**
- If all workers crash, room persists in DynamoDB
- Need periodic DynamoDB cleanup for orphaned rooms

---

### Solution 4: DynamoDB TTL (Automatic Cleanup)

**Enable DynamoDB TTL feature:**

```python
# One-time setup (AWS CLI)
aws dynamodb update-time-to-live \
    --table-name rooms \
    --time-to-live-specification "Enabled=true, AttributeName=expires_at"
```

**How it works:**
- DynamoDB automatically deletes items when `expires_at` timestamp passes
- Happens within 48 hours of expiration (not immediate)
- No cost, no Lambda needed

**For faster cleanup, add Lambda:**
```python
# Lambda triggered hourly by CloudWatch Events
import boto3

def handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('rooms')

    # Scan for expired rooms
    response = table.scan(
        FilterExpression='expires_at < :now',
        ExpressionAttributeValues={':now': int(time.time())}
    )

    # Delete expired rooms
    for item in response['Items']:
        table.delete_item(Key={'room_id': item['room_id']})
        print(f"Deleted expired room: {item['room_id']}")
```

---

## Recommended Implementation Strategy

### Phase 1: Immediate (Minimal Changes)

✅ **Add metadata updates** (already implemented above)
✅ **Enhance disconnect cleanup** (already mostly done)
✅ **Keep current 2-hour TTL**

**Worker updates status:**
```python
# When accepting job
await self.update_metadata({"properties": {"status": "busy"}})

# When completing job
await self.update_metadata({"properties": {"status": "available"}})
```

**Client filters by status:**
```python
# Discovery only finds available workers
await ws.send({
    "type": "discover_peers",
    "filters": {
        "role": "worker",
        "properties": {"status": "available"}
    }
})
```

---

### Phase 2: Testing Improvements (Next Week)

📝 **Add periodic Cognito cleanup script**
- Run daily via cron or GitHub Actions
- Delete users older than 24 hours

📝 **Add `/extend-room` endpoint**
- Workers call every hour to keep room alive
- Prevents long jobs from being interrupted

---

### Phase 3: Production Hardening (Before Scale-Up)

📝 **Enable DynamoDB TTL**
- Automatic cleanup of expired rooms
- No operational overhead

📝 **Add room health monitoring**
- Alert if room count > 100
- Alert if Cognito user count > 1000

📝 **Add graceful shutdown handlers**
- Workers clean up on SIGTERM
- Ensures Cognito users deleted on pod termination

---

## Testing Workflow

### Development (Frequent Testing)

**Before:**
```bash
# Each test run
pytest tests/
# Result: +3 new Cognito users
```

**After:**
```bash
# Run tests with cleanup
pytest tests/
python scripts/cleanup_cognito_users.py  # Delete test users

# Or use test fixture
@pytest.fixture
def signaling_room():
    # Create room
    room = create_test_room()
    yield room
    # Cleanup automatically
    delete_room_and_users(room)
```

### Integration Testing

**Use shared long-lived room:**
```bash
# Create once
export SLEAP_RTC_ROOM_ID="dev-test-room-001"
export SLEAP_RTC_ROOM_TOKEN="testtoken"

# All tests use same room
pytest tests/integration/

# Cleanup manually when done
curl -X POST http://localhost:8001/delete-peers-and-room \
  -d '{"peer_id": "any-peer-in-room"}'
```

---

## Summary

### Concern 1: Worker Status
**Solution:** Real-time metadata updates
- Workers update status when busy/available
- Clients filter by status in discovery
- Server holds centralized state in `ROOMS` dict
- **No race conditions** if workers update proactively

### Concern 2: Cleanup
**Solution:** Multi-layered approach
- **Layer 1:** Graceful disconnect cleanup (immediate)
- **Layer 2:** Periodic script cleanup (daily)
- **Layer 3:** DynamoDB TTL (backup)
- **For testing:** Use test fixtures with cleanup

**Your current architecture supports all of this!** The server already has centralized state in `ROOMS`, we just added the ability to update it.

---

## Questions?

**Q: Is the status update synchronous?**
A: Yes, server updates `ROOMS` immediately before responding. Future discoveries see updated state instantly.

**Q: What if worker crashes without updating status?**
A: Disconnect handler removes worker from `ROOMS`, so discovery won't find it. Status reset happens automatically.

**Q: Can multiple clients assign to same worker simultaneously?**
A: Extremely unlikely. Worker updates status within milliseconds of accepting job. If it happens, worker rejects with "busy" response.

**Q: Do Cognito users need to be deleted?**
A: Not strictly necessary, but they accumulate (10,000 user pool limit). Periodic cleanup prevents hitting limits during testing.
