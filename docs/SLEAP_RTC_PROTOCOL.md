# SLEAP-RTC Application Protocol

## Overview

This document defines the **application-level protocol** for SLEAP-RTC training and inference jobs. This protocol is **independent of the signaling server** and sits on top of the generic peer messaging infrastructure.

**Important:** The signaling server does NOT know about this protocol. All SLEAP-RTC messages are wrapped in `peer_message.payload` and passed through transparently.

---

## Layer Separation

```
┌─────────────────────────────────────┐
│   SLEAP-RTC Protocol (This Doc)    │  ← Application layer
│   - Job requests                     │
│   - Job responses                    │
│   - Progress updates                 │
│   - Model transfer                   │
└─────────────────────────────────────┘
                 ↓
┌─────────────────────────────────────┐
│   Signaling Server API v2.0         │  ← Transport layer
│   - Peer discovery                   │
│   - Message routing                  │
│   - WebRTC signaling                 │
└─────────────────────────────────────┘
```

---

## Message Envelope

All SLEAP-RTC messages are wrapped in signaling server's `peer_message`:

```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    "app_message_type": "job_request",  // SLEAP-RTC message type
    // ... SLEAP-RTC specific fields
  }
}
```

---

## Worker Registration

### Worker Metadata

Workers register with signaling server using this metadata structure:

```json
{
  "type": "register",
  "peer_id": "Worker-3108",
  "room_id": "abc123",
  "token": "f7g8h9",
  "id_token": "cognito_token",
  "role": "worker",
  "metadata": {
    "tags": ["sleap-rtc", "training-worker", "inference-worker"],
    "properties": {
      "sleap_version": "1.3.0",
      "gpu_memory_mb": 16384,
      "gpu_model": "NVIDIA RTX 4090",
      "cuda_version": "11.8",
      "max_concurrent_jobs": 1,
      "supported_models": ["base", "centroid", "topdown"],
      "supported_job_types": ["training", "inference"],
      "status": "available"  // "available", "busy", "maintenance"
    }
  }
}
```

**Key Properties:**
- `sleap_version`: For compatibility checking
- `gpu_memory_mb`: For resource matching
- `max_concurrent_jobs`: Number of parallel jobs supported
- `supported_models`: Model architectures this worker can handle
- `status`: Current availability

---

## Client Registration

### Client Metadata

Clients register with simpler metadata:

```json
{
  "type": "register",
  "peer_id": "Client-1489",
  "room_id": "abc123",
  "token": "f7g8h9",
  "id_token": "cognito_token",
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

---

## Job Workflow

### 1. Job Request

**Direction:** Client → Worker(s)

**When:** Client wants to submit a training or inference job

**Discovery first:**
```json
{
  "type": "discover_peers",
  "from_peer_id": "Client-1489",
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

**Then send job request to each discovered worker:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    "app_message_type": "job_request",
    "job_id": "job-uuid-123",
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
      "batch_size": 8,
      "learning_rate": 0.001,
      "augmentation": true
    },
    "requirements": {
      "min_gpu_memory_mb": 8192,
      "estimated_duration_minutes": 45
    },
    "timeout_ms": 5000  // How long to wait for response
  }
}
```

---

### 2. Job Response

**Direction:** Worker → Client

**When:** Worker decides if it can accept the job

**Accept:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_response",
    "job_id": "job-uuid-123",
    "accepted": true,
    "estimated_start_time_sec": 0,  // Can start immediately
    "estimated_duration_minutes": 42,
    "worker_info": {
      "gpu_utilization": 0.15,
      "available_memory_mb": 14000
    }
  }
}
```

**Reject:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-7492",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_response",
    "job_id": "job-uuid-123",
    "accepted": false,
    "reason": "insufficient_memory",  // or "busy", "incompatible_version", etc.
    "retry_after_sec": 300  // Optional: when worker might be available
  }
}
```

---

### 3. Job Assignment

**Direction:** Client → Selected Worker

**When:** Client selects one worker from responses

```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    "app_message_type": "job_assignment",
    "job_id": "job-uuid-123",
    "initiate_connection": true  // Signal to start WebRTC connection
  }
}
```

**Then establish WebRTC connection for data transfer:**
```json
{
  "type": "offer",
  "sender": "Client-1489",
  "target": "Worker-3108",
  "sdp": "v=0\r\no=- ..."
}
```

---

### 4. Job Status Updates

**Direction:** Worker → Client

**When:** During job execution (periodic updates)

```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_status",
    "job_id": "job-uuid-123",
    "status": "running",  // "starting", "running", "completed", "failed"
    "progress": 0.45,  // 0.0 to 1.0
    "details": {
      "current_epoch": 45,
      "total_epochs": 100,
      "current_loss": 0.0234,
      "estimated_remaining_minutes": 22
    },
    "message": "Training epoch 45/100"
  }
}
```

---

### 5. Job Completion

**Direction:** Worker → Client

**When:** Job finishes successfully

```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_complete",
    "job_id": "job-uuid-123",
    "status": "completed",
    "result": {
      "model_size_mb": 25.3,
      "final_loss": 0.0189,
      "training_duration_minutes": 40,
      "total_epochs": 100
    },
    "transfer_method": "webrtc_datachannel",
    "ready_for_download": true
  }
}
```

**Model is transferred over WebRTC data channel** (not through signaling server).

---

### 6. Job Failure

**Direction:** Worker → Client

**When:** Job fails

```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_failed",
    "job_id": "job-uuid-123",
    "status": "failed",
    "error": {
      "code": "OUT_OF_MEMORY",
      "message": "GPU out of memory at epoch 23",
      "recoverable": false
    },
    "partial_results": null
  }
}
```

---

### 7. Job Cancellation

**Direction:** Client → Worker

**When:** Client wants to abort job

```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    "app_message_type": "job_cancel",
    "job_id": "job-uuid-123",
    "reason": "user_requested"
  }
}
```

**Worker acknowledges:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {
    "app_message_type": "job_cancelled",
    "job_id": "job-uuid-123",
    "status": "cancelled",
    "cleanup_complete": true
  }
}
```

---

## Inference Jobs

Similar workflow, but simplified:

### Inference Request
```json
{
  "app_message_type": "job_request",
  "job_id": "infer-uuid-456",
  "job_type": "inference",
  "dataset_info": {
    "format": "slp",
    "frame_count": 100,
    "video_path": "video.mp4"
  },
  "model_info": {
    "model_path": "trained_model.h5",
    "model_size_mb": 25.3
  },
  "config": {
    "batch_size": 4,
    "confidence_threshold": 0.7
  }
}
```

### Inference Response
```json
{
  "app_message_type": "job_complete",
  "job_id": "infer-uuid-456",
  "status": "completed",
  "result": {
    "predictions_size_mb": 2.1,
    "frame_count": 100,
    "inference_duration_seconds": 45,
    "average_confidence": 0.89
  }
}
```

---

## Worker Heartbeat (Optional)

Workers can send periodic heartbeats to indicate they're still alive:

```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",  // Or broadcast to all clients
  "payload": {
    "app_message_type": "worker_heartbeat",
    "timestamp": "2025-11-03T10:30:00Z",
    "status": "available",
    "gpu_utilization": 0.0,
    "active_jobs": 0
  }
}
```

---

## Error Handling

### Application-Level Errors

All SLEAP-RTC errors are wrapped in `peer_message.payload`:

```json
{
  "app_message_type": "app_error",
  "error_code": "INCOMPATIBLE_VERSION",
  "message": "Worker SLEAP version 1.2.0 incompatible with client version 1.3.0",
  "recoverable": false,
  "details": {
    "worker_version": "1.2.0",
    "client_version": "1.3.0",
    "min_required_version": "1.3.0"
  }
}
```

---

## Version Compatibility

### Version Negotiation

Clients and workers should check version compatibility:

```python
def is_compatible(worker_version: str, client_version: str) -> bool:
    # Example: major.minor.patch
    worker_major, worker_minor, _ = worker_version.split('.')
    client_major, client_minor, _ = client_version.split('.')

    # Same major version, worker minor >= client minor
    return (worker_major == client_major and
            worker_minor >= client_minor)
```

### Deprecation Policy

- **Minor version bumps**: Add new features, backward compatible
- **Major version bumps**: Breaking changes, may require migration

---

## Data Transfer

### Small Data (< 1MB)
Can be sent through signaling server `peer_message.payload`.

### Large Data (> 1MB)
**Must use WebRTC data channel:**
1. Client sends job assignment
2. Client creates WebRTC offer
3. Worker accepts and establishes connection
4. Transfer dataset/model over data channel
5. Close WebRTC connection when done

---

## Security Considerations

### Trust Model
- Workers trust clients to provide valid datasets
- Clients trust workers to execute jobs correctly
- Signaling server trusts Cognito authentication

### Data Privacy
- Datasets may contain sensitive information
- Use WebRTC encryption for data transfer
- Workers should not retain data after job completion

### Resource Limits
- Workers should enforce memory/time limits
- Clients should timeout if workers don't respond
- Jobs should be killable by clients

---

## Example Implementation Pseudocode

### Client

```python
class SleapRTCClient:
    async def submit_training_job(self, dataset_path, config):
        # 1. Discover workers
        await self.signaling.send({
            "type": "discover_peers",
            "from_peer_id": self.peer_id,
            "filters": {
                "role": "worker",
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {"gpu_memory_mb": {"$gte": 8192}}
            }
        })

        workers = await self.wait_for_peer_list()

        # 2. Send job request to all workers
        job_id = str(uuid.uuid4())
        for worker in workers:
            await self.send_peer_message(worker["peer_id"], {
                "app_message_type": "job_request",
                "job_id": job_id,
                "job_type": "training",
                "dataset_info": {...},
                "config": config
            })

        # 3. Collect responses
        responses = await self.collect_job_responses(job_id, timeout=5.0)

        # 4. Select best worker
        selected = min([r for r in responses if r["accepted"]],
                       key=lambda r: r["estimated_start_time_sec"])

        # 5. Assign job
        await self.send_peer_message(selected["worker_id"], {
            "app_message_type": "job_assignment",
            "job_id": job_id
        })

        # 6. Establish WebRTC and transfer data
        await self.establish_webrtc_connection(selected["worker_id"])
        await self.transfer_dataset(dataset_path)

        # 7. Monitor progress
        async for status in self.monitor_job(job_id):
            print(f"Progress: {status['progress']*100:.0f}%")

        # 8. Download model
        model = await self.download_model()
        return model
```

### Worker

```python
class SleapRTCWorker:
    async def run(self):
        # Register with metadata
        await self.signaling.send({
            "type": "register",
            "peer_id": self.peer_id,
            "room_id": self.room_id,
            "token": self.token,
            "id_token": self.id_token,
            "role": "worker",
            "metadata": {
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {
                    "gpu_memory_mb": self.gpu_info["memory_mb"],
                    "status": "available"
                }
            }
        })

        # Handle incoming messages
        async for message in self.signaling.messages:
            if message["type"] != "peer_message":
                continue

            payload = message["payload"]

            if payload["app_message_type"] == "job_request":
                await self.handle_job_request(message["from_peer_id"], payload)

    async def handle_job_request(self, client_id, request):
        # Check if can accept
        can_accept = (self.status == "available" and
                      self.check_compatibility(request))

        # Respond
        await self.send_peer_message(client_id, {
            "app_message_type": "job_response",
            "job_id": request["job_id"],
            "accepted": can_accept,
            "estimated_start_time_sec": 0 if can_accept else 999999
        })

        if can_accept:
            # Wait for assignment, then execute
            await self.wait_for_assignment(request["job_id"])
            await self.execute_training_job(client_id, request)
```

---

## Future Extensions

### Potential Additions
- **Job queueing**: Workers maintain job queue
- **Priority levels**: High-priority jobs jump queue
- **Checkpointing**: Resume interrupted jobs
- **Multi-worker training**: Distributed training across workers
- **Resource reservations**: Reserve worker for future job

### Extensibility
Add new `app_message_type` values without changing signaling server:
- `model_evaluation`
- `hyperparameter_tuning`
- `dataset_preprocessing`

---

## Summary

| Layer | Responsibility | Implementation |
|-------|----------------|----------------|
| **SLEAP-RTC Protocol** | Job submission, progress tracking, model transfer | `sleap-rtc` Python package |
| **Signaling Server** | Peer discovery, message routing, WebRTC signaling | This repository (server.py) |
| **WebRTC** | Encrypted data transfer (datasets, models) | `aiortc` library |

**Key Principle:** Signaling server is a generic peer-to-peer messaging bus. SLEAP-RTC protocol is an application built on top of it.
