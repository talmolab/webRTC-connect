# sleap-rtc Implementation Checklist

This document contains instructions for updating the sleap-rtc repository to support the new signaling server v2.0 features (peer discovery, worker status management, and multi-worker job assignment).

---

## Context

The signaling server (webRTC-connect repo) has been upgraded to v2.0 with the following new features:

1. **Peer Discovery** - Find workers based on capabilities (GPU memory, status, etc.)
2. **Metadata Updates** - Real-time worker status synchronization (available/busy)
3. **Generic Message Routing** - Application-specific messages (job requests/responses)
4. **Health & Metrics** - Monitoring endpoints

**Signaling Server API Documentation:** See `docs/SIGNALING_API_V2.md` in webRTC-connect repo

**Application Protocol Specification:** See `docs/SLEAP_RTC_PROTOCOL.md` in webRTC-connect repo

---

## Implementation Tasks

### Task 1: Enhance Worker Registration

**File:** `sleap_rtc/worker.py` (or equivalent)

**What to add:** Worker metadata during registration

**Before:**
```python
await self.signaling_ws.send(json.dumps({
    "type": "register",
    "peer_id": self.peer_id,
    "room_id": self.room_id,
    "token": self.token,
    "id_token": self.id_token
}))
```

**After:**
```python
await self.signaling_ws.send(json.dumps({
    "type": "register",
    "peer_id": self.peer_id,
    "room_id": self.room_id,
    "token": self.token,
    "id_token": self.id_token,
    "role": "worker",  # NEW
    "metadata": {       # NEW
        "tags": ["sleap-rtc", "training-worker", "inference-worker"],
        "properties": {
            "gpu_memory_mb": self._detect_gpu_memory(),
            "gpu_model": self._detect_gpu_model(),
            "sleap_version": sleap.__version__,
            "cuda_version": self._detect_cuda_version(),
            "hostname": socket.gethostname(),
            "status": "available",
            "max_concurrent_jobs": 1,
            "supported_models": ["base", "centroid", "topdown"],
            "supported_job_types": ["training", "inference"]
        }
    }
}))
```

**Helper functions needed:**
```python
def _detect_gpu_memory(self) -> int:
    """Detect GPU memory in MB."""
    import torch
    if torch.cuda.is_available():
        return torch.cuda.get_device_properties(self.gpu_id).total_memory // (1024 * 1024)
    return 0

def _detect_gpu_model(self) -> str:
    """Detect GPU model name."""
    import torch
    if torch.cuda.is_available():
        return torch.cuda.get_device_properties(self.gpu_id).name
    return "CPU"

def _detect_cuda_version(self) -> str:
    """Detect CUDA version."""
    import torch
    return torch.version.cuda if torch.cuda.is_available() else "N/A"
```

---

### Task 2: Add Worker Status Update Method

**File:** `sleap_rtc/worker.py`

**What to add:** Method to update worker status in signaling server

```python
async def update_status(self, status: str, **extra_properties):
    """Update worker status in signaling server.

    Args:
        status: "available", "busy", or "maintenance"
        **extra_properties: Additional properties to update (e.g., current_job_id)
    """
    metadata = {
        "properties": {
            "status": status,
            **extra_properties
        }
    }

    await self.signaling_ws.send(json.dumps({
        "type": "update_metadata",
        "peer_id": self.peer_id,
        "metadata": metadata
    }))

    # Wait for confirmation (optional but recommended)
    response = await self.signaling_ws.recv()
    response_data = json.loads(response)

    if response_data.get("type") == "metadata_updated":
        logging.info(f"Status updated to: {status}")
    else:
        logging.warning(f"Unexpected response: {response_data}")
```

**When to call:**
- `update_status("busy")` - When accepting a job
- `update_status("available")` - When job completes or fails
- `update_status("maintenance")` - During updates/maintenance

---

### Task 3: Implement Job Request Handler (Worker)

**File:** `sleap_rtc/worker.py`

**What to add:** Handler for incoming job requests from clients

```python
async def handle_peer_message(self, message: dict):
    """Handle incoming peer messages (job requests)."""
    if message["type"] != "peer_message":
        return

    payload = message["payload"]
    app_message_type = payload.get("app_message_type")

    if app_message_type == "job_request":
        await self._handle_job_request(message["from_peer_id"], payload)
    elif app_message_type == "job_assignment":
        await self._handle_job_assignment(message["from_peer_id"], payload)
    elif app_message_type == "job_cancel":
        await self._handle_job_cancel(payload)

async def _handle_job_request(self, client_id: str, request: dict):
    """Respond to job request from client."""
    job_id = request["job_id"]
    job_type = request["job_type"]

    # Check if we can accept this job
    can_accept = (
        self.status == "available" and
        self._check_job_compatibility(request)
    )

    if not can_accept:
        # Send rejection
        await self._send_peer_message(client_id, {
            "app_message_type": "job_response",
            "job_id": job_id,
            "accepted": False,
            "reason": "busy" if self.status != "available" else "incompatible"
        })
        return

    # Estimate job duration
    estimated_duration = self._estimate_job_duration(request)

    # Send acceptance
    await self._send_peer_message(client_id, {
        "app_message_type": "job_response",
        "job_id": job_id,
        "accepted": True,
        "estimated_start_time_sec": 0,
        "estimated_duration_minutes": estimated_duration,
        "worker_info": {
            "gpu_utilization": self._get_gpu_utilization(),
            "available_memory_mb": self._get_available_memory()
        }
    })

    # Update status to "reserved" (prevent other clients from requesting)
    await self.update_status("reserved", pending_job_id=job_id)

async def _handle_job_assignment(self, client_id: str, assignment: dict):
    """Handle job assignment from client."""
    job_id = assignment["job_id"]

    # Update status to busy
    await self.update_status("busy", current_job_id=job_id)

    # Initiate WebRTC connection if requested
    if assignment.get("initiate_connection"):
        # Wait for WebRTC offer from client
        # (existing WebRTC code handles this)
        pass

    # Store job info for execution
    self.current_job = {
        "job_id": job_id,
        "client_id": client_id,
        "assigned_at": time.time()
    }

def _check_job_compatibility(self, request: dict) -> bool:
    """Check if this worker can handle the job."""
    job_spec = request.get("config", {})
    requirements = request.get("requirements", {})

    # Check GPU memory
    min_gpu_mb = requirements.get("min_gpu_memory_mb", 0)
    if self.gpu_memory_mb < min_gpu_mb:
        return False

    # Check model support
    model_type = job_spec.get("model_type")
    if model_type and model_type not in self.supported_models:
        return False

    # Check job type
    job_type = request.get("job_type")
    if job_type not in self.supported_job_types:
        return False

    return True

async def _send_peer_message(self, to_peer_id: str, payload: dict):
    """Send peer message via signaling server."""
    await self.signaling_ws.send(json.dumps({
        "type": "peer_message",
        "from_peer_id": self.peer_id,
        "to_peer_id": to_peer_id,
        "payload": payload
    }))
```

---

### Task 4: Update Job Execution (Worker)

**File:** `sleap_rtc/worker.py`

**What to modify:** Existing training/inference execution to send status updates

```python
async def execute_training_job(self, job_request: dict):
    """Execute training job with progress updates."""
    job_id = self.current_job["job_id"]
    client_id = self.current_job["client_id"]

    try:
        # Send starting status
        await self._send_peer_message(client_id, {
            "app_message_type": "job_status",
            "job_id": job_id,
            "status": "starting",
            "progress": 0.0,
            "message": "Initializing training"
        })

        # Run training with periodic updates
        for epoch in range(num_epochs):
            # ... training code ...

            # Send progress update every N epochs
            if epoch % 5 == 0:
                await self._send_peer_message(client_id, {
                    "app_message_type": "job_status",
                    "job_id": job_id,
                    "status": "running",
                    "progress": epoch / num_epochs,
                    "details": {
                        "current_epoch": epoch,
                        "total_epochs": num_epochs,
                        "current_loss": loss_value,
                        "estimated_remaining_minutes": self._estimate_remaining_time(epoch, num_epochs)
                    },
                    "message": f"Training epoch {epoch}/{num_epochs}"
                })

        # Send completion
        await self._send_peer_message(client_id, {
            "app_message_type": "job_complete",
            "job_id": job_id,
            "status": "completed",
            "result": {
                "model_size_mb": os.path.getsize(model_path) / (1024 * 1024),
                "final_loss": final_loss,
                "training_duration_minutes": training_duration,
                "total_epochs": num_epochs
            },
            "transfer_method": "webrtc_datachannel",
            "ready_for_download": True
        })

    except Exception as e:
        # Send failure
        await self._send_peer_message(client_id, {
            "app_message_type": "job_failed",
            "job_id": job_id,
            "status": "failed",
            "error": {
                "code": type(e).__name__,
                "message": str(e),
                "recoverable": False
            }
        })

    finally:
        # Update status back to available
        await self.update_status("available")
        self.current_job = None
```

---

### Task 5: Enhance Client Registration

**File:** `sleap_rtc/client.py` (or equivalent)

**What to add:** Client metadata during registration

```python
await self.signaling_ws.send(json.dumps({
    "type": "register",
    "peer_id": self.peer_id,
    "room_id": self.room_id,
    "token": self.token,
    "id_token": self.id_token,
    "role": "client",  # NEW
    "metadata": {       # NEW
        "tags": ["sleap-rtc", "training-client"],
        "properties": {
            "sleap_version": sleap.__version__,
            "platform": platform.system(),
            "user_id": getpass.getuser()
        }
    }
}))
```

---

### Task 6: Implement Worker Discovery (Client)

**File:** `sleap_rtc/client.py`

**What to add:** Method to discover available workers

```python
async def discover_workers(self, **filter_requirements) -> list:
    """Discover available workers matching requirements.

    Args:
        **filter_requirements: Keyword arguments for filtering
            - min_gpu_memory_mb: Minimum GPU memory
            - model_type: Required model support
            - job_type: "training" or "inference"

    Returns:
        List of worker peer info dicts
    """
    # Build filters
    filters = {
        "role": "worker",
        "tags": ["sleap-rtc"],
        "properties": {
            "status": "available"
        }
    }

    # Add GPU memory requirement
    if "min_gpu_memory_mb" in filter_requirements:
        filters["properties"]["gpu_memory_mb"] = {
            "$gte": filter_requirements["min_gpu_memory_mb"]
        }

    # Add job type requirement
    if "job_type" in filter_requirements:
        job_type = filter_requirements["job_type"]
        if job_type == "training":
            filters["tags"].append("training-worker")
        elif job_type == "inference":
            filters["tags"].append("inference-worker")

    # Send discovery request
    await self.signaling_ws.send(json.dumps({
        "type": "discover_peers",
        "from_peer_id": self.peer_id,
        "filters": filters
    }))

    # Wait for response
    response = await self.signaling_ws.recv()
    response_data = json.loads(response)

    if response_data["type"] == "peer_list":
        workers = response_data["peers"]
        logging.info(f"Discovered {len(workers)} available workers")
        return workers
    else:
        logging.error(f"Unexpected response: {response_data}")
        return []
```

---

### Task 7: Implement Job Submission (Client)

**File:** `sleap_rtc/client.py`

**What to add:** Method to submit job to multiple workers and select best

```python
async def submit_training_job(self, dataset_path: str, config: dict, **job_requirements):
    """Submit training job to available workers.

    Args:
        dataset_path: Path to training dataset
        config: Training configuration
        **job_requirements: Job requirements (min_gpu_memory_mb, etc.)

    Returns:
        Selected worker peer_id
    """
    # 1. Discover available workers
    workers = await self.discover_workers(
        job_type="training",
        **job_requirements
    )

    if not workers:
        raise NoWorkersAvailableError("No workers found matching requirements")

    logging.info(f"Found {len(workers)} workers, sending job requests...")

    # 2. Create job request
    job_id = str(uuid.uuid4())
    job_request = {
        "app_message_type": "job_request",
        "job_id": job_id,
        "job_type": "training",
        "dataset_info": {
            "format": "slp",
            "path": dataset_path,
            "frame_count": self._count_frames(dataset_path),
            "estimated_size_mb": os.path.getsize(dataset_path) / (1024 * 1024)
        },
        "config": config,
        "requirements": job_requirements
    }

    # 3. Send job request to all discovered workers
    for worker in workers:
        await self._send_peer_message(worker["peer_id"], job_request)

    # 4. Collect responses (with timeout)
    responses = await self._collect_job_responses(job_id, timeout=5.0)

    if not responses:
        raise NoWorkersAcceptedError("No workers accepted the job")

    # Filter accepted responses
    accepted = [r for r in responses if r["accepted"]]

    if not accepted:
        reasons = [r["reason"] for r in responses if not r["accepted"]]
        raise NoWorkersAcceptedError(f"All workers rejected: {reasons}")

    # 5. Select best worker (e.g., fastest estimated time)
    selected = min(accepted, key=lambda r: r.get("estimated_duration_minutes", 999999))

    logging.info(f"Selected worker: {selected['worker_id']} "
                 f"(estimate: {selected['estimated_duration_minutes']} min)")

    # 6. Send job assignment to selected worker
    await self._send_peer_message(selected["worker_id"], {
        "app_message_type": "job_assignment",
        "job_id": job_id,
        "initiate_connection": True
    })

    # 7. Establish WebRTC connection
    await self._establish_webrtc_connection(selected["worker_id"])

    # 8. Transfer dataset
    await self._transfer_dataset(dataset_path)

    # 9. Monitor job progress
    await self._monitor_job_progress(job_id)

    return selected["worker_id"]

async def _collect_job_responses(self, job_id: str, timeout: float) -> list:
    """Collect job responses from workers."""
    responses = []
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            remaining = deadline - time.time()
            msg = await asyncio.wait_for(
                self.signaling_ws.recv(),
                timeout=max(0.1, remaining)
            )

            data = json.loads(msg)

            if data["type"] == "peer_message":
                payload = data["payload"]
                if (payload.get("app_message_type") == "job_response" and
                    payload.get("job_id") == job_id):

                    responses.append({
                        "worker_id": data["from_peer_id"],
                        "accepted": payload["accepted"],
                        "reason": payload.get("reason", ""),
                        "estimated_duration_minutes": payload.get("estimated_duration_minutes", 0)
                    })

        except asyncio.TimeoutError:
            break

    return responses

async def _monitor_job_progress(self, job_id: str):
    """Monitor job progress and display updates."""
    while True:
        msg = await self.signaling_ws.recv()
        data = json.loads(msg)

        if data["type"] != "peer_message":
            continue

        payload = data["payload"]

        if payload.get("job_id") != job_id:
            continue

        app_msg_type = payload["app_message_type"]

        if app_msg_type == "job_status":
            # Display progress
            progress = payload.get("progress", 0)
            message = payload.get("message", "")
            print(f"Progress: {progress*100:.1f}% - {message}")

        elif app_msg_type == "job_complete":
            print("Job completed successfully!")
            result = payload.get("result", {})
            print(f"Final loss: {result.get('final_loss', 'N/A')}")
            print(f"Duration: {result.get('training_duration_minutes', 0):.1f} minutes")
            break

        elif app_msg_type == "job_failed":
            error = payload.get("error", {})
            raise JobFailedError(f"Job failed: {error.get('message', 'Unknown error')}")

async def _send_peer_message(self, to_peer_id: str, payload: dict):
    """Send peer message via signaling server."""
    await self.signaling_ws.send(json.dumps({
        "type": "peer_message",
        "from_peer_id": self.peer_id,
        "to_peer_id": to_peer_id,
        "payload": payload
    }))
```

---

### Task 8: Add Error Classes

**File:** `sleap_rtc/exceptions.py` (or at top of relevant file)

```python
class NoWorkersAvailableError(Exception):
    """No workers available matching requirements."""
    pass

class NoWorkersAcceptedError(Exception):
    """No workers accepted the job request."""
    pass

class JobFailedError(Exception):
    """Job execution failed on worker."""
    pass
```

---

### Task 9: Add Backward Compatibility Check

**File:** `sleap_rtc/client.py` or `sleap_rtc/worker.py`

**What to add:** Fallback for older signaling servers that don't support discovery

```python
async def discover_workers(self, **filter_requirements) -> list:
    """Discover workers (with fallback for old signaling servers)."""

    # Try new discovery API
    try:
        await self.signaling_ws.send(json.dumps({
            "type": "discover_peers",
            "from_peer_id": self.peer_id,
            "filters": {...}
        }))

        response = await asyncio.wait_for(self.signaling_ws.recv(), timeout=2.0)
        response_data = json.loads(response)

        if response_data["type"] == "peer_list":
            return response_data["peers"]
        elif response_data["type"] == "error" and response_data["code"] == "UNKNOWN_MESSAGE_TYPE":
            # Old signaling server, fall back
            logging.warning("Signaling server doesn't support discovery, using manual peer_id")
            return await self._fallback_manual_peer_selection()

    except asyncio.TimeoutError:
        logging.warning("Discovery timed out, falling back to manual peer_id")
        return await self._fallback_manual_peer_selection()

async def _fallback_manual_peer_selection(self) -> list:
    """Fallback: Use manually configured worker peer_id."""
    # Read from config or environment variable
    worker_peer_id = os.getenv("SLEAP_RTC_WORKER_PEER_ID")

    if not worker_peer_id:
        raise ValueError("No workers discovered and SLEAP_RTC_WORKER_PEER_ID not set")

    return [{
        "peer_id": worker_peer_id,
        "role": "worker",
        "metadata": {}
    }]
```

---

### Task 10: Update CLI (Optional)

**File:** `sleap_rtc/cli.py` or equivalent

**What to add:** CLI flags for worker discovery

```python
@click.command()
@click.argument("dataset_path")
@click.option("--min-gpu-memory", type=int, default=8192,
              help="Minimum GPU memory in MB")
@click.option("--model-type", type=str, default="base",
              help="Model type to train")
def train(dataset_path: str, min_gpu_memory: int, model_type: str):
    """Submit training job to remote worker."""

    client = SleapRTCClient()

    # Connect to signaling server
    await client.connect(
        room_id=os.getenv("SLEAP_RTC_ROOM_ID"),
        token=os.getenv("SLEAP_RTC_ROOM_TOKEN")
    )

    # Submit job with requirements
    worker_id = await client.submit_training_job(
        dataset_path=dataset_path,
        config={"model_type": model_type},
        min_gpu_memory_mb=min_gpu_memory,
        job_type="training"
    )

    print(f"Job assigned to worker: {worker_id}")
```

---

## Testing Tasks

### Test 1: Worker Registration
```python
def test_worker_registers_with_metadata():
    """Test worker registration includes GPU info."""
    worker = SleapRTCWorker(gpu_id=0)
    await worker.connect(room_id="test-room", token="test-token")

    # Verify metadata was sent
    assert worker.gpu_memory_mb > 0
    assert worker.gpu_model != ""
```

### Test 2: Worker Discovery
```python
def test_client_discovers_workers():
    """Test client can discover available workers."""
    # Start worker
    worker = SleapRTCWorker()
    await worker.connect()

    # Client discovers
    client = SleapRTCClient()
    await client.connect()

    workers = await client.discover_workers(min_gpu_memory_mb=8192)

    assert len(workers) == 1
    assert workers[0]["peer_id"] == worker.peer_id
```

### Test 3: Status Updates
```python
def test_worker_status_updates():
    """Test worker status updates when accepting job."""
    worker = SleapRTCWorker()
    await worker.connect()

    # Accept job
    await worker.accept_job("job-123")

    # Status should be busy
    # (verify by having another client discover - should see no workers)
    client2 = SleapRTCClient()
    workers = await client2.discover_workers()

    assert len(workers) == 0  # Worker is busy
```

### Test 4: Job Workflow
```python
@pytest.mark.integration
def test_full_job_workflow():
    """Test complete job submission workflow."""
    # Setup
    worker = SleapRTCWorker()
    await worker.connect()

    client = SleapRTCClient()
    await client.connect()

    # Submit job
    worker_id = await client.submit_training_job(
        dataset_path="test_data.slp",
        config={"epochs": 10}
    )

    assert worker_id == worker.peer_id

    # Worker should be busy
    workers = await client.discover_workers()
    assert len(workers) == 0

    # Wait for completion
    await asyncio.sleep(5)  # Simulate training

    # Worker should be available again
    workers = await client.discover_workers()
    assert len(workers) == 1
```

---

## Summary Checklist

Copy this checklist when giving instructions to Claude in sleap-rtc repo:

### Worker Implementation
- [ ] Add metadata to worker registration (Task 1)
- [ ] Implement `update_status()` method (Task 2)
- [ ] Add job request handler (Task 3)
- [ ] Add job assignment handler (Task 3)
- [ ] Update job execution to send progress updates (Task 4)
- [ ] Add helper methods: `_detect_gpu_memory()`, `_detect_gpu_model()`, etc. (Task 1)

### Client Implementation
- [ ] Add metadata to client registration (Task 5)
- [ ] Implement `discover_workers()` method (Task 6)
- [ ] Implement `submit_training_job()` method (Task 7)
- [ ] Add `_collect_job_responses()` helper (Task 7)
- [ ] Add `_monitor_job_progress()` helper (Task 7)
- [ ] Add backward compatibility fallback (Task 9)

### Supporting Code
- [ ] Add exception classes (Task 8)
- [ ] Add `_send_peer_message()` helper (reusable by both worker and client)
- [ ] Update CLI with new options (Task 10 - optional)

### Testing
- [ ] Add test for worker registration with metadata
- [ ] Add test for worker discovery
- [ ] Add test for status updates
- [ ] Add integration test for full job workflow

---

## Example Usage After Implementation

**Start Worker:**
```bash
# Worker automatically advertises GPU capabilities
sleap-rtc worker --gpu 0 --room my-lab-room --token abc123
```

**Submit Training Job:**
```bash
# Client automatically discovers and selects best worker
sleap-rtc train my_data.slp --min-gpu-memory 8192 --room my-lab-room --token abc123
```

**Output:**
```
Discovering workers...
Found 2 available workers:
  - Worker-GPU1 (16GB, RTX 4090, estimate: 42 min)
  - Worker-GPU2 (24GB, RTX 6000, estimate: 38 min)
Selected Worker-GPU2 (faster estimate)
Establishing connection...
Progress: 25.0% - Training epoch 25/100
Progress: 50.0% - Training epoch 50/100
Progress: 75.0% - Training epoch 75/100
Job completed successfully!
Final loss: 0.0189
Duration: 37.2 minutes
Model saved to: trained_model.h5
```
