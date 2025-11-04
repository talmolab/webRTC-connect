# /// script
# dependencies = [
#   "boto3",
#   "fastapi",
#   "python-jose[cryptography]",
#   "requests",
#   "uvicorn",
#   "websockets",
# ]
# ///

import asyncio
import boto3
import requests
import threading
import time
import json
import logging
import websockets
import uvicorn
import uuid
import os

from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header
from jose import jwt

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Global variables to store rooms and peer connections for websocket objects.
# Enhanced structure to support roles and metadata:
# ROOMS[room_id] = {
#     "created_by": uid,
#     "token": token,
#     "expires_at": timestamp,
#     "peers": {
#         peer_id: {
#             "websocket": <WebSocket>,
#             "role": "worker" | "client" | "peer",
#             "metadata": {...},  # Application-specific data
#             "connected_at": timestamp
#         }
#     }
# }
ROOMS = {}
PEER_TO_ROOM = {}

# Metrics tracking
METRICS = {
    "total_connections": 0,
    "total_messages": 0,
    "active_connections": 0,
    "rooms_created": 0
}

# AWS Cognito and DynamoDB initialization/configuration.
COGNITO_REGION = os.environ['COGNITO_REGION']
COGNITO_USER_POOL_ID = os.environ['COGNITO_USER_POOL_ID']
COGNITO_APP_CLIENT_ID = os.environ['COGNITO_APP_CLIENT_ID']
COGNITO_KEYS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
JWKS = requests.get(COGNITO_KEYS_URL).json()["keys"]

# Initialize AWS SDK (boto3 Python API for AWS).
cognito_client = boto3.client('cognito-idp', region_name=COGNITO_REGION)
dynamodb = boto3.resource('dynamodb', region_name=COGNITO_REGION)
rooms_table = dynamodb.Table('rooms')

# FastAPI App (Room Creation + Metrics)
app = FastAPI(title="SLEAP-RTC Signaling Server", version="2.0.0")


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


@app.post("/delete-peers-and-room")
async def delete_peer_and_room(json_data: dict):
    """Deletes all peers from their room and cleans up if the room is empty."""
    # ROOMS[room_id]["peers"]: { Worker-3108: <Worker websocket object>, Client-1489: <Client websocket object> }
    # PEER_TO_ROOM: { Worker-3108: room-7462, Client-1489: room-7462 }

    # Get associated room from peer_id
    peer_id = json_data.get("peer_id")
    room_id = PEER_TO_ROOM.get(peer_id)
    if not room_id:
        return {"status": "peer not found"}
    room = ROOMS.get(room_id)
    if not room:
        return {"status": "room not found"}

    # Delete all Users in the room from Cognito.
    peer_ids = list(room["peers"].keys())
    for pid in peer_ids:
        try:
            # Delete the Cognito user
            cognito_client.admin_delete_user(
                UserPoolId=COGNITO_USER_POOL_ID,
                Username=pid
            )
            logging.info(f"Deleted Cognito user {pid}")

            # Remove peer from PEER_TO_ROOM mapping
            del PEER_TO_ROOM[pid]
        except Exception as e:
            logging.error(f"Failed to delete Cognito user: {e}")

    # If the room has no more peers, delete from memory and DynamoDB.
    if not room["peers"]:
        del ROOMS[room_id]
        try:
            rooms_table.delete_item(Key={"room_id": room_id})
            logging.info(f"Room {room_id} deleted from DynamoDB as it has no more peers.")
        except Exception as e:
            logging.error(f"Failed to delete room {room_id} from DynamoDB: {e}")

    return {"status": "peer deleted successfully"}


@app.post("/anonymous-signin")
async def anonymous_signin():
    """Handles anonymous sign-in and returns a Cognito ID token."""
    
    # Create a random username and password.
    username = str(uuid.uuid4())
    password = f"Aa{uuid.uuid4().hex}!"

    try:
        # Sign up the user with the random credentials.
        response = cognito_client.sign_up(
            ClientId=COGNITO_APP_CLIENT_ID,
            Username=username,
            Password=password
        )

        # If sign-up is successful, the user is confirmed automatically.
        cognito_client.admin_confirm_sign_up(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=username
        )

        # Sign in the user to get tokens.
        response = cognito_client.initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password
            }
        )

        return {
            "id_token": response["AuthenticationResult"]["IdToken"],
            "username": username # return username to later identify for deletion
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Anonymous sign-in failed: {e}")


@app.post("/create-room")
async def create_room(authorization: str = Header(...)):
    """Creates a new room and returns the room ID and token."""
    # Extract token from Authorization header
    token = authorization.replace("Bearer ", "")

    # Cognito ID token verification.
    claims = verify_cognito_token(token)
    uid = claims["sub"]

    # Generate a unique room ID and token to be associated with this verified Cognito ID token.
    room_id = str(uuid.uuid4())[:8]
    token = str(uuid.uuid4())[:6]
    expires_at = int((datetime.utcnow() + timedelta(hours=2)).timestamp())  # 2 hours TTL

    # Create a new room item in DynamoDB.
    item = {
        "room_id": room_id,
        "created_by": uid, # user ID from Cognito ID token
        "token": token,
        "expires_at": expires_at  # 2 hours TTL
    }

    # Store the room in DynamoDB.
    rooms_table.put_item(Item=item)

    # Update metrics
    METRICS["rooms_created"] += 1

    return { "room_id": room_id, "token": token }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0"
    }


@app.get("/metrics")
async def get_metrics():
    """Get system metrics (public endpoint for monitoring)."""
    # Calculate real-time metrics
    total_rooms = len(ROOMS)
    total_peers = sum(len(room["peers"]) for room in ROOMS.values())

    # Count peers by role
    peers_by_role = {}
    for room in ROOMS.values():
        for peer_data in room["peers"].values():
            role = peer_data.get("role", "peer")
            peers_by_role[role] = peers_by_role.get(role, 0) + 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "active_rooms": total_rooms,
        "active_connections": total_peers,
        "peers_by_role": peers_by_role,
        "total_connections": METRICS["total_connections"],
        "total_messages": METRICS["total_messages"],
        "rooms_created": METRICS["rooms_created"]
    }


async def handle_register(websocket, message):
    """Handles the registration of a peer in a room w/ its websocket.

    Enhanced to support role and metadata for generic peer discovery.
    """

    peer_id = message.get('peer_id') # identify a peer uniquely in the room (Zoom username)
    room_id = message.get('room_id') # from backend API call for room identification (Zoom meeting ID)
    token = message.get('token') # from backend API call for room joining (Zoom meeting password)
    id_token = message.get('id_token') # from anon. Cognito sign-in (prevent peer spoofing, even anonymously)

    # NEW: Optional role and metadata for generic signaling
    role = message.get('role', 'peer')  # Default to 'peer' for backward compatibility
    metadata = message.get('metadata', {})  # Arbitrary application data

    # Validate required fields.
    if not all([peer_id, room_id, token, id_token]):
        await websocket.send(json.dumps({"type": "error", "reason": "Missing required fields during registration."}))
        return

    # Verify Cognito ID token (passed from peer Cognito anonymous sign-in).
    try:
        claims = verify_cognito_token(id_token)
        uid = claims["sub"]  # user ID from Cognito ID token
    except Exception as e:
        logging.error(f"Token verification failed: {e}")
        await websocket.send(json.dumps({"error": "Invalid token"}))
        return

    # Can now fetch prev. created DynamoDB room document.
    try:
        # doc = db.collection("rooms").document(room_id).get()
        response = rooms_table.get_item(Key={"room_id": room_id})
        room_data = response.get('Item')

        # Item Format: {
        #     "room_id": room_id,
        #     "created_by": uid, decoded ID token,
        #     "token": token,
        #     "expires_at": time.time() + 10 * 60
        # }

        if not room_data:
            await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
            return

        # If Client calls, should be using Worker's token. Vice versa.
        if token != room_data.get("token"):
            await websocket.send(json.dumps({"type": "error", "reason": "Invalid token"}))
            return

        # Check room expiration.
        if time.time() > room_data.get("expires_at"):
            await websocket.send(json.dumps({"type": "error", "reason": "Room expired"}))
            return

        # 4. Cannot store peer's websocket object directly in document, so keep it in memory.
        # Client should not be able to create a room since Worker would have already created it.
        # Looks the same as DynamoDB document, but in memory and with a 'peers' dict.
        if room_id not in ROOMS:
            ROOMS[room_id] = {
                "created_by": uid,
                "token": room_data["token"],
                "expires_at": room_data["expires_at"],
                "peers": {}
            }
        # Compare token from request with the one stored in DynamoDB.
        # i.e. check "Zoom meeting password" is correct. (Both peer must have same token to join the same room.)

        # NEW: Store peer data object instead of just websocket
        # ROOMS[room_id]["peers"]: {
        #   Worker-3108: {websocket: <ws>, role: "worker", metadata: {...}, connected_at: timestamp}
        # }
        ROOMS[room_id]["peers"][peer_id] = {
            "websocket": websocket,
            "role": role,
            "metadata": metadata,
            "connected_at": time.time()
        }
        PEER_TO_ROOM[peer_id] = room_id

        # Update metrics
        METRICS["total_connections"] += 1
        METRICS["active_connections"] = sum(len(room["peers"]) for room in ROOMS.values())

        # Send registration confirmation to the peer.
        await websocket.send(json.dumps({
            "type": "registered_auth",
            "room_id": room_id,
            "token": token,
            "peer_id": peer_id
        }))

        logging.info(f"[REGISTERED] peer_id: {peer_id} (role: {role}) in room: {room_id}")

    except Exception as e:
        logging.error(f"Failed to fetch room {room_id}: {e}")
        await websocket.send(json.dumps({"type": "error", "reason": "DynamoDB error"}))
        return


async def forward_message(sender_pid: str, target_pid: str, data):
    """Forward a message from one peer to another.

    Args:
        sender_pid (str): The Peer ID of the sending peer.
        target_pid (str): The Peer ID of the receiving peer.
        data (dict): The data to forward.
    """

    room_id = PEER_TO_ROOM.get(sender_pid)
    if not room_id:
        logging.error(f"Room not found for peer {sender_pid}")
        return

    room = ROOMS.get(room_id)
    if not room:
        logging.error(f"Room {room_id} not found in memory.")
        return

    # Select target peer's data from room's peers dictionary.
    target_peer = room["peers"].get(target_pid)
    if not target_peer:
        logging.error(f"Target peer {target_pid} not found in room {room_id}.")
        return

    # Extract websocket from peer data object
    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        logging.info(f"Forwarding message from {sender_pid} to {target_pid}: {data}")
        await target_websocket.send(json.dumps(data))
        METRICS["total_messages"] += 1
    except Exception as e:
        logging.error(f"Failed to send message from {sender_pid} to {target_pid}. Error: {e}")
    

def matches_filters(peer_data: dict, filters: dict) -> bool:
    """Check if peer matches discovery filters.

    Args:
        peer_data: Peer data dict with role, metadata, etc.
        filters: Filter criteria dict with optional role, tags, properties.

    Returns:
        bool: True if peer matches all filters, False otherwise.
    """
    # Role filter
    if "role" in filters and peer_data.get("role") != filters["role"]:
        return False

    metadata = peer_data.get("metadata", {})

    # Tag filter (match any)
    if "tags" in filters:
        peer_tags = set(metadata.get("tags", []))
        filter_tags = set(filters["tags"])
        if not peer_tags.intersection(filter_tags):
            return False

    # Property filters with operators
    if "properties" in filters:
        peer_props = metadata.get("properties", {})
        for key, value in filters["properties"].items():
            peer_value = peer_props.get(key)

            if isinstance(value, dict):
                # Operator syntax: {"$gte": 8192}
                if "$gte" in value and (peer_value is None or peer_value < value["$gte"]):
                    return False
                if "$lte" in value and (peer_value is None or peer_value > value["$lte"]):
                    return False
                if "$eq" in value and peer_value != value["$eq"]:
                    return False
            elif peer_value != value:
                return False

    return True


async def handle_discover_peers(websocket, message):
    """Handle peer discovery request with filtering.

    Message format:
    {
        "type": "discover_peers",
        "from_peer_id": "Client-1489",
        "filters": {
            "role": "worker",
            "tags": ["gpu", "training"],
            "properties": {
                "gpu_memory_mb": {"$gte": 8192}
            }
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    filters = message.get("filters", {})

    # Get requester's room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Peer not registered in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "ROOM_NOT_FOUND",
            "message": f"Room {room_id} not found"
        }))
        return

    # Find matching peers (exclude self)
    matching_peers = []
    for peer_id, peer_data in room["peers"].items():
        if peer_id == from_peer_id:
            continue  # Don't return self

        if matches_filters(peer_data, filters):
            # Return peer info without websocket object
            matching_peers.append({
                "peer_id": peer_id,
                "role": peer_data.get("role", "peer"),
                "metadata": peer_data.get("metadata", {}),
                "connected_at": peer_data.get("connected_at", 0)
            })

    # Send response
    await websocket.send(json.dumps({
        "type": "peer_list",
        "to_peer_id": from_peer_id,
        "peers": matching_peers,
        "count": len(matching_peers)
    }))

    logging.info(f"[DISCOVER] {from_peer_id} found {len(matching_peers)} matching peers in room {room_id}")


async def handle_update_metadata(websocket, message):
    """Handle metadata update from peer.

    Allows peers to update their metadata in real-time (e.g., status changes).

    Message format:
    {
        "type": "update_metadata",
        "peer_id": "Worker-3108",
        "metadata": {
            "tags": ["sleap-rtc", "training-worker"],
            "properties": {
                "status": "busy",
                ...
            }
        }
    }
    """
    peer_id = message.get("peer_id")
    new_metadata = message.get("metadata")

    if not peer_id or not new_metadata:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: peer_id, metadata"
        }))
        return

    # Verify peer is in a room
    room_id = PEER_TO_ROOM.get(peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Peer not in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room or peer_id not in room["peers"]:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": f"Peer {peer_id} not found in room"
        }))
        return

    # Update metadata (merge with existing)
    peer_data = room["peers"][peer_id]

    # Merge tags (union of old and new)
    if "tags" in new_metadata:
        existing_tags = set(peer_data.get("metadata", {}).get("tags", []))
        new_tags = set(new_metadata.get("tags", []))
        merged_tags = list(existing_tags.union(new_tags))
        new_metadata["tags"] = merged_tags

    # Merge properties (new properties override old)
    if "properties" in new_metadata:
        existing_props = peer_data.get("metadata", {}).get("properties", {})
        new_props = new_metadata.get("properties", {})
        merged_props = {**existing_props, **new_props}
        new_metadata["properties"] = merged_props

    # Update stored metadata
    peer_data["metadata"] = new_metadata

    # Send confirmation
    await websocket.send(json.dumps({
        "type": "metadata_updated",
        "peer_id": peer_id,
        "metadata": new_metadata
    }))

    logging.info(f"[METADATA_UPDATE] {peer_id} updated metadata in room {room_id}")


async def handle_peer_message(websocket, message):
    """Handle generic peer-to-peer message routing.

    Message format:
    {
        "type": "peer_message",
        "from_peer_id": "Client-1489",
        "to_peer_id": "Worker-3108",
        "payload": {
            // Application-specific data (not interpreted by signaling server)
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    to_peer_id = message.get("to_peer_id")
    payload = message.get("payload")

    if not all([from_peer_id, to_peer_id, payload]):
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: from_peer_id, to_peer_id, payload"
        }))
        return

    # Verify sender is in a room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Sender not in any room"
        }))
        return

    # Verify target is in same room
    target_room_id = PEER_TO_ROOM.get(to_peer_id)
    if target_room_id != room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_IN_ROOM",
            "message": f"Target peer {to_peer_id} not in same room"
        }))
        return

    # Forward payload to target peer
    room = ROOMS.get(room_id)
    target_peer = room["peers"].get(to_peer_id)

    if not target_peer:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": f"Target peer {to_peer_id} not found"
        }))
        return

    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        await target_websocket.send(json.dumps({
            "type": "peer_message",
            "from_peer_id": from_peer_id,
            "to_peer_id": to_peer_id,
            "payload": payload  # Pass through without interpretation
        }))
        METRICS["total_messages"] += 1
        logging.info(f"[PEER_MESSAGE] Routed message from {from_peer_id} to {to_peer_id}")
    except Exception as e:
        logging.error(f"Failed to route peer message: {e}")
        await websocket.send(json.dumps({
            "type": "error",
            "code": "DELIVERY_FAILED",
            "message": f"Failed to deliver message to {to_peer_id}"
        }))


def get_room(room_id: str):
    """Fetches a room document from DynamoDB by room_id.

    Args:
        room_id (str): The ID of the room to fetch.
    Returns:
        dict: The room document data if found, otherwise None.
    """

    # Fetch the room document from DynamoDB.

    response = rooms_table.get_item(Key={"room_id": room_id})
    doc = response.get('Item')

    # Check if the document exists.
    if not doc:
        logging.error(f"Room {room_id} not found in DynamoDB.")
        return None

    # Return the document data as a dictionary.
    return doc


async def handle_client(websocket):
    """Handles incoming messages between peers to facilitate exchange of SDP & ICE candidates.

    Enhanced to support:
    - Peer registration with role and metadata
    - Peer discovery with filtering
    - Generic peer-to-peer message routing
    - WebRTC signaling (offer/answer/candidate)

    Args:
		websocket: A websocket connection object between peer1 (client) and peer2 (worker)
	Returns:
		None
	Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the client
    """

    peer_id = None  # Track for cleanup

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                logging.info(f"Received message type: {msg_type}")

                # Registration (enhanced with role/metadata)
                if msg_type == "register":
                    await handle_register(websocket, data)
                    peer_id = data.get('peer_id')

                # NEW: Peer discovery
                elif msg_type == "discover_peers":
                    await handle_discover_peers(websocket, data)

                # NEW: Metadata updates
                elif msg_type == "update_metadata":
                    await handle_update_metadata(websocket, data)

                # NEW: Generic peer message routing
                elif msg_type == "peer_message":
                    await handle_peer_message(websocket, data)

                # Existing: WebRTC signaling (offer/answer for backward compatibility)
                elif msg_type in ["offer", "answer"]:
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')

                    if not sender_pid or not target_pid:
                        logging.warning("Missing sender or target peer ID in signaling message.")
                        continue

                    await forward_message(sender_pid, target_pid, data)

                # ICE candidates (if you add support for them)
                elif msg_type == "candidate":
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')

                    if not sender_pid or not target_pid:
                        logging.warning("Missing sender or target peer ID in candidate message.")
                        continue

                    await forward_message(sender_pid, target_pid, data)

                else:
                    logging.warning(f"Unknown message type: {msg_type}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "UNKNOWN_MESSAGE_TYPE",
                        "message": f"Unknown message type: {msg_type}"
                    }))

            except json.JSONDecodeError:
                logging.error("Invalid JSON received")
                await websocket.send(json.dumps({
                    "type": "error",
                    "code": "INVALID_JSON",
                    "message": "Invalid JSON format"
                }))

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"Client {peer_id} disconnected cleanly.")

    except Exception as e:
        logging.error(f"Error handling client {peer_id}: {e}")

    finally:
        # Cleanup on disconnect
        if peer_id:
            room_id = PEER_TO_ROOM.get(peer_id)
            if room_id and room_id in ROOMS:
                # Remove peer from room
                if peer_id in ROOMS[room_id]["peers"]:
                    del ROOMS[room_id]["peers"][peer_id]
                    logging.info(f"Removed peer {peer_id} from room {room_id}")

                # If room is empty, clean it up
                if not ROOMS[room_id]["peers"]:
                    del ROOMS[room_id]
                    logging.info(f"Room {room_id} is empty and has been removed")

            # Remove peer-to-room mapping
            if peer_id in PEER_TO_ROOM:
                del PEER_TO_ROOM[peer_id]

            # Update metrics
            METRICS["active_connections"] = sum(len(room["peers"]) for room in ROOMS.values())

        logging.info(f"Connection cleanup complete for peer {peer_id}")


def run_fastapi_server():
    """Runs the FastAPI server for room creation."""

    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
    

async def main():
    """Main function to run server indefinitely.
    
    Creates a websocket server to handle incoming connections and passes them to handle_client.
    
    Args:
		None
	Returns:
		None
    """

    async with websockets.serve(handler=handle_client, host="0.0.0.0", port=8080): # use 0.0.0.0 to allow external connections from anywhere (as the signaling server)
        # run server indefinitely
        logging.info("Server started!")
        await asyncio.Future()

if __name__ == "__main__":
    # Start FastAPI server in a separate thread.
    threading.Thread(target=run_fastapi_server, daemon=True).start() 
    asyncio.run(main())