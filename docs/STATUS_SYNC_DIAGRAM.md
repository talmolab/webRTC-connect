# Worker Status Synchronization - Visual Flow

## Scenario: Two Clients, One Worker

```
Time    Worker-A               Signaling Server           Client-1          Client-2
        (16GB GPU)             (ROOMS dict)               (Researcher)      (Researcher)
─────────────────────────────────────────────────────────────────────────────────────────

T+0     register               ROOMS = {
        status: available        "room": {
                ────────────────►   "peers": {
                                      "Worker-A": {
                                        status: "available"
                                      }
                                    }
                                  }
                                }

T+10                                                       register
                                                           ───────────────►

T+11                                                                         register
                                                                             ──────────►

T+15                           ROOMS = {
                                 "room": {
                                   "peers": {
                                     "Worker-A": {status: "available"},
                                     "Client-1": {...},
                                     "Client-2": {...}
                                   }
                                 }
                               }

T+20                                                       discover_peers
                                                           filters: status="available"
                                                           ───────────────►
                                                                            peer_list:
                                                                            [Worker-A]
                                                           ◄───────────────

T+25                                                       job_request
                                                           ───────────────► forward
                              ◄────────────────────────────────────────────
                job_request
        ◄───────


T+26    job_response:
        accepted=true
                ────────────────►  forward
                                                           ◄───────────────
                                                           (accepted!)

T+27    update_metadata:                                                   discover_peers
        status: "busy"                                                     filters: status="available"
                ────────────────►                                          ───────────────►

T+28                           ROOMS = {                                   peer_list: []
                                 "room": {                                 (Worker-A filtered out!)
                                   "peers": {                              ◄───────────────
                                     "Worker-A": {
                                       status: "busy" ◄──
                                     }
                                   }
                                 }
                               }

T+30    [Training job                                                      (no workers found)
         running...]                                                       (waits or retries)



T+120   [Job complete]

T+121   update_metadata:
        status: "available"
                ────────────────►

T+122                          ROOMS = {
                                 "room": {
                                   "peers": {
                                     "Worker-A": {
                                       status: "available" ◄─
                                     }
                                   }
                                 }
                               }

T+130                                                                      discover_peers
                                                                           filters: status="available"
                                                                           ───────────────►

T+131                                                                      peer_list:
                                                                           [Worker-A]
                                                                           ◄───────────────
                                                                           (Worker-A available again!)
```

---

## Key Timing Points

### ✅ T+27: Status Update BEFORE Client-2 Discovers

Worker-A updates status to "busy" **immediately** after accepting job from Client-1.

When Client-2 discovers at T+27, Worker-A is already marked busy and filtered out.

### ✅ T+122: Status Update When Job Completes

Worker-A updates status back to "available" after job completion.

Future clients can now discover Worker-A again.

---

## Race Condition Analysis

### What if Client-2 discovers at T+26.5?

```
T+26.0   Worker-A responds: accepted=true
T+26.5   Client-2 discovers ← Discovers Worker-A as "available"
T+27.0   Worker-A updates: status="busy"
```

**Scenario:** Client-2 gets stale data showing Worker-A as available.

**Mitigation 1: Optimistic Rejection**
```python
# Worker-A receives job_request from Client-2
if self.status == "busy":
    # Already accepted another job
    await self.respond(accepted=False, reason="busy")
```

**Mitigation 2: Update Status Earlier**
```python
# Option: Update status BEFORE responding
await self.update_metadata({"properties": {"status": "reserved"}})
await self.respond_job_request(accepted=True)
```

**Result:** Race window reduced from ~1000ms to ~10ms.

---

## Alternative: Reservation System

```
Time    Worker-A               Signaling Server           Client-1

T+25                                                       job_request
                                                           ───────────────►

T+26                           reserve_worker:
                               Worker-A locked for 30s
                                                           ◄───────────────
                                                           (reserved token)

T+27                                                       job_assignment
                                                           (with token)
                                                           ───────────────►

T+28    assigned!
        (start training)
```

**Pros:** Guaranteed exclusive access
**Cons:** Complex, requires server-side timers, lock management

**Verdict:** Not needed. Metadata updates + optimistic rejection sufficient.

---

## Multi-Worker Scenario

```
Worker-A: status="available", GPU=16GB
Worker-B: status="available", GPU=24GB
Worker-C: status="busy", GPU=32GB

Client-1 discovers:
  filters: {status: "available", gpu >= 16GB}
  → Returns: [Worker-A, Worker-B]  (Worker-C filtered out)

Client-1 sends job_request to both:
  → Worker-A responds: accepted, 45min estimate
  → Worker-B responds: accepted, 38min estimate

Client-1 selects Worker-B (faster)

Client-1 sends job_assignment to Worker-B
Worker-B updates: status="busy"

Client-2 discovers:
  filters: {status: "available", gpu >= 16GB}
  → Returns: [Worker-A]  (Worker-B now busy, Worker-C still busy)

Client-2 assigns to Worker-A
Worker-A updates: status="busy"

Client-3 discovers:
  filters: {status: "available", gpu >= 16GB}
  → Returns: []  (all workers busy)
  → Client-3 waits or shows "no workers available" error
```

---

## Status Update Message Format

### Worker → Server
```json
{
  "type": "update_metadata",
  "peer_id": "Worker-A-GPU1",
  "metadata": {
    "properties": {
      "status": "busy",
      "current_job_id": "job-uuid-789",
      "job_start_time": "2025-11-03T10:30:00Z"
    }
  }
}
```

### Server → Worker (Confirmation)
```json
{
  "type": "metadata_updated",
  "peer_id": "Worker-A-GPU1",
  "metadata": {
    "tags": ["sleap-rtc", "training-worker"],
    "properties": {
      "status": "busy",
      "current_job_id": "job-uuid-789",
      "job_start_time": "2025-11-03T10:30:00Z",
      "gpu_memory_mb": 16384,
      ...
    }
  }
}
```

**Note:** Server **merges** properties. Only specified properties are updated; others remain unchanged.

---

## Server State Transitions

### Worker Registration
```python
ROOMS["room"]["peers"]["Worker-A"] = {
    "websocket": <ws>,
    "role": "worker",
    "metadata": {
        "properties": {
            "status": "available",
            "gpu_memory_mb": 16384
        }
    },
    "connected_at": 1699120000
}
```

### After update_metadata (status → busy)
```python
ROOMS["room"]["peers"]["Worker-A"]["metadata"]["properties"] = {
    "status": "busy",           # ← Updated
    "current_job_id": "job-789", # ← Added
    "gpu_memory_mb": 16384      # ← Preserved
}
```

### After update_metadata (status → available)
```python
ROOMS["room"]["peers"]["Worker-A"]["metadata"]["properties"] = {
    "status": "available",       # ← Updated
    "current_job_id": None,      # ← Cleared
    "gpu_memory_mb": 16384       # ← Preserved
}
```

---

## Implementation Checklist

### Signaling Server (✅ Done)
- [x] Add `handle_update_metadata()` function
- [x] Wire into message handler
- [x] Merge logic for properties
- [x] Return confirmation message

### sleap-rtc Worker (Your TODO)
- [ ] Add `update_metadata()` method
- [ ] Call when accepting job: `status="busy"`
- [ ] Call when completing job: `status="available"`
- [ ] Handle errors gracefully

### sleap-rtc Client (Your TODO)
- [ ] Add `status="available"` to discovery filters
- [ ] Handle empty peer list (no workers available)
- [ ] Optionally: poll/retry if no workers found

---

## Testing

### Test 1: Basic Status Update
```python
# Worker updates status
await worker.update_metadata({
    "properties": {"status": "busy"}
})

# Verify server state
assert ROOMS["room"]["peers"]["Worker-A"]["metadata"]["properties"]["status"] == "busy"

# Discovery should not return this worker
peers = await client.discover_peers(filters={"properties": {"status": "available"}})
assert "Worker-A" not in [p["peer_id"] for p in peers]
```

### Test 2: Race Condition
```python
# Client-1 and Client-2 discover simultaneously
peers1 = await client1.discover_peers()
peers2 = await client2.discover_peers()

# Both see Worker-A
assert "Worker-A" in [p["peer_id"] for p in peers1]
assert "Worker-A" in [p["peer_id"] for p in peers2]

# Both send job requests
await client1.send_job_request("Worker-A")
await client2.send_job_request("Worker-A")

# Worker-A accepts first request
response1 = await client1.wait_for_response()
assert response1["accepted"] == True

# Worker-A rejects second request
response2 = await client2.wait_for_response()
assert response2["accepted"] == False
assert response2["reason"] == "busy"
```

### Test 3: Status Cleanup on Disconnect
```python
# Worker updates status to busy
await worker.update_metadata({"properties": {"status": "busy"}})

# Worker disconnects (crash)
await worker.disconnect()

# Discovery should not return disconnected worker
peers = await client.discover_peers()
assert "Worker-A" not in [p["peer_id"] for p in peers]
```

---

## Summary

**Central Source of Truth:** Yes, the signaling server's `ROOMS` dictionary.

**Synchronization:** Real-time via `update_metadata` messages.

**Race Conditions:** Mitigated by proactive updates + optimistic rejection.

**Complexity:** Minimal - just add status updates to worker code.
