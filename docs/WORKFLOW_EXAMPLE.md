# Multi-Worker Room Workflow Example

## Scenario
- **Worker A** and **Worker B** want to join a room
- **Client** wants to discover workers and select one
- Room: "lab-room-123"

---

## Sequence of Events

### Phase 1: Room Creation

**Who creates the room?** Someone needs to call the HTTP API first.

#### Option A: First Worker Creates Room

**Step 1a: Worker A Creates Room**
```http
POST http://52.9.213.137:8001/anonymous-signin
Content-Type: application/json
{}
```

**Response:**
```json
{
  "id_token": "eyJra...worker-a-token",
  "username": "uuid-worker-a"
}
```

**Step 1b: Worker A Creates Room**
```http
POST http://52.9.213.137:8001/create-room
Authorization: Bearer eyJra...worker-a-token
```

**Response:**
```json
{
  "room_id": "lab-room-123",  // Actually returns 8-char UUID like "a7f3d2e1"
  "token": "abc123"           // 6-char token for joining
}
```

**Worker A shares `room_id` and `token` with Worker B and Client** (via external mechanism: Slack, config file, etc.)

---

#### Option B: External Coordinator Creates Room

More common pattern for managed deployments:

**Step 1: Admin/Coordinator Creates Room**
```http
POST http://52.9.213.137:8001/anonymous-signin
{}
```
```http
POST http://52.9.213.137:8001/create-room
Authorization: Bearer <admin-token>
```

**Response:**
```json
{
  "room_id": "a7f3d2e1",
  "token": "f9g2h5"
}
```

**Admin distributes room credentials:**
- **Workers**: Get `room_id` + `token` via environment variables or config file
- **Client**: Gets `room_id` + `token` via CLI args or config

---

### Phase 2: Worker Registration

#### Step 2a: Worker A Registers

**Worker A connects to WebSocket:**
```
ws://52.9.213.137:8080/
```

**Worker A gets own auth token:**
```http
POST http://52.9.213.137:8001/anonymous-signin
{}
```
Response: `{"id_token": "eyJ...worker-a", "username": "worker-a-uuid"}`

**Worker A registers with room:**
```json
{
  "type": "register",
  "peer_id": "Worker-A-GPU1",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "id_token": "eyJ...worker-a",
  "role": "worker",
  "metadata": {
    "tags": ["sleap-rtc", "training-worker", "inference-worker"],
    "properties": {
      "gpu_memory_mb": 16384,
      "gpu_model": "NVIDIA RTX 4090",
      "sleap_version": "1.3.0",
      "cuda_version": "11.8",
      "hostname": "gpu-server-01.lab.edu",
      "status": "available",
      "max_concurrent_jobs": 1,
      "supported_models": ["base", "centroid", "topdown"]
    }
  }
}
```

**Server validates and responds:**
```json
{
  "type": "registered_auth",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "peer_id": "Worker-A-GPU1"
}
```

**Server state after Worker A joins:**
```python
ROOMS = {
    "a7f3d2e1": {
        "created_by": "admin-cognito-id",
        "token": "f9g2h5",
        "expires_at": 1699127200,  # 2 hours from creation
        "peers": {
            "Worker-A-GPU1": {
                "websocket": <WebSocket object>,
                "role": "worker",
                "metadata": {
                    "tags": ["sleap-rtc", "training-worker", "inference-worker"],
                    "properties": {
                        "gpu_memory_mb": 16384,
                        "gpu_model": "NVIDIA RTX 4090",
                        "sleap_version": "1.3.0",
                        "status": "available",
                        ...
                    }
                },
                "connected_at": 1699120000
            }
        }
    }
}

PEER_TO_ROOM = {
    "Worker-A-GPU1": "a7f3d2e1"
}
```

---

#### Step 2b: Worker B Registers

**Worker B connects to WebSocket:**
```
ws://52.9.213.137:8080/
```

**Worker B gets own auth token:**
```http
POST http://52.9.213.137:8001/anonymous-signin
{}
```
Response: `{"id_token": "eyJ...worker-b", "username": "worker-b-uuid"}`

**Worker B registers with SAME room:**
```json
{
  "type": "register",
  "peer_id": "Worker-B-GPU2",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "id_token": "eyJ...worker-b",
  "role": "worker",
  "metadata": {
    "tags": ["sleap-rtc", "training-worker"],
    "properties": {
      "gpu_memory_mb": 24576,
      "gpu_model": "NVIDIA RTX 6000 Ada",
      "sleap_version": "1.3.0",
      "cuda_version": "12.0",
      "hostname": "gpu-server-02.lab.edu",
      "status": "available",
      "max_concurrent_jobs": 2,
      "supported_models": ["base", "centroid"]
    }
  }
}
```

**Server validates and responds:**
```json
{
  "type": "registered_auth",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "peer_id": "Worker-B-GPU2"
}
```

**Server state after Worker B joins:**
```python
ROOMS = {
    "a7f3d2e1": {
        "created_by": "admin-cognito-id",
        "token": "f9g2h5",
        "expires_at": 1699127200,
        "peers": {
            "Worker-A-GPU1": {
                "websocket": <WebSocket>,
                "role": "worker",
                "metadata": {...},
                "connected_at": 1699120000
            },
            "Worker-B-GPU2": {
                "websocket": <WebSocket>,
                "role": "worker",
                "metadata": {...},
                "connected_at": 1699120100
            }
        }
    }
}

PEER_TO_ROOM = {
    "Worker-A-GPU1": "a7f3d2e1",
    "Worker-B-GPU2": "a7f3d2e1"
}
```

**Workers are now listening for messages...**

---

### Phase 3: Client Joins and Discovers Workers

#### Step 3a: Client Registers

**Client connects to WebSocket:**
```
ws://52.9.213.137:8080/
```

**Client gets own auth token:**
```http
POST http://52.9.213.137:8001/anonymous-signin
{}
```
Response: `{"id_token": "eyJ...client", "username": "client-uuid"}`

**Client registers with room:**
```json
{
  "type": "register",
  "peer_id": "Client-Researcher-42",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "id_token": "eyJ...client",
  "role": "client",
  "metadata": {
    "tags": ["sleap-rtc", "training-client"],
    "properties": {
      "sleap_version": "1.3.0",
      "platform": "linux",
      "user_id": "researcher_42"
    }
  }
}
```

**Server validates and responds:**
```json
{
  "type": "registered_auth",
  "room_id": "a7f3d2e1",
  "token": "f9g2h5",
  "peer_id": "Client-Researcher-42"
}
```

**Server state after Client joins:**
```python
ROOMS = {
    "a7f3d2e1": {
        "created_by": "admin-cognito-id",
        "token": "f9g2h5",
        "expires_at": 1699127200,
        "peers": {
            "Worker-A-GPU1": {...},
            "Worker-B-GPU2": {...},
            "Client-Researcher-42": {
                "websocket": <WebSocket>,
                "role": "client",
                "metadata": {...},
                "connected_at": 1699120200
            }
        }
    }
}
```

---

#### Step 3b: Client Discovers Workers

**Client sends discovery request:**
```json
{
  "type": "discover_peers",
  "from_peer_id": "Client-Researcher-42",
  "filters": {
    "role": "worker",
    "tags": ["sleap-rtc", "training-worker"],
    "properties": {
      "gpu_memory_mb": {"$gte": 8192},
      "sleap_version": "1.3.0",
      "status": "available"
    }
  }
}
```

**Server processes:**
1. Gets client's room: `"a7f3d2e1"`
2. Iterates through peers in room
3. Applies filters:
   - Role: `"worker"` ✓ (both workers match)
   - Tags: Has `"training-worker"` ✓ (both match)
   - gpu_memory_mb ≥ 8192 ✓ (Worker-A: 16384, Worker-B: 24576)
   - sleap_version = "1.3.0" ✓ (both match)
   - status = "available" ✓ (both match)

**Server responds:**
```json
{
  "type": "peer_list",
  "to_peer_id": "Client-Researcher-42",
  "count": 2,
  "peers": [
    {
      "peer_id": "Worker-A-GPU1",
      "role": "worker",
      "metadata": {
        "tags": ["sleap-rtc", "training-worker", "inference-worker"],
        "properties": {
          "gpu_memory_mb": 16384,
          "gpu_model": "NVIDIA RTX 4090",
          "sleap_version": "1.3.0",
          "cuda_version": "11.8",
          "hostname": "gpu-server-01.lab.edu",
          "status": "available",
          "max_concurrent_jobs": 1,
          "supported_models": ["base", "centroid", "topdown"]
        }
      },
      "connected_at": 1699120000
    },
    {
      "peer_id": "Worker-B-GPU2",
      "role": "worker",
      "metadata": {
        "tags": ["sleap-rtc", "training-worker"],
        "properties": {
          "gpu_memory_mb": 24576,
          "gpu_model": "NVIDIA RTX 6000 Ada",
          "sleap_version": "1.3.0",
          "cuda_version": "12.0",
          "hostname": "gpu-server-02.lab.edu",
          "status": "available",
          "max_concurrent_jobs": 2,
          "supported_models": ["base", "centroid"]
        }
      },
      "connected_at": 1699120100
    }
  ]
}
```

**Client now knows about 2 available workers!**

---

### Phase 4: Job Request (Broadcast to All Workers)

**Client sends job request to Worker A:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-Researcher-42",
  "to_peer_id": "Worker-A-GPU1",
  "payload": {
    "app_message_type": "job_request",
    "job_id": "job-uuid-789",
    "job_type": "training",
    "dataset_info": {
      "format": "slp",
      "frame_count": 1000,
      "skeleton_type": "fly",
      "estimated_size_mb": 250
    },
    "config": {
      "model_type": "base",
      "epochs": 100,
      "batch_size": 8
    },
    "requirements": {
      "min_gpu_memory_mb": 8192,
      "estimated_duration_minutes": 45
    }
  }
}
```

**Server routes message:**
1. Validates: `Client-Researcher-42` and `Worker-A-GPU1` both in room `"a7f3d2e1"` ✓
2. Extracts Worker-A's websocket from `ROOMS["a7f3d2e1"]["peers"]["Worker-A-GPU1"]["websocket"]`
3. Forwards message:

**Worker A receives:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-Researcher-42",
  "to_peer_id": "Worker-A-GPU1",
  "payload": {
    "app_message_type": "job_request",
    "job_id": "job-uuid-789",
    ...
  }
}
```

**Client sends SAME job request to Worker B:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-Researcher-42",
  "to_peer_id": "Worker-B-GPU2",
  "payload": {
    "app_message_type": "job_request",
    "job_id": "job-uuid-789",
    ... // Same payload
  }
}
```

**Worker B receives same job request**

---

### Phase 5: Workers Respond

#### Worker A Evaluates Job

**Worker A logic:**
```python
# Check if can accept
can_accept = (
    self.status == "available" and
    self.gpu_memory_mb >= 8192 and
    self.current_jobs < self.max_concurrent_jobs and
    "base" in self.supported_models
)
# Result: True (Worker A is available and compatible)
```

**Worker A responds:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-A-GPU1",
  "to_peer_id": "Client-Researcher-42",
  "payload": {
    "app_message_type": "job_response",
    "job_id": "job-uuid-789",
    "accepted": true,
    "estimated_start_time_sec": 0,
    "estimated_duration_minutes": 42,
    "worker_info": {
      "gpu_utilization": 0.15,
      "available_memory_mb": 14000
    }
  }
}
```

**Server routes to Client** (validates both in same room, forwards message)

---

#### Worker B Evaluates Job

**Worker B logic:**
```python
# Check if can accept
can_accept = (
    self.status == "available" and
    self.gpu_memory_mb >= 8192 and
    self.current_jobs < self.max_concurrent_jobs and
    "base" in self.supported_models
)
# Result: True (Worker B is also available and has more memory!)
```

**Worker B responds:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-B-GPU2",
  "to_peer_id": "Client-Researcher-42",
  "payload": {
    "app_message_type": "job_response",
    "job_id": "job-uuid-789",
    "accepted": true,
    "estimated_start_time_sec": 0,
    "estimated_duration_minutes": 38,  // Faster GPU!
    "worker_info": {
      "gpu_utilization": 0.05,
      "available_memory_mb": 22000
    }
  }
}
```

**Server routes to Client**

---

### Phase 6: Client Selects Worker

**Client receives 2 responses:**
- Worker-A: accepted, 42 min estimate
- Worker-B: accepted, 38 min estimate

**Client selection logic:**
```python
responses = [worker_a_response, worker_b_response]
accepted = [r for r in responses if r["payload"]["accepted"]]

# Select fastest
selected = min(accepted,
               key=lambda r: r["payload"]["estimated_duration_minutes"])

# Result: Worker-B-GPU2 (38 min < 42 min)
```

**Client assigns job to Worker B:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-Researcher-42",
  "to_peer_id": "Worker-B-GPU2",
  "payload": {
    "app_message_type": "job_assignment",
    "job_id": "job-uuid-789",
    "initiate_connection": true
  }
}
```

**Server routes to Worker B**

---

### Phase 7: WebRTC Connection Establishment

**Client creates WebRTC offer for Worker B:**
```json
{
  "type": "offer",
  "sender": "Client-Researcher-42",
  "target": "Worker-B-GPU2",
  "sdp": "v=0\r\no=- 1234567890 2 IN IP4 127.0.0.1\r\n..."
}
```

**Server routes via `forward_message()` to Worker B**

**Worker B creates answer:**
```json
{
  "type": "answer",
  "sender": "Worker-B-GPU2",
  "target": "Client-Researcher-42",
  "sdp": "v=0\r\no=- 9876543210 2 IN IP4 127.0.0.1\r\n..."
}
```

**Server routes back to Client**

**ICE candidates exchanged:**
```json
{
  "type": "candidate",
  "sender": "Client-Researcher-42",
  "target": "Worker-B-GPU2",
  "candidate": "candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host"
}
```

**WebRTC data channel established!**

---

### Phase 8: Data Transfer & Training

**Dataset transfer (over WebRTC data channel, NOT signaling server):**
```
Client → (encrypted) → Worker B
250 MB dataset transferred
```

**Worker B sends status updates:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-B-GPU2",
  "to_peer_id": "Client-Researcher-42",
  "payload": {
    "app_message_type": "job_status",
    "job_id": "job-uuid-789",
    "status": "running",
    "progress": 0.25,
    "details": {
      "current_epoch": 25,
      "total_epochs": 100,
      "current_loss": 0.0456
    }
  }
}
```

**Client receives periodic updates via signaling server**

---

### Phase 9: Job Completion

**Worker B finishes training:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-B-GPU2",
  "to_peer_id": "Client-Researcher-42",
  "payload": {
    "app_message_type": "job_complete",
    "job_id": "job-uuid-789",
    "status": "completed",
    "result": {
      "model_size_mb": 25.3,
      "final_loss": 0.0189,
      "training_duration_minutes": 37
    }
  }
}
```

**Model transfer (over WebRTC data channel):**
```
Worker B → (encrypted) → Client
25.3 MB trained model transferred
```

**Client closes WebRTC connection**

**Worker A remains available for future jobs!**

---

## Summary Timeline

```
Time   Event
────────────────────────────────────────────────────────────
T+0    Admin creates room "a7f3d2e1" with token "f9g2h5"
T+5    Worker-A connects to WS, registers (role: worker, GPU: 16GB)
T+10   Worker-B connects to WS, registers (role: worker, GPU: 24GB)
T+15   Client connects to WS, registers (role: client)
T+16   Client discovers workers → finds 2 matches
T+17   Client sends job_request to Worker-A
T+17   Client sends job_request to Worker-B
T+18   Worker-A responds: accepted (42 min estimate)
T+18   Worker-B responds: accepted (38 min estimate)
T+19   Client selects Worker-B (faster)
T+20   Client sends job_assignment to Worker-B
T+21   WebRTC offer/answer exchange
T+25   Dataset transfer complete (250 MB)
T+30   Training starts on Worker-B
T+67   Training complete (37 min actual)
T+69   Model transfer complete (25 MB)
T+70   WebRTC connection closed

       Worker-A still available in room for next job!
```

---

## Key Points

### 1. Room Creation
- **Who creates?** Admin, first worker, or external coordinator
- **Credentials shared:** `room_id` + `token` distributed to all participants
- **TTL:** 2 hours from creation

### 2. Worker Registration
- Each worker gets own Cognito token
- Workers advertise capabilities via `metadata.properties`
- Multiple workers can join same room

### 3. Client Discovery
- Client uses filters to find compatible workers
- Signaling server does NOT interpret metadata (just filters)
- Discovery returns all matches

### 4. Job Negotiation
- Client broadcasts job request to multiple workers
- Workers respond with accept/reject + estimates
- Client selects best worker (by any criteria)

### 5. Data Transfer
- **Small messages** (< 1KB): Through signaling server
- **Large data** (datasets/models): Through WebRTC data channels
- Signaling server never sees training data

### 6. Signaling Server Role
- Routes messages between peers in same room
- Validates room membership
- Does NOT interpret application payloads
- Does NOT handle data transfer

---

## Potential Issues to Check

### 1. Room ID Distribution
**Question:** How do workers know what `room_id` to join?

**Options:**
- Environment variable: `SLEAP_RTC_ROOM_ID=a7f3d2e1`
- Config file: `room_id = "a7f3d2e1"`
- CLI argument: `sleap-rtc worker --room a7f3d2e1 --token f9g2h5`
- Broadcast discovery (future): Workers advertise on local network

### 2. Token Security
**Question:** How is room `token` shared securely?

**Current:** Shared via same mechanism as `room_id` (acceptable for lab)
**Future:** Could use short-lived tokens or public/private key auth

### 3. Worker Rejection Handling
**Question:** What if all workers reject?

**Client should:**
- Wait for timeout (5 seconds)
- If no accepted responses, report error to user
- Optionally: retry after delay or with different job config

### 4. Worker Disconnection
**Question:** What if selected worker disconnects before job starts?

**Handled by:**
- WebRTC connection will fail
- Client can retry with different worker from original list
- Or re-discover workers (one may have become available)

---

## Next: Implement in sleap-rtc

Now that the workflow is clear, you'll implement:
1. Worker registration with metadata
2. Client discovery logic
3. Job request/response handling
4. Worker selection algorithm
5. Progress tracking

See `docs/SLEAP_RTC_PROTOCOL.md` for the full protocol spec!
