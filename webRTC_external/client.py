import asyncio
import sys
import websockets
import json
import logging
import os

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from websockets import WebSocketClientProtocol

# setup logging
logging.basicConfig(level=logging.INFO)

async def clean_exit(pc, websocket):
    logging.info("Closing WebRTC connection...")
    await pc.close()

    logging.info("Closing websocket connection...")
    await websocket.close()

    logging.info("Client shutdown complete. Exiting...")


async def handle_connection(pc: RTCPeerConnection, websocket):
    """Handles receiving SDP answer from Worker and ICE candidates from Worker.

    Args:
        pc: RTCPeerConnection object
        websocket: websocket connection object 
    
    Returns:
		None
        
    Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the message
    """

    try:
        async for message in websocket:
            data = json.loads(message)

            # 1. receive answer SDP from worker and set it as this peer's remote description
            if data.get('type') == 'answer':
                print(f"Received answer from worker: {data}")

                await pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type=data.get('type')))

            # 2. to handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
            elif data.get('type') == 'candidate':
                print("Received ICE candidate")
                candidate = data.get('candidate')
                await pc.addIceCandidate(candidate)

            elif data.get('type') == 'quit': # NOT initiator, received quit request from worker
                print("Worker has quit. Closing connection...")
                await clean_exit(pc, websocket)
                break

            # 3. error handling
            else:
                logging.DEBUG(f"Unhandled message: {data}")
                logging.DEBUG("exiting...")
                break
    
    except json.JSONDecodeError:
        logging.DEBUG("Invalid JSON received")

    except Exception as e:
        logging.DEBUG(f"Error handling message: {e}")


async def run_client(pc, peer_id: str, DNS: str, port_number: str):
    """Sends initial SDP offer to worker peer and establishes both connection & datachannel to be used by both parties.
	
		Initializes websocket to select worker peer and sends datachannel object to worker.
	
    Args:
		pc: RTCPeerConnection object
		peer_id: unique str identifier for client
        
    Returns:
		None
        
    Raises:
		Exception: An error occurred while running the client
    """

    channel = pc.createDataChannel("my-data-channel")
    logging.info("channel(%s) %s" % (channel.label, "created by local party."))

    async def send_client_messages():
        """Handles typed messages from client to be sent to worker peer.
        
		Takes input from client and sends it to worker peer via datachannel.
	
        Args:
			None
        
		Returns:
			None
        
        """
        message = input("Enter message to send (type 'file' to prompt file or type 'quit' to exit): ")
        data = None

        if message.lower() == "quit": # client is initiator, send quit request to worker
            logging.info("Quitting...")
            await pc.close()
            return 
        
        if message.lower() == "file":
            logging.info("Prompting file...")
            file_path = input("Enter file path: (or type 'quit' to exit): ")
            if not file_path:
                logging.info("No file path entered.")
                return
            if file_path.lower() == "quit":
                logging.info("Quitting...")
                await pc.close()
                return
            if not os.path.exists(file_path):
                logging.info("File does not exist.")
                return
            else: 
                with open(file_path, "rb") as file:
                    logging.info(f"File opened: {file_path}")
                    data = file.read()

        if channel.readyState != "open":
            logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
            return 

        if not data: # no file
          channel.send(message)
          logging.info(f"Message sent to worker.")
        
        else: # file present
          logging.info(f"Sending {file_path} to worker...")
          channel.send(os.path.basename)
          channel.send(data)
          channel.send("END_OF_FILE")
          logging.info(f"File sent to worker.")


    @channel.on("open")
    async def on_channel_open():
        """Event handler function for when the datachannel is open.
        Args:
			None
            
        Returns:
			None
        """

        logging.info(f"{channel.label} is open")
        await send_client_messages()
    

    @channel.on("message")
    async def on_message(message):
        logging.info(f"Client received: {message}")
        await send_client_messages()


    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(f"ICE connection state is now {pc.iceConnectionState}")
        if pc.iceConnectionState in ["connected", "completed"]:
            logging.info("ICE connection established.")
            # connected_event.set()
        elif pc.iceConnectionState in ["failed", "disconnected"]:
            logging.info("ICE connection failed/disconnected. Closing connection.")
            await clean_exit(pc, websocket)
            return
        elif pc.iceConnectionState == "closed":
            logging.info("ICE connection closed.")
            await clean_exit(pc, websocket)
            return


    # 1. client registers with the signaling server (temp: localhost:8080) via websocket connection
    # this is how the client will know the worker peer exists
    async with websockets.connect(f"{DNS}:{port_number}") as websocket:
        # 1a. register the client with the signaling server
        await websocket.send(json.dumps({'type': 'register', 'peer_id': peer_id}))
        logging.info(f"{peer_id} sent to signaling server for registration!")

        # 1b. query for available workers
        await websocket.send(json.dumps({'type': 'query'}))
        response = await websocket.recv()
        available_workers = json.loads(response)["peers"]
        logging.info(f"Available workers: {available_workers}")

        # 1c. select a worker to connect to (will implement firebase auth later)
        target_worker = available_workers[0] if available_workers else None
        logging.info(f"Selected worker: {target_worker}")

        if not target_worker:
            logging.info("No workers available")
            return
        
        # 2. create and send SDP offer to worker peer
        await pc.setLocalDescription(await pc.createOffer())
        await websocket.send(json.dumps({'type': pc.localDescription.type, 'target': target_worker, 'sdp': pc.localDescription.sdp}))
        logging.info('Offer sent to worker')

        # 3. handle incoming messages from server (e.g. answer from worker)
        await handle_connection(pc, websocket)

    await pc.close()
    await websocket.close()
    

if __name__ == "__main__":
    pc = RTCPeerConnection()
    DNS = sys.argv[1] if len(sys.argv) > 1 else "ws://ec2-34-230-32-163.compute-1.amazonaws.com"
    port_number = sys.argv[2] if len(sys.argv) > 1 else 8080

    try: 
        asyncio.run(run_client(pc, "client1", DNS, port_number))
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt: Exiting...")
    finally:
        logging.info("exited")

    