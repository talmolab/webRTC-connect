# WebRTC Signaling Server Architecture Summary

## Overview
The signaling server is a Python FastAPI application that facilitates WebRTC connection establishment between peers (workers and clients). It handles room management, peer registration, SDP offer/answer exchange, and ICE candidate forwarding.

**Tech Stack:**
- Framework: FastAPI + Uvicorn
- WebSocket: websockets library
- Authentication: AWS Cognito (JWT tokens)
- Room Persistence: AWS DynamoDB
- Language: Python 3.12

---

## 1. ROOM MANAGEMENT

### Data Structures
Located in `/Users/amickl/repos/webRTC-connect/webRTC_external/server.py` (lines 31-33):

```python
# Global variables to store rooms and peer connections for websocket objects.
ROOMS = {}  # Structure: { room_id: { created_by, token, expires_at, peers: { peer_id: websocket } } }
PEER_TO_ROOM = {}  # Reverse mapping: { peer_id: room_id }
```

### Room Lifecycle

#### 1. Room Creation (HTTP POST)
**Endpoint:** `POST /create-room`
**File:** `/Users/amickl/repos/webRTC-connect/webRTC_external/server.py` (lines 149-175)

```python
@app.post("/create-room")
async def create_room(authorization: str = Header(...)):
    """Creates a new room and returns the room ID and token."""
    # Extract token from Authorization header
    token = authorization.replace("Bearer ", "") 
    
    # Cognito ID token verification.
    claims = verify_cognito_token(token)
    uid = claims["sub"]  # User ID from Cognito
    
    # Generate unique room ID and token
    room_id = str(uuid.uuid4())[:8]  # e.g., "room-7462"
    token = str(uuid.uuid4())[:6]    # e.g., "abc123"
    expires_at = int((datetime.utcnow() + timedelta(hours=2)).timestamp())  # 2-hour TTL
    
    # Persist to DynamoDB
    item = {
        "room_id": room_id,
        "created_by": uid,
        "token": token,
        "expires_at": expires_at
    }
    rooms_table.put_item(Item=item)
    
    return { "room_id": room_id, "token": token }
```

**Room Document Structure (DynamoDB):**
```json
{
    "room_id": "abc12345",
    "created_by": "cognito-user-id-xxx",
    "token": "xyz789",
    "expires_at": 1699564800
}
```

#### 2. Room Validation & In-Memory Storage
**Function:** `handle_register()` (lines 178-260)

When a peer joins via WebSocket, the room is validated and stored in memory:

```python
# Fetch room from DynamoDB
response = rooms_table.get_item(Key={"room_id": room_id})
room_data = response.get('Item')

# Validate room exists, token matches, and not expired
if not room_data:
    await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
    return

if token != room_data.get("token"):
    await websocket.send(json.dumps({"type": "error", "reason": "Invalid token"}))
    return

if time.time() > room_data.get("expires_at"):
    await websocket.send(json.dumps({"type": "error", "reason": "Room expired"}))
    return

# Create in-memory room with websocket objects (can't store in DB)
if room_id not in ROOMS:
    ROOMS[room_id] = {
        "created_by": uid,
        "token": room_data["token"],
        "expires_at": room_data["expires_at"],
        "peers": {}
    }

# Register peer
ROOMS[room_id]["peers"][peer_id] = websocket
PEER_TO_ROOM[peer_id] = room_id
```

**In-Memory Structure:**
```
ROOMS = {
    "abc12345": {
        "created_by": "cognito-user-xxx",
        "token": "xyz789",
        "expires_at": 1699564800,
        "peers": {
            "Worker-3108": <WebSocketServerProtocol>,
            "Client-1489": <WebSocketServerProtocol>
        }
    }
}

PEER_TO_ROOM = {
    "Worker-3108": "abc12345",
    "Client-1489": "abc12345"
}
```

#### 3. Room Cleanup
**Endpoint:** `POST /delete-peers-and-room`
**File:** lines 65-105

```python
@app.post("/delete-peers-and-room")
async def delete_peer_and_room(json_data: dict):
    """Deletes all peers from their room and cleans up if the room is empty."""
    
    peer_id = json_data.get("peer_id")
    room_id = PEER_TO_ROOM.get(peer_id)
    
    if not room_id:
        return {"status": "peer not found"}
    
    room = ROOMS.get(room_id)
    if not room:
        return {"status": "room not found"}
    
    # Delete all users in the room from Cognito
    peer_ids = list(room["peers"].keys())
    for pid in peer_ids:
        try:
            cognito_client.admin_delete_user(
                UserPoolId=COGNITO_USER_POOL_ID,
                Username=pid
            )
            del PEER_TO_ROOM[pid]
        except Exception as e:
            logging.error(f"Failed to delete Cognito user: {e}")
    
    # If room empty, delete from memory and DynamoDB
    if not room["peers"]:
        del ROOMS[room_id]
        rooms_table.delete_item(Key={"room_id": room_id})
    
    return {"status": "peer deleted successfully"}
```

---

## 2. PEER CONNECTIONS & COMMUNICATION

### Peer Registration
**Message Type:** `"register"`
**Handler:** `handle_register()` (lines 178-260)

**Message Format:**
```json
{
    "type": "register",
    "peer_id": "Worker-3108",
    "room_id": "abc12345",
    "token": "xyz789",
    "id_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:**
```json
{
    "type": "registered_auth",
    "room_id": "abc12345",
    "token": "xyz789",
    "peer_id": "Worker-3108"
}
```

### Peer Identification
Two identifiers per peer:
- **peer_id** (string): Unique identifier within a room (e.g., "Worker-3108", "Client-1489")
  - Format convention: `{role}-{random_number}` (determined by client)
  - Used to identify sender/target in messages
  
- **id_token** (JWT): Cognito authentication token
  - Prevents peer spoofing
  - Generated via anonymous sign-in endpoint

### Message Forwarding
**Function:** `forward_message()` (lines 263-290)

The server forwards SDP and ICE messages between peers:

```python
async def forward_message(sender_pid: str, target_pid: str, data):
    """Forward a message from one peer to another."""
    
    room_id = PEER_TO_ROOM.get(sender_pid)
    if not room_id:
        logging.error(f"Room not found for peer {sender_pid}")
        return
    
    room = ROOMS.get(room_id)
    if not room:
        logging.error(f"Room {room_id} not found in memory.")
        return
    
    # Get target peer's websocket
    target_websocket = room["peers"].get(target_pid)
    
    try:
        logging.info(f"Forwarding message from {sender_pid} to {target_pid}: {data}")
        await target_websocket.send(json.dumps(data))
    except:
        logging.error(f"Failed to send message from {sender_pid} to {target_pid}. It may have disconnected.")
```

---

## 3. MESSAGE TYPES SUPPORTED

### Registration Messages

| Type | Direction | Usage |
|------|-----------|-------|
| `register` | Client → Server | Peer joins room |
| `registered_auth` | Server → Client | Confirmation with details |

### SDP Exchange Messages

| Type | Direction | Usage |
|------|-----------|-------|
| `offer` | Client → Server | WebRTC offer (initiates connection) |
| `answer` | Server → Client | WebRTC answer (accepts connection) |

### ICE Candidate Messages

| Type | Direction | Usage |
|------|-----------|-------|
| `candidate` | Bidirectional | ICE candidate for NAT traversal |

### Query Messages (Commented Out)

```python
# These are defined but currently commented out:
# elif msg_type == "query":
#     # send available peers to client terminal via websocket
#     response = {'type': 'available_peers', 'peers': list(connected_peers.keys())}
#     await websocket.send(json.dumps(response))
```

### Error Messages

```json
{
    "type": "error",
    "reason": "Missing required fields during registration."
}
```

### Message Handler
**Function:** `handle_client()` (lines 315-410)

```python
async def handle_client(websocket):
    """Handles incoming messages between peers to facilitate exchange of SDP & ICE candidates."""
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                logging.info(f"Received message: {data}")
                
                if msg_type == "register":
                    await handle_register(websocket, data)
                
                elif msg_type in ["offer", "answer"]:
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')
                    
                    if not sender_pid or not target_pid:
                        logging.info("Missing sender or target peer ID")
                        continue
                    
                    await forward_message(sender_pid, target_pid, data)
            
            except json.JSONDecodeError:
                logging.info("Invalid JSON received")
    
    except websockets.exceptions.ConnectionClosedOK:
        logging.info("Client disconnected cleanly.")
    
    except Exception as e:
        logging.info(f"Error handling client: {e}")
    
    finally:
        logging.info("Exiting server...")
```

---

## 4. API STRUCTURE

### HTTP API (FastAPI)
**Server:** Runs on port 8001 (configurable via terraform.tfvars)

#### Endpoints

1. **POST /anonymous-signin**
   - Purpose: Create anonymous Cognito user and get ID token
   - Returns: `{ "id_token": "...", "username": "..." }`
   - Used before room creation or peer registration

2. **POST /create-room** (requires Bearer token)
   - Purpose: Create new WebRTC room with 2-hour TTL
   - Returns: `{ "room_id": "abc12345", "token": "xyz789" }`
   - Only callable with valid Cognito ID token

3. **POST /delete-peers-and-room**
   - Purpose: Clean up peers and optionally delete empty room
   - Request body: `{ "peer_id": "Worker-3108" }`
   - Returns: `{ "status": "peer deleted successfully" }`

### WebSocket API
**Server:** Runs on port 8080 (configurable via terraform.tfvars)

**Connection:** `ws://server-ip:8080`

**Flow:**
1. Client connects
2. Client sends `register` message
3. Server validates and responds with `registered_auth`
4. Client sends/receives `offer`, `answer`, `candidate` messages
5. Client disconnects

---

## 5. PEER ROLES & METADATA

### Current Peer Roles
The system supports two types of peers:
- **Worker** (e.g., "Worker-3108"): GPU-equipped training node
- **Client** (e.g., "Client-1489"): User/remote client

### Peer Metadata
Currently stored as strings in the room structure. No explicit metadata objects:

```python
peer_id  # String like "Worker-3108" or "Client-1489"
```

The naming convention encodes the role, but there's no separate metadata object.

### No Explicit Peer Metadata Storage
The current implementation doesn't store peer metadata beyond:
- peer_id (string identifier)
- room_id (association)
- websocket (connection)
- id_token (for authentication)

---

## 6. DEPLOYMENT ARCHITECTURE

### Infrastructure Files
- **Terraform Module:** `/Users/amickl/repos/webRTC-connect/terraform/modules/signaling-server/main.tf`
- **Dev Environment:** `/Users/amickl/repos/webRTC-connect/terraform/environments/dev/`
- **Production Environment:** `/Users/amickl/repos/webRTC-connect/terraform/environments/production/`

### Deployment Configuration
- **Container:** Runs signaling server in Docker container on EC2
- **HTTP Port:** 8001 (FastAPI/Uvicorn)
- **WebSocket Port:** 8080 (WebSocket server)
- **Elastic IP:** Provides stable IP across instance replacements
- **Auto-startup:** Container starts automatically on EC2 boot
- **Security Group:** Configurable access controls for ports 8080, 8001, 22 (SSH)

### Terraform Variables (from terraform.tfvars.example)
```hcl
aws_region                = "us-west-1"
instance_type             = "t3.small"
docker_image              = "ghcr.io/talmolab/webrtc-server:linux-amd64-latest"
cognito_region            = "us-west-1"
cognito_user_pool_id      = "us-west-1_XXXXXXXXX"
cognito_app_client_id     = "xxxxxxxxxxxxxxxxxxxxxx"
allowed_cidr_blocks       = ["0.0.0.0/0"]  # Open in dev, restrict in production
websocket_port            = 8080
http_port                 = 8001
```

---

## 7. AUTHENTICATION FLOW

### Cognito Integration
1. **Anonymous Sign-in** (no credentials needed)
   - POST `/anonymous-signin` → Cognito creates random username/password
   - Returns JWT `id_token` for peer authentication

2. **Room Creation**
   - POST `/create-room` with Bearer token
   - Token verified against Cognito JWKS
   - Creates room with creator's user ID

3. **Peer Registration**
   - Peer sends `id_token` during registration
   - Server verifies token against Cognito JWKS
   - Prevents peer spoofing even for anonymous users

### Token Verification
**Function:** `verify_cognito_token()` (lines 51-62)

```python
def verify_cognito_token(token):
    try:
        claims = jwt.decode(
            token,
            JWKS,
            algorithms=["RS256"],
            audience=COGNITO_APP_CLIENT_ID,
            issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        )
        return claims
    except jwt.JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {e}")
```

---

## 8. KEY CODE FILES

| File Path | Purpose |
|-----------|---------|
| `/Users/amickl/repos/webRTC-connect/webRTC_external/server.py` | Main signaling server (437 lines) |
| `/Users/amickl/repos/webRTC-connect/webRTC_external/client.py` | Reference WebRTC client implementation (294 lines) |
| `/Users/amickl/repos/webRTC-connect/webRTC_external/requirements.txt` | Python dependencies |
| `/Users/amickl/repos/webRTC-connect/terraform/modules/signaling-server/main.tf` | AWS infrastructure definition |
| `/Users/amickl/repos/webRTC-connect/openspec/project.md` | Project conventions & architecture patterns |

---

## 9. CURRENT LIMITATIONS & DESIGN NOTES

1. **No Peer Metadata System**
   - Peer identity encoded in peer_id string only
   - No structured metadata objects
   - No role-based access control

2. **Single Room Limitation**
   - All registered peers in a room can message any other peer
   - No channel concepts or grouping within rooms

3. **In-Memory Room Storage**
   - Rooms stored in memory (ROOMS dict)
   - Shared state across all connections
   - Lost on server restart
   - Not suitable for multi-server deployments

4. **Commented-Out Query Feature**
   - "query" message type for discovering available peers (commented out)
   - Could be useful for client peer discovery

5. **Error Handling**
   - Basic exception handling in message forwarding
   - Silent failures if peer disconnects during forwarding

---

## 10. EXAMPLE COMMUNICATION FLOW

```
Client                    Signaling Server              Worker
  |                             |                          |
  |--- POST /anonymous-signin ->|                          |
  |<- { id_token, username } ---|                          |
  |                             |                          |
  |--- POST /create-room ------>|                          |
  |<- { room_id, token } --------|                          |
  |                             |                          |
  |--- WebSocket connect ------->|                          |
  |--- register (json) --------->|                          |
  |<- registered_auth ----------|                          |
  |                             |<--- WebSocket connect ---
  |                             |<--- register (json) ----
  |                             |--- registered_auth ----->
  |                             |                          |
  |--- offer (json) ----------->|--- offer -----+          |
  |                             |               v          |
  |                             |<-- answer ---+           |
  |<- answer (json) -----------|                          |
  |                             |                          |
  |--- candidate -------+------>|--- candidate -+          |
  |                    v        |              v          |
  |                    +- candidate <-- candidate -------
  |                                                        |
  |<==== WebRTC P2P Data Channel Established ============>|
```

