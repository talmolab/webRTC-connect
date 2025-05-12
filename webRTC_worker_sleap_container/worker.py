import asyncio
import subprocess
import stat
import sys
import websockets
import json
import logging
import shutil
import os

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel

# setup logging
logging.basicConfig(level=logging.INFO)

# global variables
CHUNK_SIZE = 32 * 1024

# directory to save files received from client
SAVE_DIR = "/app/shared_data"
ZIP_DIR = "/app/shared_data/models"
received_files = {}

async def zip_results(file_name, dir_path): # trained_model.zip, /app/shared_data/models
    """Zips the contents of the shared_data directory and saves it to a zip file."""
    logging.info("Zipping results...")
    if os.path.exists(dir_path):
        try:
            shutil.make_archive(file_name.split(".")[0], 'zip', dir_path)
            logging.info(f"Results zipped to {file_name}")
        except Exception as e:
            logging.error(f"Error zipping results: {e}")
            return
    else:
        logging.info("Results already zipped or directory does not exist!")
        return


async def unzip_results(file_path):
    """Unzips the contents of the given file path."""
    logging.info("Unzipping results...")
    if os.path.exists(file_path):
        try:
            shutil.unpack_archive(file_path, SAVE_DIR)
            logging.info(f"Results unzipped from {file_path}")
        except Exception as e:
            logging.error(f"Error unzipping results: {e}")
            return
    else:
        logging.info("Results already unzipped or directory does not exist!")
        return
    

async def clean_exit(pc, websocket):
    """ Handles cleanup and shutdown of the worker.
        Args:
            pc: RTCPeerConnection object
            websocket: WebSocket connection object
        Returns:
            None    
    """

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
            file_save_dir = "models" # SHOULD ORIGINATE FROM training_config.json
            
            # Send metadata first
            channel.send(f"FILE_META::{file_name}:{file_size}:{file_save_dir}")
            
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
    """ Handles incoming messages from the signaling server and processes them accordingly.
        Args:
            pc: RTCPeerConnection object
            websocket: WebSocket connection object
        Returns:
            None    
        """
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
    """
      Main function to run the worker.
        Args:
          pc: RTCPeerConnection object
          peer_id: ID of the worker peer
          DNS: DNS address of the signaling server
          port_number: Port number of the signaling server
        Returns:
          None
    """
    # websockets are only necessary here for setting up exchange of SDP & ICE candidates to each other
    
    # 2. listen for incoming data channel messages on channel established by the client
    @pc.on("datachannel")
    def on_datachannel(channel):
        """ Handles incoming data channel messages from the client.
            Args:
                channel: DataChannel object
            Returns:
                None
        """

        # listen for incoming messages on the channel
        logging.info("channel(%s) %s" % (channel.label, "created by remote party & received."))
        # file_data = bytearray()
        # file_name = "default_receieved_file.bin"

    
        async def send_worker_file(file_path):
            """Handles direct, one-way file transfer from client to be sent to client peer.
            
            Takes file from worker and sends it to client peer via datachannel. Doesn't require typed responses.
        
            Args:
                None
            
            Returns:
                None
            
            """
            
            if channel.readyState != "open":
                logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
                return 

            logging.info(f"Given file path {file_path}")
            if not file_path:
                logging.info("No file path entered.")
                return
            if not os.path.exists(file_path):
                logging.info("File does not exist.")
                return
            else: 
                logging.info(f"Sending {file_path} to client...")

                # Obtain metadata
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                file_save_dir = "models" # SHOULD ORIGINATE FROM training_config.json
                
                # Send metadata first
                channel.send(f"FILE_META::{file_name}:{file_size}:{file_save_dir}")

                # Send file in chunks (32 KB)
                with open(file_path, "rb") as file:
                    logging.info(f"File opened: {file_path}")
                    while chunk := file.read(CHUNK_SIZE):
                        while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB 
                            await asyncio.sleep(0.1)

                        channel.send(chunk)

                channel.send("END_OF_FILE")
                logging.info(f"File sent to client.")
                
            return
        

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            """
              Logs the ICE connection state and handles connection state changes.
              Args:
                None
              Returns:
                None
            """

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
            """
              Logs the channel open event.
                Args:
                  None
                Returns:
                  None
            """
            logging.info(f'{channel.label} channel is open')
        
        @channel.on("message")
        async def on_message(message):
            """
              Handles incoming messages from the client.
                Args:
                  message: The message received from the client (can be string or bytes)
                Returns:
                  None
            """

            # receive client message
            logging.info(f"Worker received: {message}")
            logging.info(f"Received message of type: {type(message)}")

            # global received_files dictionary
            global received_files
            
            if isinstance(message, str):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return

                if message == "END_OF_FILE":
                    logging.info("End of file transfer received.")
                    # File transfer complete, save to disk
                    file_name, file_data = list(received_files.items())[0]
                    file_path = os.path.join(SAVE_DIR, file_name)

                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}")

                    # Unzip results if needed
                    if file_path.endswith(".zip"):
                        await unzip_results(file_path)
                        logging.info(f"Unzipped results from {file_path}")

                    # Reset for next file and train model
                    received_files.clear()

                    train_script_path = os.path.join(SAVE_DIR, "train-script.sh")

                    if os.path.exists(train_script_path):
                        try:
                            logging.info(f"Running training script: {train_script_path}")

                            # Make the script executable
                            os.chmod(train_script_path, os.stat(train_script_path).st_mode | stat.S_IEXEC)

                            # Run the training script in the save directory
                            process = await asyncio.create_subprocess_exec(
                                "bash", "train-script.sh",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.STDOUT,
                                cwd=SAVE_DIR
                            )

                            assert process.stdout is not None
                            async for line in process.stdout:
                                decoded_line = line.decode().rstrip()
                                logging.info(decoded_line)

                                if channel.readyState == "open":
                                    try:
                                        channel.send(f"TRAIN_LOG:{decoded_line}")
                                    except Exception as e:
                                        logging.error(f"Failed to send log line: {e}")
                            
                            await process.wait()


                            # result = await subprocess.run(
                            #     ["bash", "train-script.sh"],  # Use bash to run it
                            #     check=True,
                            #     capture_output=True,
                            #     text=True,
                            #     cwd=SAVE_DIR  # Set the working directory to SAVE_DIR
                            # )
                            logging.info("Training completed successfully.")

                            logging.info("Zipping results...")
                            zipped_file_name = f"trained_{file_name}" # i.e. trained_tmph39.zip
                            await zip_results(zipped_file_name, ZIP_DIR)

                            logging.info(f"Sending zipped file to client: {zipped_file_name}")
                            await send_worker_file(zipped_file_name)

                        except subprocess.CalledProcessError as e:
                            logging.error(f"Training failed with error:\n{e.stderr}")
                            await clean_exit(pc, websocket)
                    else:
                        logging.info(f"No training script found in {SAVE_DIR}. Skipping training.")

                    await send_worker_messages(channel, pc, websocket)
                elif "FILE_META::" in message:
                    logging.info(f"File metadata received: {message}")
                    # Metadata received (file name & size)
                    _, meta = message.split("FILE_META::", 1)
                    file_name, file_size = meta.split(":")

                    # file_name, file_size = message.split(":")
                    received_files[file_name] = bytearray()
                    logging.info(f"File name received: {file_name}, of size {file_size}")
                else:
                    logging.info(f"Client sent: {message}")


            elif isinstance(message, bytes):
                logging.info (f"{message} is of type {type(message)} and in elif statement")
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return
                
                file_name = list(received_files.keys())[0]
                received_files.get(file_name).extend(message)


    # 1. worker registers with the signaling server (temp: localhost:8080) via websocket connection
    # This is how the worker will know the client peer exists
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
        """ Handles ICE connection state changes.
            Args:
              None
            Returns:
              None
        """
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
    DNS = sys.argv[1] if len(sys.argv) > 1 else "ws://ec2-3-80-210-101.compute-1.amazonaws.com"
    port_number = sys.argv[2] if len(sys.argv) > 1 else 8080
    try:
        asyncio.run(run_worker(pc, "worker1", DNS, port_number))
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt: Exiting...")
    finally:
        logging.info("exited")
        

    
