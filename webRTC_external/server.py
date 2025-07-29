import asyncio
import threading
from time import time
import firebase_admin
import json
import logging
import os
import websockets
import secrets, time, uuid
import uvicorn

from aiortc import RTCPeerConnection, RTCSessionDescription
from fastapi import FastAPI, Request, HTTPException
from firebase_admin import credentials, firestore, auth

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Key: peer_id, Value: specific peer websocket 
connected_peers = {} 

# Maps room_id -> { "token": ..., "expires_at": ..., "created_by": ..., "peers": { peer_id: websocket } }
ROOMS = {}
PEER_TO_ROOM = {}

# FastAPI App (Room Creaton)
app = FastAPI()

# Initialize Firebase Admin SDK.
cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()


@app.post("/create-room")
async def create_room(req: Request):
    auth_header = req.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(401, "Missing Authorization header")
    
    id_token = auth_header.split(" ")[1] # Firebase ID token from anon. sign-in
    try:
        decoded = auth.verify_id_token(id_token)
    except:
        raise HTTPException(401, "Invalid token")
    
    uid = decoded["uid"]

    # Generate a unique room ID and token to be associated with this verified Firebase ID token.
    # Firebase ID token -> uid
    room_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)

    db.collection("rooms").document(room_id).set({
        "created_by": uid,
        "token": token,
        "expires_at": time.time() + 10 * 60
    })

    return { "room_id": room_id, "token": token }


async def handle_register(websocket, message):
    """Handles the registration of a peer in a room w/ its websocket."""

    peer_id = message.get('peer_id') # identify a peer uniquely in the room (Zoom username)
    room_id = message.get('room_id') # from backend API call for room identification (Zoom meeting ID)
    token = message.get('token') # from backend API call for room joining (Zoom meeting password)
    id_token = message.get('id_token') # from anon. Firebase sign-in (prevent peer spoofing, even anonymously)

    # Validate required fields.
    if not all([peer_id, room_id, token, id_token]):
        await websocket.send(json.dumps({"type": "error", "reason": "Missing required fields"}))
        return  

    # 1. Verify Firebase ID token (passed from peer Firebase anonymous sign-in).
    try:
        decoded = auth.verify_id_token(id_token)
        uid = decoded['uid']
    except Exception as e:
        logging.error(f"Token verification failed: {e}")
        await websocket.send(json.dumps({"error": "Invalid token"}))
        return

    # 2. Can now fetch prev. created Firestore room document.
    try:
        doc = db.collection("rooms").document(room_id).get()
        # Format: {
        #     "created_by": uid, decoded Firebase ID token,
        #     "token": token,
        #     "expires_at": time.time() + 10 * 60
        # }

        # If Client calls, should be using Worker's room_id. Vice versa.
    except Exception as e:
        logging.error(f"Failed to fetch room {room_id}: {e}")
        await websocket.send(json.dumps({"type": "error", "reason": "Firestore error"}))
        return

    if not doc.exists:
        await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
        return

    room_data = doc.to_dict()
    
    # 3. Compare token from request with the one stored in Firestore.
    # i.e. check "Zoom meeting password" is correct. (Both peer must have same token to join the room.)
    # If Client calls, should be using Worker's token. Vice versa.
    if token != room_data.get("token"):
        await websocket.send(json.dumps({"type": "error", "reason": "Invalid token"}))
        return
    
    # Check room expiration.
    if time() > room_data.get("expires_at"):
        await websocket.send(json.dumps({"type": "error", "reason": "Room expired"}))
        return
    
    # 4. Cannot store peer's websocket object directly in Firestore document, so keep it in memory.
    # Client should not be able to create a room since Worker would have already created it.
    # Looks the same as Firestore document, but in memory and with a 'peers' dict.
    if room_id not in ROOMS:
        ROOMS[room_id] = {
            "created_by": uid,
            "token": room_data["token"],
            "expires_at": room_data["expires_at"],
            "peers": {}
        }

    # room_id -> peers dict.: { peer_id: websocket }
    # Zoom username associated with their websocket
    # ROOMS[room_id]["peers"]: { Worker-3108: <Worker websocket object>, Client-1489: <Client websocket object> }
    # PEER_TO_ROOM: { Worker-3108: room-7462, Client-1489: room-7462 }
    ROOMS[room_id]["peers"][peer_id] = websocket
    PEER_TO_ROOM[peer_id] = room_id

    # Send registration confirmation to the peer.
    await websocket.send(json.dumps({
        "type": "registered_auth",
        "room_id": room_id,
        "token": token,
        "peer_id": peer_id
    }))

    logging.info(f"[REGISTERED] peer_id: {peer_id} in room: {room_id}")


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

    # Select target peer's websocket from room's peers dictionary.
    target_websocket = room["peers"].get(target_pid)

    try:
        logging.info(f"Forwarding message from {sender_pid} to {target_pid}: {data}")
        await target_websocket.send(json.dumps(data))
    except:
        logging.error(f"Failed to send message from {sender_pid} to {target_pid}. It may have disconnected.")


def verify_token(id_token: str):
    """Verifies the Firebase ID token and returns the decoded token.

    Args:
        id_token (str): The Firebase ID token to verify.
    Returns:
        str: The user ID if the token is valid, otherwise raises an exception.
    Raises:
        ValueError: If the token is invalid or expired.
    """

    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token['uid']
    except Exception as e:
        logging.error(f"Token verification failed: {e}")
        raise ValueError("Invalid or expired token")
    

def get_room(room_id: str):
    """Fetches a room document from Firestore by room_id.

    Args:
        room_id (str): The ID of the room to fetch.
    Returns:
        dict: The room document data if found, otherwise None.
    """

    # Fetch the room document from Firestore.
    doc = db.collection("rooms").document(room_id).get()

    # Check if the document exists.
    if not doc.exists:
        logging.error(f"Room {room_id} does not exist.")
        return None
    
    # Return the document data as a dictionary.
    return doc.to_dict()


async def handle_client(websocket):
    """Handles incoming messages between peers to facilitate exchange of SDP & ICE candidates.

    Registers both peers and links them together to forward SDP & ICE candidates.
    Takes SDP arguments from for registration, query (seek available workers), offer, and answer.

    Args:
		websocket: A websocket connection object between peer1 (client) and peer2 (worker)
	Returns:
		None
	Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the client
    """
    
    peer_id = None
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                logging.info(f"Received message: {data}")

                if msg_type == "register":
                    await handle_register(websocket, data)
                    # peer_id = data['peer_id']

                # elif msg_type == "query":
                #     # send available peers to client terminal via websocket
                #     response = {'type': 'available_peers', 'peers': list(connected_peers.keys())}
                #     await websocket.send(json.dumps(response))

                # Either peer can send an offer to the other peer (usually Client -> Worker).
                elif msg_type in ["offer", "answer"]:
                    # handle offer/exchange between peers
                    # formatted as "offer:peer_id" or "answer:peer_id"
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')

                    # Check for sender and target peer IDs.
                    if not sender_pid or not target_pid:
                        logging.info("Missing sender or target peer ID in offer message.")
                        continue

                    # Forward the offer to the target peer.
                    await forward_message(sender_pid, target_pid, data)

                    # if target_peer_id exists, send message to target_peer_id
                    # update server terminal
                    # if target_websocket:
                    #     logging.info(f"Forwarding message from {peer_id} to {target_peer_id}")
                    #     await target_websocket.send(json.dumps(data))
                    # else:
                    #     logging.info(f"Peer not found: {target_peer_id}")

                # elif msg_type == "answer":
                #     # obtain sender/target PIDs
                #     sender_pid = data.get('sender')
                #     target_pid = data.get('target')

                #     # Check for sender and target peer IDs.
                #     if not sender_pid or not target_pid:
                #         logging.info("Missing sender or target peer ID in offer message.")
                #         continue

                #     # Forward the offer to the target peer.
                #     await forward_message(sender_pid, target_pid, data)

                #     # target_peer_id = 'client1'
                #     # target_websocket = connected_peers.get(target_peer_id)

                #     # if target_peer_id exists, send message to target_peer_id
                #     # update server terminal
                #     # if target_websocket:
                #     #     logging.info(f"Forwarding message from {peer_id} to {target_peer_id}")
                #     #     await target_websocket.send(json.dumps(data))
                #     # else:
                #     #     logging.info(f"Peer not found: {target_peer_id}")

            except json.JSONDecodeError:
                logging.info("Invalid JSON received")

    except websockets.exceptions.ConnectionClosedOK:
        logging.info("Client disconnected cleanly.")
    
    except Exception as e:
        logging.info(f"Error handling client: {e}")
    
    finally:
        if peer_id:
            # disconnect the peer
            del connected_peers[peer_id]
            logging.info(f"Peer disconnected: {peer_id}")


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