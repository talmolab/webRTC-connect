import asyncio
import websockets
import json
import logging 

from aiortc import RTCPeerConnection, RTCSessionDescription

# init commit
# test commit
# setup logging
logging.basicConfig(level=logging.INFO)

# key: peer_id, value: specific peer websocket 
connected_peers = {} 


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
                logging.info(f"Received message: {data}")

                if data.get('type') == "register":
                    # formatted as "register:peer_id"
                    peer_id = data.get('peer_id')
                    # add peer_id to connected_peers dictionary & update server terminal
                    connected_peers[peer_id] = websocket
                    logging.info(f"Registered peer: {peer_id}")
                                    
                elif data.get('type') == "query":
                    # send available peers to client terminal via websocket
                    response = {'type': 'available_peers', 'peers': list(connected_peers.keys())}
                    await websocket.send(json.dumps(response))

                elif data.get('type') == "offer":
                    # handle offer/exchange between peers
                    # formatted as "offer:peer_id" or "answer:peer_id"
                    target_peer_id = data.get('target')
                    target_websocket = connected_peers.get(target_peer_id)

                    # if target_peer_id exists, send message to target_peer_id
                    # update server terminal
                    if target_websocket:
                        logging.info(f"Forwarding message from {peer_id} to {target_peer_id}")
                        await target_websocket.send(json.dumps(data))
                    else:
                        logging.info(f"Peer not found: {target_peer_id}")

                elif data.get('type') == "answer":
                    # hardcoded for now
                    target_peer_id = 'client1'
                    target_websocket = connected_peers.get(target_peer_id)

                    # if target_peer_id exists, send message to target_peer_id
                    # update server terminal
                    if target_websocket:
                        logging.info(f"Forwarding message from {peer_id} to {target_peer_id}")
                        await target_websocket.send(json.dumps(data))
                    else:
                        logging.info(f"Peer not found: {target_peer_id}")

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
        
async def main():
    """Main function to run server indefinitely.
    
    Creates a websocket server to handle incoming connections and passes them to handle_client.
    
    Args:
		None
    
	Returns:
		None
    
	Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the client
    """

    async with websockets.serve(handle_client, "0.0.0.0", 8080):
        # run server indefinitely
        logging.info("Server started!")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())