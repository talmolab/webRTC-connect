# Signaling Server API v2.0

## Overview

The signaling server provides a generic peer discovery and messaging infrastructure for WebRTC applications. It is **application-agnostic** - it does not know about training jobs, models, or any SLEAP-specific logic.

**Version:** 2.0.0
**Ports:**
- WebSocket: 8080
- HTTP API: 8001

---

## Key Concepts

### Peers
Individual participants in a room. Each peer has:
- **peer_id**: Unique identifier (e.g., "Worker-3108", "Client-1489")
- **role**: Optional role string (e.g., "worker", "client", "peer")
- **metadata**: Arbitrary JSON data for application use

### Rooms
Logical groups of peers. Rooms:
- Have a 2-hour TTL (time-to-live)
- Are identified by `room_id`
- Require a `token` for peers to join
- Are stored in DynamoDB for persistence

### Message Routing
The signaling server routes messages between peers without interpreting their content.

---

## HTTP API Endpoints

### 1. Anonymous Sign-In

**Endpoint:** `POST /anonymous-signin`

**Description:** Creates an anonymous Cognito user and returns authentication token.

**Request:**
```json
{}
```

**Response:**
```json
{
  "id_token": "eyJra...",
  "username": "uuid-generated-username"
}
```

---

### 2. Create Room

**Endpoint:** `POST /create-room`

**Description:** Creates a new room with 2-hour expiration.

**Headers:**
```
Authorization: Bearer <id_token>
```

**Response:**
```json
{
  "room_id": "abc123de",
  "token": "f7g8h9"
}
```

---

### 3. Delete Peers and Room

**Endpoint:** `POST /delete-peers-and-room`

**Description:** Removes all peers from a room and cleans up resources.

**Request:**
```json
{
  "peer_id": "Worker-3108"
}
```

**Response:**
```json
{
  "status": "peer deleted successfully"
}
```

---

### 4. Health Check

**Endpoint:** `GET /health`

**Description:** Health check for monitoring and load balancers.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-11-03T10:30:00Z",
  "version": "2.0.0"
}
```

---

### 5. Metrics

**Endpoint:** `GET /metrics`

**Description:** System-wide metrics for monitoring (public, no auth required).

**Response:**
```json
{
  "timestamp": "2025-11-03T10:30:00Z",
  "active_rooms": 3,
  "active_connections": 12,
  "peers_by_role": {
    "worker": 5,
    "client": 7
  },
  "total_connections": 147,
  "total_messages": 3421,
  "rooms_created": 23
}
```

---

## WebSocket Messages

### Connection
**URL:** `ws://signaling-server:8080/`

All messages are JSON-formatted.

---

### 1. Register (Enhanced)

**Direction:** Client → Server

**Description:** Register peer in a room with optional role and metadata.

**Message:**
```json
{
  "type": "register",
  "peer_id": "Worker-3108",
  "room_id": "abc123de",
  "token": "f7g8h9",
  "id_token": "eyJra...",
  "role": "worker",
  "metadata": {
    "tags": ["gpu", "training", "inference"],
    "properties": {
      "gpu_memory_mb": 16384,
      "cuda_version": "11.8",
      "sleap_version": "1.3.0",
      "supported_models": ["base", "centroid"]
    }
  }
}
```

**Response:**
```json
{
  "type": "registered_auth",
  "room_id": "abc123de",
  "token": "f7g8h9",
  "peer_id": "Worker-3108"
}
```

**Notes:**
- `role` and `metadata` are optional (default to "peer" and `{}`)
- Backward compatible: existing clients without role/metadata still work
- Server does NOT interpret metadata content

---

### 2. Discover Peers (NEW)

**Direction:** Client → Server

**Description:** Find peers in the same room matching filter criteria.

**Message:**
```json
{
  "type": "discover_peers",
  "from_peer_id": "Client-1489",
  "filters": {
    "role": "worker",
    "tags": ["gpu", "training"],
    "properties": {
      "gpu_memory_mb": {"$gte": 8192},
      "sleap_version": "1.3.0"
    }
  }
}
```

**Filter Operators:**
- `$gte`: Greater than or equal
- `$lte`: Less than or equal
- `$eq`: Equal (or just use direct value)

**Response:**
```json
{
  "type": "peer_list",
  "to_peer_id": "Client-1489",
  "count": 2,
  "peers": [
    {
      "peer_id": "Worker-3108",
      "role": "worker",
      "metadata": {
        "tags": ["gpu", "training", "inference"],
        "properties": {
          "gpu_memory_mb": 16384,
          "cuda_version": "11.8"
        }
      },
      "connected_at": 1699120000
    },
    {
      "peer_id": "Worker-7492",
      "role": "worker",
      "metadata": {...},
      "connected_at": 1699120100
    }
  ]
}
```

---

### 3. Peer Message (NEW)

**Direction:** Peer → Server → Peer

**Description:** Send generic application-specific message to another peer.

**Message:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    // Application-specific data (NOT interpreted by server)
    "app_message_type": "job_request",
    "job_id": "uuid-123",
    "job_spec": {
      "type": "training",
      "dataset": "data.slp",
      "config": {...}
    }
  }
}
```

**Delivered to target as:**
```json
{
  "type": "peer_message",
  "from_peer_id": "Client-1489",
  "to_peer_id": "Worker-3108",
  "payload": {
    // Exact payload passed through unchanged
  }
}
```

**Notes:**
- Server validates sender and target are in same room
- Server does NOT interpret or validate `payload` content
- Application protocol goes inside `payload`

---

### 4. WebRTC Signaling (Existing)

**Offer:**
```json
{
  "type": "offer",
  "sender": "Client-1489",
  "target": "Worker-3108",
  "sdp": "v=0\r\no=- ..."
}
```

**Answer:**
```json
{
  "type": "answer",
  "sender": "Worker-3108",
  "target": "Client-1489",
  "sdp": "v=0\r\no=- ..."
}
```

**ICE Candidate:**
```json
{
  "type": "candidate",
  "sender": "Worker-3108",
  "target": "Client-1489",
  "candidate": "candidate:..."
}
```

---

### 5. Error Messages

**Format:**
```json
{
  "type": "error",
  "code": "ERROR_CODE",
  "message": "Human-readable error message"
}
```

**Error Codes:**
- `NOT_IN_ROOM`: Peer not registered in any room
- `ROOM_NOT_FOUND`: Room doesn't exist
- `PEER_NOT_IN_ROOM`: Target peer not in same room
- `PEER_NOT_FOUND`: Target peer doesn't exist
- `INVALID_MESSAGE`: Missing required fields
- `DELIVERY_FAILED`: Failed to deliver message
- `UNKNOWN_MESSAGE_TYPE`: Message type not recognized
- `INVALID_JSON`: Malformed JSON

---

## Connection Lifecycle

### 1. Connect
```
Client connects to ws://signaling-server:8080/
```

### 2. Authenticate & Join Room
```json
{
  "type": "register",
  "peer_id": "Worker-3108",
  "room_id": "abc123",
  "token": "f7g8h9",
  "id_token": "cognito_token",
  "role": "worker",
  "metadata": {...}
}
```

### 3. Discover Peers
```json
{
  "type": "discover_peers",
  "from_peer_id": "Worker-3108",
  "filters": {"role": "client"}
}
```

### 4. Exchange Messages
```json
{
  "type": "peer_message",
  "from_peer_id": "Worker-3108",
  "to_peer_id": "Client-1489",
  "payload": {...}
}
```

### 5. Establish WebRTC Connection
```json
{
  "type": "offer",
  "sender": "Client-1489",
  "target": "Worker-3108",
  "sdp": "..."
}
```

### 6. Disconnect
```
WebSocket close
```
Server automatically removes peer from room and cleans up if room is empty.

---

## Backward Compatibility

### Existing Clients (v1.x)
Clients that don't send `role` or `metadata` still work:
- Default role: `"peer"`
- Default metadata: `{}`
- All existing offer/answer/candidate messages unchanged

### Migration Path
1. Deploy v2.0 server (backward compatible)
2. Update clients to use new features as needed
3. Old and new clients can coexist in same room

---

## Security

### Authentication
- Cognito JWT tokens required for all operations
- Room tokens prevent unauthorized joining
- Peer-to-peer messages validated (same room check)

### Privacy
- Server does not log message payloads
- Metadata is application-controlled (server doesn't interpret)
- No sensitive data stored permanently

### Rate Limiting
- Consider adding rate limits in production
- Monitor `/metrics` endpoint for abuse

---

## Monitoring

### Health Check
```bash
curl http://signaling-server:8001/health
```

### Metrics
```bash
curl http://signaling-server:8001/metrics
```

### CloudWatch Logs
- All connections/disconnections logged
- Errors logged with context
- Message routing logged (type only, not payload)

---

## Scaling Considerations

### Current Architecture (v2.0)
- **Single instance**: 500-1000 concurrent connections
- **In-memory state**: ROOMS and PEER_TO_ROOM dictionaries
- **No persistence**: Restart loses active connections

### Future Scaling (See `docs/SCALING_ARCHITECTURE.md`)
- Redis-backed state for horizontal scaling
- Application Load Balancer for multi-instance deployment
- DynamoDB for persistent room state

---

## Example Usage

See `examples/` directory for:
- `python_client_example.py` - Python WebSocket client
- `worker_example.py` - Worker registration and discovery
- `client_example.py` - Client job submission flow

---

## Related Documentation

- [`SCALING_ARCHITECTURE.md`](./SCALING_ARCHITECTURE.md) - Scaling strategies
- [`ADMIN_SECURITY.md`](./ADMIN_SECURITY.md) - Admin dashboard and security
- [`SLEAP_RTC_PROTOCOL.md`](./SLEAP_RTC_PROTOCOL.md) - Application protocol spec
