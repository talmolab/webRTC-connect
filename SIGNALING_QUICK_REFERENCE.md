# Signaling Server - Quick Reference Guide

## Key Files
- **Main Server:** `/Users/amickl/repos/webRTC-connect/webRTC_external/server.py` (437 lines)
- **Reference Client:** `/Users/amickl/repos/webRTC-connect/webRTC_external/client.py` (294 lines)
- **Detailed Architecture:** `/Users/amickl/repos/webRTC-connect/SIGNALING_ARCHITECTURE.md` (full reference)

## Quick Facts

### Room Management
- Rooms created via HTTP API with 2-hour TTL
- Stored in DynamoDB for persistence
- Loaded into memory (ROOMS dict) when peers join
- Data structure: `{ room_id: { created_by, token, expires_at, peers: { peer_id: websocket } } }`

### Peers
- Identified by `peer_id` (e.g., "Worker-3108", "Client-1489")
- Authenticated via AWS Cognito `id_token` (JWT)
- Bidirectional mapping: `PEER_TO_ROOM`, `ROOMS[room_id]["peers"]`

### Message Types
| Type | Handler | Purpose |
|------|---------|---------|
| `register` | `handle_register()` | Peer joins room |
| `offer` | `forward_message()` | WebRTC SDP offer |
| `answer` | `forward_message()` | WebRTC SDP answer |
| `candidate` | `forward_message()` | ICE candidate |
| `registered_auth` | Server response | Registration success |
| `error` | Server response | Error notification |

### HTTP Endpoints
- `POST /anonymous-signin` - Get temporary Cognito ID token
- `POST /create-room` - Create new room (requires Bearer token)
- `POST /delete-peers-and-room` - Clean up peers and room

### WebSocket Server
- Port: 8080 (configurable)
- Handler: `handle_client()` processes messages
- Forwarding: `forward_message()` relays between peers

### Authentication Flow
1. Anonymous sign-in → get `id_token`
2. Create room with `id_token` → get `room_id` + `token`
3. Register peer with both → join room

### Deployment
- **Infrastructure:** Terraform (modules/signaling-server/)
- **Ports:** 8001 (HTTP), 8080 (WebSocket)
- **Container:** Docker on EC2 with Elastic IP
- **AWS Services:** Cognito (auth), DynamoDB (rooms), EC2 (compute)

## Example Message Flows

### Registration
```json
Client sends:
{ "type": "register", "peer_id": "Client-123", "room_id": "abc12", "token": "xyz", "id_token": "..." }

Server responds:
{ "type": "registered_auth", "peer_id": "Client-123", "room_id": "abc12", "token": "xyz" }
```

### SDP Exchange
```json
Client sends:
{ "type": "offer", "sender": "Client-123", "target": "Worker-456", "sdp": "v=0..." }

Server forwards to Worker, Worker responds:
{ "type": "answer", "sender": "Worker-456", "target": "Client-123", "sdp": "v=0..." }

Server forwards back to Client
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│              AWS Signaling Server (EC2)                 │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │           FastAPI Application                   │   │
│  │                                                 │   │
│  │  HTTP Endpoints (Port 8001)                     │   │
│  │  • POST /anonymous-signin                       │   │
│  │  • POST /create-room                            │   │
│  │  • POST /delete-peers-and-room                  │   │
│  │                                                 │   │
│  │  WebSocket Server (Port 8080)                   │   │
│  │  • handle_client() - message router             │   │
│  │  • handle_register() - peer registration        │   │
│  │  • forward_message() - message relay            │   │
│  └─────────────────────────────────────────────────┘   │
│            ▲ ROOMS (in-memory)                          │
│            │ PEER_TO_ROOM (mappings)                    │
│            │                                             │
│  ┌─────────▼─────────────────────────────────────────┐  │
│  │      AWS Services                                │  │
│  │  • DynamoDB - Room Persistence                  │  │
│  │  • Cognito - JWT Token Verification             │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
       ▲                                        ▲
       │ HTTP + WebSocket                      │ HTTP + WebSocket
       │                                        │
  ┌────┴────┐                              ┌───┴─────┐
  │  Client │                              │  Worker │
  │(aiortc) │◄──────── Room ────────────────│(aiortc) │
  └─────────┘    WebSocket Signaling       └─────────┘
       │                                        │
       │◄───────── P2P Data Channel ──────────►│
       │        (Direct Connection)             │
       │   (Not through Signaling Server)       │
       └───────────────────────────────────────┘
```

## Known Limitations
- No peer metadata beyond peer_id
- Rooms in memory only (not replicated across servers)
- No peer discovery/query feature (commented out)
- All peers in room can message each other (no channels)
- Basic error handling (silent failures possible)

## Data Structures

### ROOMS Dictionary
```python
ROOMS = {
    "abc12345": {
        "created_by": "cognito-user-id",
        "token": "xyz789",
        "expires_at": 1699564800,  # Unix timestamp
        "peers": {
            "Worker-3108": <WebSocketServerProtocol>,
            "Client-1489": <WebSocketServerProtocol>
        }
    }
}
```

### PEER_TO_ROOM Mapping
```python
PEER_TO_ROOM = {
    "Worker-3108": "abc12345",
    "Client-1489": "abc12345"
}
```

### Room Document (DynamoDB)
```json
{
    "room_id": "abc12345",
    "created_by": "cognito-user-id",
    "token": "xyz789",
    "expires_at": 1699564800
}
```

## Common Workflows

### Initialize Connection
1. Client: POST /anonymous-signin
2. Server: Return id_token, username
3. Client: POST /create-room (Bearer: id_token)
4. Server: Return room_id, token
5. Client: WebSocket connect + register message
6. Server: Validate + respond with registered_auth

### Exchange SDP & ICE
1. Client: WebSocket send offer (sender, target, sdp)
2. Server: forward_message() routes to target
3. Worker: Receives offer → sends answer
4. Server: forward_message() routes answer back
5. Both: Exchange ICE candidates via forward_message()
6. Result: P2P connection established

### Cleanup
1. Any peer calls: POST /delete-peers-and-room (peer_id)
2. Server: Delete from Cognito, remove from PEER_TO_ROOM
3. If room empty: Delete from ROOMS and DynamoDB
4. Return: success status
