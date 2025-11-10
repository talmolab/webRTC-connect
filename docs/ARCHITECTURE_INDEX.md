# Signaling Server Architecture Documentation Index

Complete analysis of the webRTC signaling server architecture for the sleap-webRTC project.

## Documentation Files

### 1. SIGNALING_ARCHITECTURE.md (16 KB)
Comprehensive technical reference with detailed explanations, code examples, and diagrams.

**Contents:**
- Room management system (creation, validation, cleanup)
- Peer connections and communication patterns
- All supported message types
- HTTP and WebSocket API specifications
- Peer roles and metadata
- Deployment architecture
- Authentication flow
- Current limitations
- Communication flow diagrams

**Best for:** Understanding the complete system, implementing features, debugging

### 2. SIGNALING_QUICK_REFERENCE.md (7.4 KB)
Quick lookup guide with tables, data structures, and common workflows.

**Contents:**
- Quick facts and room management summary
- Message type lookup table
- HTTP endpoints summary
- WebSocket server info
- Architecture diagram
- Data structure definitions
- Common workflows (4 key scenarios)
- Known limitations

**Best for:** Quick lookups, refreshing memory, onboarding new developers

### 3. ARCHITECTURE_INDEX.md (This File)
Navigation guide to all documentation.

**Contents:**
- Overview of all documentation
- Quick navigation
- Key takeaways
- Code file references

**Best for:** Finding what you need quickly

## Key Files in the Codebase

### Main Implementation
- **webRTC_external/server.py** (437 lines)
  - Lines 31-33: Global data structures (ROOMS, PEER_TO_ROOM)
  - Lines 51-62: Token verification
  - Lines 65-105: Room deletion
  - Lines 108-146: Anonymous sign-in
  - Lines 149-175: Room creation
  - Lines 178-260: Peer registration
  - Lines 263-290: Message forwarding
  - Lines 315-410: Main message handler
  - Lines 412-437: Server initialization

### Reference Implementation
- **webRTC_external/client.py** (294 lines)
  - Demonstrates peer registration flow
  - Shows SDP offer/answer exchange
  - Includes ICE candidate handling
  - Data channel usage example

### Infrastructure
- **terraform/modules/signaling-server/main.tf** (179 lines)
  - EC2 instance configuration
  - Security group definition
  - IAM role and policies
  - Elastic IP management
  - Docker container setup

## Quick Navigation

### Need to understand...

**Room Management?**
- Read: SIGNALING_ARCHITECTURE.md Section 1
- Code: server.py lines 31-33, 149-175, 178-260, 65-105
- Reference: SIGNALING_QUICK_REFERENCE.md "ROOMS MANAGEMENT" table

**Peer Registration?**
- Read: SIGNALING_ARCHITECTURE.md Section 2
- Code: server.py lines 178-260 (handle_register)
- Reference: SIGNALING_QUICK_REFERENCE.md "MESSAGE ROUTING" table

**Message Protocol?**
- Read: SIGNALING_ARCHITECTURE.md Section 3
- Reference: SIGNALING_QUICK_REFERENCE.md "MESSAGE ROUTING" table
- Code: server.py lines 315-410 (handle_client), 263-290 (forward_message)

**API Endpoints?**
- Read: SIGNALING_ARCHITECTURE.md Section 4
- Reference: SIGNALING_QUICK_REFERENCE.md "HTTP Endpoints" section
- Code: server.py lines 108-175

**Authentication?**
- Read: SIGNALING_ARCHITECTURE.md Section 7
- Reference: SIGNALING_QUICK_REFERENCE.md "AUTHENTICATION CHAIN"
- Code: server.py lines 51-62, 108-146, 149-175

**Deployment?**
- Read: SIGNALING_ARCHITECTURE.md Section 6
- Reference: SIGNALING_QUICK_REFERENCE.md "Deployment" section
- Code: terraform/modules/signaling-server/main.tf

## Key Concepts at a Glance

### Room Structure
```python
ROOMS[room_id] = {
    "created_by": "cognito-user-id",
    "token": "xyz789",           # Like meeting password
    "expires_at": 1699564800,    # 2-hour TTL
    "peers": {
        "Worker-3108": <websocket>,
        "Client-1489": <websocket>
    }
}
```

### Message Flow
1. **Register:** Peer -> Server -> Store in room
2. **Offer:** Client -> Server -> Forward to Worker
3. **Answer:** Worker -> Server -> Forward to Client
4. **Candidate:** Bidirectional forwarding
5. **P2P Data:** Direct connection (not through server)

### Authentication Chain
1. POST /anonymous-signin → get `id_token`
2. POST /create-room → get `room_id` + `token`
3. WebSocket register → join room with `id_token`

### Supported Peer Roles
- **Worker:** GPU-equipped training node (e.g., "Worker-3108")
- **Client:** Remote user/client (e.g., "Client-1489")

## Technical Stack

- **Language:** Python 3.12
- **Framework:** FastAPI + Uvicorn
- **WebSocket:** websockets library
- **Authentication:** AWS Cognito (JWT)
- **Persistence:** AWS DynamoDB
- **Infrastructure:** Terraform + AWS EC2
- **Container:** Docker

## Ports

- **8001:** HTTP API (FastAPI) - Room creation, authentication, cleanup
- **8080:** WebSocket - Peer registration, message forwarding
- **22:** SSH - Administrative access

## Key Data Flows

### Room Creation Flow
```
Client -> /anonymous-signin -> id_token
       -> /create-room (Bearer) -> room_id + token
       -> Stored in DynamoDB
```

### Peer Registration Flow
```
Peer -> WebSocket connect
     -> register message (peer_id, room_id, token, id_token)
     -> Server validates room (DynamoDB check)
     -> Server registers in ROOMS[room_id]["peers"]
     -> registered_auth response
```

### Message Forward Flow
```
Sender -> WebSocket message (type, sender, target, data)
       -> forward_message() lookup
       -> Find target websocket in ROOMS[room_id]["peers"]
       -> Send to target websocket
```

## Current Limitations

1. **No peer metadata** - Only peer_id string, no structured data
2. **Single room mode** - All peers can message any other peer
3. **In-memory storage** - Rooms lost on restart, not replicated
4. **Query disabled** - Peer discovery feature exists but commented out
5. **Basic error handling** - Silent failures in message forwarding

## Next Steps for Development

If you need to:
- **Add peer metadata:** Extend ROOMS structure and registration flow
- **Implement channels:** Add grouping within rooms
- **Scale to multiple servers:** Replace in-memory storage with Redis/cache
- **Enable peer discovery:** Uncomment query handler
- **Improve error handling:** Add retry logic and better error responses

## Document Versions

- **Created:** 2024-11-03
- **Source Repository:** webRTC-connect
- **Current Branch:** amick/terraform-infrastructure
- **Last Updated:** 2024-11-03

## Questions or Corrections?

For detailed information, refer to:
1. SIGNALING_ARCHITECTURE.md - Comprehensive technical reference
2. SIGNALING_QUICK_REFERENCE.md - Quick lookup tables
3. Source code: webRTC_external/server.py - Actual implementation
4. Project specs: openspec/project.md - Architecture conventions
