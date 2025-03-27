import asyncio
import subprocess
import sys
import websockets
import json
import logging
import os

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel

# setup logging
logging.basicConfig(level=logging.INFO)

# global variables
CHUNK_SIZE = 32 * 1024

# directory to save files received from client
SAVE_DIR = "/app/shared_data"
received_files = {}

async def clean_exit(pc, websocket):
    logging.info("Closing WebRTC connection...") 
    await pc.close()

    logging.info("Closing websocket connection...")
    await websocket.close()

    logging.info("Client shutdown complete. Exiting...")


async def send_worker_messages(channel, pc, websocket):
    """Handles typed messages from worker to be sent to client peer.
        
		  Takes input from worker and sends it to client peer via datachannel. Additionally, prompts for file upload to be sent to client.
	
        Args:
			None
        
		Returns:
			None
        
        """

    message = input("Enter message to send (type 'file' to prompt file or type 'quit' to exit): ")
    data = None
    
    if message.lower() == "quit":
        logging.info("Quitting...")
        await pc.close()
        return

    if channel.readyState != "open":
        logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
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
            logging.info(f"Sending {file_path} to client...")
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            # Send metadata first
            channel.send(f"{file_name}:{file_size}")
            
            # Send file in chunks (32 KB)
            with open(file_path, "rb") as file:
                logging.info(f"File opened: {file_path}")
                while chunk := file.read(CHUNK_SIZE):
                    channel.send(chunk)
            
            channel.send("END_OF_FILE")
            logging.info(f"File sent to client.")
                    
            # Flag data to True to prevent reg msg from being sent
            data = True

    if not data:
        channel.send(message)
        logging.info(f"Message sent to client.")


async def handle_connection(pc, websocket):
    try:
        async for message in websocket:
            data = json.loads(message)
            
            # 1. receieve offer SDP from client (forwarded by signaling server)
            if data.get('type') == "offer":
                # 1a. set worker peer's remote description to the client's offer based on sdp data
                logging.info('Received offer SDP')

                await pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type='offer')) 
                
                # 1b. generate worker's answer SDP and set it as the local description
                await pc.setLocalDescription(await pc.createAnswer())
                
                # 1c. send worker's answer SDP to client so they can set it as their remote description
                await websocket.send(json.dumps({'type': pc.localDescription.type, 'target': data.get('target'), 'sdp': pc.localDescription.sdp}))
            
            # 2. to handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
            elif data.get('type') == 'candidate':
                print("Received ICE candidate")
                candidate = data.get('candidate')
                await pc.addIceCandidate(candidate)

            elif data.get('type') == 'quit': # NOT initiator, received quit request from worker
                print("Received quit request from Client. Closing connection...")
                await clean_exit(pc, websocket)
                return

            # 3. error handling
            else:
                logging.ERROR(f"Unhandled message: {data}")
                
    
    except json.JSONDecodeError:
        logging.ERROR("Invalid JSON received")

    except Exception as e:
        logging.ERROR(f"Error handling message: {e}")

        
async def run_worker(pc, peer_id: str, DNS: str, port_number):
    # websockets are only necessary here for setting up exchange of SDP & ICE candidates to each other
    
    # 2. listen for incoming data channel messages on channel established by the client
    @pc.on("datachannel")
    def on_datachannel(channel):
        # listen for incoming messages on the channel
        logging.info("channel(%s) %s" % (channel.label, "created by remote party & received."))
        file_data = bytearray()
        file_name = "default_receieved_file.bin"
        

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logging.info(f"ICE connection state is now {pc.iceConnectionState}")
            if pc.iceConnectionState == "failed":
                logging.ERROR('ICE connection failed')
                await clean_exit(pc, websocket)
                return
            elif pc.iceConnectionState in ["failed", "disconnected"]:
                logging.info("ICE connection failed/disconnected. Closing connection.")
                await clean_exit(pc, websocket)
                return
            elif pc.iceConnectionState == "closed":
                logging.info("ICE connection closed.")
                await clean_exit(pc, websocket)
                return
            
        @channel.on("open")
        def on_channel_open():
            logging.info(f'{channel.label} channel is open')
        
        @channel.on("message")
        async def on_message(message):
            # receive client message
            logging.info(f"Worker received: {message}")

            # global received_files dictionary
            global received_files
            
            if isinstance(message, str):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return

                if message == "END_OF_FILE":
                    # File transfer complete, save to disk
                    file_name, file_data = list(received_files.items())[0]
                    file_path = os.path.join(SAVE_DIR, file_name)

                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}")

                    received_files.clear()  # Reset for next file
                    await send_worker_messages(channel, pc, websocket)
                else:
                    # Metadata received (file name & size)
                    file_name, file_size = message.split(":")
                    received_files[file_name] = bytearray()
                    logging.info(f"File name received: {file_name}, of size {file_size}")

                # file_name = message
                # logging.info(f"File name received: {file_name}")

            elif isinstance(message, bytes):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return
                # file_data.extend(message)

                # with open(f"{SAVE_DIR}/{file_name}", "wb") as f:
                #     f.write(file_data)
                #     logging.info(f"File {file_name} saved to {SAVE_DIR}")
                file_name = list(received_files.keys())[0]
                received_files.get(file_name).extend(message)
			
                
            # send message to client
            # await send_worker_messages(channel, pc, websocket)


    # 1. worker registers with the signaling server (temp: localhost:8080) via websocket connection
    # this is how the worker will know the client peer exists
    async with websockets.connect(f"{DNS}:{port_number}") as websocket:
        # 1a. register the worker with the server
        await websocket.send(json.dumps({'type': 'register', 'peer_id': peer_id}))
        logging.info(f"{peer_id} sent to signaling server for registration!")

        # 1b. handle incoming messages from server (e.g. answers)
        await handle_connection(pc, websocket)
        logging.info(f"{peer_id} connected with client!" )


    # ICE, or Interactive Connectivity Establishment, is a protocol used in WebRTC to establish a connection
    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(f"ICE connection state is now {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            logging.ERROR('ICE connection failed')
            await clean_exit(pc, websocket)
            return
        elif pc.iceConnectionState in ["failed", "disconnected"]:
            logging.info("ICE connection failed/disconnected. Closing connection.")
            await clean_exit(pc, websocket)
            return
        elif pc.iceConnectionState == "closed":
            logging.info("ICE connection closed.")
            await clean_exit(pc, websocket)
            return
    
        
if __name__ == "__main__":
    pc = RTCPeerConnection()
    DNS = sys.argv[1] if len(sys.argv) > 1 else "ws://ec2-34-230-32-163.compute-1.amazonaws.com"
    port_number = sys.argv[2] if len(sys.argv) > 1 else 8080
    try:
        asyncio.run(run_worker(pc, "worker1", DNS, port_number))
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt: Exiting...")
    finally:
        logging.info("exited")
        

    
