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
ROOMS = {}
PEER_TO_ROOM = {}

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

# FastAPI App (Room Creaton)
app = FastAPI()


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
    
    return { "room_id": room_id, "token": token }


async def handle_register(websocket, message):
    """Handles the registration of a peer in a room w/ its websocket."""

    peer_id = message.get('peer_id') # identify a peer uniquely in the room (Zoom username)
    room_id = message.get('room_id') # from backend API call for room identification (Zoom meeting ID)
    token = message.get('token') # from backend API call for room joining (Zoom meeting password)
    id_token = message.get('id_token') # from anon. Cognito sign-in (prevent peer spoofing, even anonymously)

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

    # Select target peer's websocket from room's peers dictionary.
    target_websocket = room["peers"].get(target_pid)

    try:
        logging.info(f"Forwarding message from {sender_pid} to {target_pid}: {data}")
        await target_websocket.send(json.dumps(data))
    except:
        logging.error(f"Failed to send message from {sender_pid} to {target_pid}. It may have disconnected.")
    

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
        # Delete the room.
        logging.info("Exiting server...")

        # if peer_id:
        #     # disconnect the peer
        #     # del connected_peers[peer_id]
        #     logging.info(f"Peer disconnected: {peer_id}")


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