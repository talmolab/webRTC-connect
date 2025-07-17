import asyncio
import subprocess
import stat
import sys
import websockets
import json
import logging
import shutil
import os
import re
import zmq

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
# from run_training import run_all_training_jobs
from pathlib import Path
from websockets.client import ClientConnection

class RTCWorkerClient:
    def __init__(self, remote_save_dir="/app/shared_data", chunk_size=32 * 1024):
        self.save_dir = remote_save_dir
        self.chunk_size = chunk_size
        self.received_files = {}
        self.output_dir = ""
        self.ctrl_socket = None
        self.pc = None  # RTCPeerConnection will be set later
        self.websocket = None  # WebSocket connection will be set later

    def parse_training_script(training_script_path: str):
        jobs = []
        pattern = re.compile(r"^\s*sleap-train\s+([^\s]+)\s+([^\s]+)")

        with open(training_script_path, "r") as f:
            for line in f:
                match = pattern.match(line)
                if match:
                    config, labels = match.groups()
                    jobs.append((config.strip(), labels.strip()))
        return jobs


    async def run_all_training_jobs(self, channel: RTCDataChannel, train_script_path: str, save_dir: str):
        training_jobs = self.parse_training_script(train_script_path)

        for config_name, labels_name in training_jobs:
            job_name = Path(config_name).stem

            # Send RTC msg over channel to indicate job start.
            logging.info(f"Starting training job: {job_name} with config: {config_name} and labels: {labels_name}")
            channel.send(f"TRAIN_JOB_START::{job_name}")

            cmd = [
                "sleap-train",
                config_name,
                labels_name,
                "--zmq",
                "--controller_port",
                "9000",
                "--publish_port",
                "9001"
            ]
            logging.info(f"[RUNNING] {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=save_dir
            )

            assert process.stdout is not None

            async def stream_logs():
                async for line in process.stdout:
                    decoded_line = line.decode().rstrip()
                    logging.info(decoded_line)

                    if channel.readyState == "open":
                        try:
                            channel.send(f"TRAIN_LOG:{decoded_line}")
                        except Exception as e:
                            logging.error(f"Failed to send log line: {e}")

            await stream_logs()
            await process.wait()

            if process.returncode == 0:
                logging.info(f"[DONE] Job {job_name} completed successfully.")
                if channel.readyState == "open":
                    channel.send(f"TRAIN_END::{job_name}")
            else:
                logging.warning(f"[FAILED] Job {job_name} exited with code {process.returncode}.")
                if channel.readyState == "open":
                    channel.send(f"TRAIN_ERROR::{job_name}::{process.returncode}")

            # try:
            #     subprocess.run([
            #         "sleap-train",
            #         config_name,
            #         labels_name,
            #         "--zmq",
            #         "--controller_port",
            #         "9000",
            #         "--publish_port",
            #         "9001"
            #     ], check=True)
            # except subprocess.CalledProcessError as e:
            #     channel.send(f"TRAIN_JOB_ERROR::{job_name}::{e.stderr}")
            #     logging.error(f"Training job {job_name} failed with error: {e.stderr}")
            #     continue

            channel.send(f"TRAIN_JOB_END::{job_name}")

        channel.send("TRAINING_JOBS_DONE")


    def start_zmq_control(self, zmq_address: str = "tcp://127.0.0.1:9000"):
        """Starts a ZMQ control PUB socket to send ZMQ commands to the Trainer.
    
        Args:
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """
        # Initialize socket and event loop.
        logging.info("Starting ZMQ control socket...")
        context = zmq.Context()
        socket = context.socket(zmq.PUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.bind(zmq_address)

        # set PUB socket for use in other functions
        self.ctrl_socket = socket
        logging.info("ZMQ control socket initialized.")

    async def start_progress_listener(channel: RTCDataChannel, zmq_address: str = "tcp://127.0.0.1:9001"):
        """Starts a listener for ZMQ messages and sends progress updates to the client over the data channel.
    
        Args:
            channel: DataChannel object to send progress updates.
            zmq_address: Address of the ZMQ socket to connect to.
        Returns:
            None
        """

        # Initialize socket and event loop.
        logging.info("Starting ZMQ progress listener...")
        context = zmq.Context()
        socket = context.socket(zmq.SUB)

        logging.info(f"Connecting to ZMQ address: {zmq_address}")
        socket.bind(zmq_address) 
        socket.setsockopt_string(zmq.SUBSCRIBE, "")

        loop = asyncio.get_event_loop()

        def recv_msg():
            """Receives a message from the ZMQ socket in a non-blocking way.
            
            Returns:
                The received message as a JSON object, or None if no message is available.
            """
            
            try:
                # logging.info("Receiving message from ZMQ...")
                return socket.recv_string(flags=zmq.NOBLOCK)  # or jsonpickle.decode(msg_str) if needed
            except zmq.Again:
                return None

        while True:
            # Send progress as JSON string with prefix.
            msg = await loop.run_in_executor(None, recv_msg)

            if msg:
                try:
                    logging.info(f"Sending progress report to client: {msg}")
                    channel.send(f"PROGRESS_REPORT::{msg}")
                    # logging.info("Progress report sent to client.")
                except Exception as e:
                    logging.error(f"Failed to send ZMQ progress: {e}")
                    
            # Polling interval.
            await asyncio.sleep(0.05)

    async def zip_results(file_name: str, dir_path: str = None):
        """Zips the contents of the shared_data directory and saves it to a zip file.

        Args:
            file_name: Name of the zip file to be created.
            dir_path: Path to the directory to be zipped.
        Returns:
            None
        """

        if dir_path is None:
            dir_path = self.save_dir

        logging.info("Zipping results...")
        if Path(dir_path):
            try:
                shutil.make_archive(file_name.split(".")[0], 'zip', dir_path)
                logging.info(f"Results zipped to {file_name}")
            except Exception as e:
                logging.error(f"Error zipping results: {e}")
                return
        else:
            logging.info(f"{dir_path} does not exist!")
            return

    async def unzip_results(file_path: str, dir_path: str = None):
        """Unzips the contents of the given file path.

        Args:
            file_path: Path to the zip file to be unzipped.
        Returns:
            None
        """

        if dir_path is None:
            dir_path = self.save_dir

        logging.info("Unzipping results...")
        if Path(file_path):
            try:
                shutil.unpack_archive(file_path, dir_path)
                logging.info(f"Results unzipped from {file_path}")
            except Exception as e:
                logging.error(f"Error unzipping results: {e}")
                return
        else:
            logging.info(f"{file_path} does not exist!")
            return
        
    async def clean_exit(pc: RTCPeerConnection, websocket: ClientConnection):
        """Handles cleanup and shutdown of the worker.

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

    async def send_worker_messages(pc: RTCPeerConnection, channel: RTCDataChannel):
        """Handles typed messages from worker to be sent to client peer.

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
            if not Path(file_path):
                logging.info("File does not exist.")
                return
            else:
                logging.info(f"Sending {file_path} to client...")
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                file_save_dir = output_dir 
                
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

    async def handle_connection(self, pc: RTCPeerConnection, websocket: ClientConnection):
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

                # Receive offer SDP from client (forwarded by signaling server).
                if data.get('type') == "offer":
                    logging.info('Received offer SDP')

                    # Set worker peer's remote description to the client's offer based on sdp data
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type='offer')) 
                    
                    # Generate worker's answer SDP and set it as the local description
                    await pc.setLocalDescription(await pc.createAnswer())
                    
                    # Send worker's answer SDP to client so they can set it as their remote description
                    await websocket.send(json.dumps({'type': pc.localDescription.type, 'target': data.get('target'), 'sdp': pc.localDescription.sdp}))

                    # Reset received_files dictionary
                    self.received_files.clear()
                
                # Handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
                elif data.get('type') == 'candidate':
                    print("Received ICE candidate")
                    candidate = data.get('candidate')
                    await pc.addIceCandidate(candidate)

                elif data.get('type') == 'quit': # NOT initiator, received quit request from worker
                    print("Received quit request from Client. Closing connection...")
                    await self.clean_exit(pc, websocket)
                    return

                # Error handling
                else:
                    logging.ERROR(f"Unhandled message: {data}")
                    
        
        except json.JSONDecodeError:
            logging.ERROR("Invalid JSON received")

        except Exception as e:
            logging.ERROR(f"Error handling message: {e}")

    async def keep_ice_alive(channel: RTCDataChannel):
        """Sends periodic keep-alive messages to the client to maintain the connection.
        
        Args:
            channel: DataChannel object
        Returns:
            None
        """

        while True:
            await asyncio.sleep(15)
            if channel.readyState == "open":
                channel.send(b"KEEP_ALIVE")

    # Websockets are only necessary here for setting up exchange of SDP & ICE candidates to each other.
    # Listen for incoming data channel messages on channel established by the client.         
    def on_datachannel(self, channel: RTCDataChannel):
        """Handles incoming data channel messages from the client.

        Args:
            channel: DataChannel object
        Returns: 
            None
        """

        # Listen for incoming messages on the channel.
        logging.info("channel(%s) %s" % (channel.label, "created by remote party & received."))
    
        async def send_worker_file(file_path: str):
            """Handles direct, one-way file transfer from client to be sent to client peer.
        
            Args:
                file_path: Path to the file to be sent.
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
            if not Path(file_path):
                logging.info("File does not exist.")
                return
            else: 
                logging.info(f"Sending {file_path} to client...")

                # Obtain metadata.
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                file_save_dir = output_dir
                
                # Send metadata first.
                channel.send(f"FILE_META::{file_name}:{file_size}:{file_save_dir}")

                # Send file in chunks (32 KB).
                with open(file_path, "rb") as file:
                    logging.info(f"File opened: {file_path}")
                    while chunk := file.read(self.chunk_size):
                        while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB 
                            await asyncio.sleep(0.1)

                        channel.send(chunk)

                channel.send("END_OF_FILE")
                logging.info(f"File sent to client.")
                
            return
            
        @channel.on("open")
        def on_channel_open():
            """Logs the channel open event.

            Args:
                None
            Returns:
                None
            """

            asyncio.create_task(self.keep_ice_alive(channel))
            logging.info(f'{channel.label} channel is open')
        
        @channel.on("message")
        async def on_message(message):
            """Handles incoming messages from the client.

            Args:
                message: The message received from the client (can be string or bytes)
            Returns:
                None
            """

            # Log Client's message.
            logging.info(f"Worker received: {message}")
            
            if isinstance(message, str):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return

                if message == "END_OF_FILE":
                    logging.info("End of file transfer received.")

                    # File transfer complete, save to disk.
                    file_name, file_data = list(self.received_files.items())[0]
                    file_path = os.path.join(self.output_dir, file_name)

                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}")

                    # Unzip results if needed.
                    if file_path.endswith(".zip"):
                        await self.unzip_results(file_path)
                        logging.info(f"Unzipped results from {file_path}")

                    # Reset dictionary for next file and train model.
                    self.received_files.clear()

                    train_script_path = os.path.join(self.save_dir, "train-script.sh")

                    if Path(train_script_path):
                        try:
                            # Start ZMQ progress listener.
                            progress_listener_task = asyncio.create_task(self.start_progress_listener(channel))
                            logging.info(f'{channel.label} progress listener started')

                            # Start ZMQ control socket.
                            self.start_zmq_control()
                            logging.info(f'{channel.label} ZMQ control socket started')
                            
                            # Give SUB socket time to connect.
                            await asyncio.sleep(1)

                            logging.info(f"Running training script: {train_script_path}")

                            # Make the script executable
                            os.chmod(train_script_path, os.stat(train_script_path).st_mode | stat.S_IEXEC)

                            # Run the training script in the save directory
                            await self.run_all_training_jobs(channel, train_script_path=train_script_path, save_dir=self.save_dir)

                            # Finish training.
                            logging.info("Training completed successfully.")
                            progress_listener_task.cancel()

                            # Zip the results.
                            logging.info("Zipping results...")
                            zipped_file_name = f"trained_{file_name}"
                            await self.zip_results(zipped_file_name, f"{self.save_dir}/{output_dir}")

                            # Send the zipped file to the client.
                            logging.info(f"Sending zipped file to client: {zipped_file_name}")
                            await send_worker_file(zipped_file_name)

                        except subprocess.CalledProcessError as e:
                            logging.error(f"Training failed with error:\n{e.stderr}")
                            await self.clean_exit(self.pc, self.websocket)
                    else:
                        logging.info(f"No training script found in {self.save_dir}. Skipping training.")

                elif "OUTPUT_DIR::" in message:
                    logging.info(f"Output directory received: {message}")
                    _, output_dir = message.split("OUTPUT_DIR::", 1)

                elif "FILE_META::" in message:
                    logging.info(f"File metadata received: {message}")
                    _, meta = message.split("FILE_META::", 1)
                    file_name, file_size = meta.split(":")

                    self.received_files[file_name] = bytearray()
                    logging.info(f"File name received: {file_name}, of size {file_size}")
                elif "ZMQ_CTRL::" in message:
                    logging.info(f"ZMQ control message received: {message}")
                    _, zmq_msg = message.split("ZMQ_CTRL::", 1)
                    
                    # ProgressListenerZMQ listens on zmq_address, send updates there.
                    # Should be either stop or cancel training cmd.
                    if self.ctrl_socket != None:
                        self.ctrl_socket.send_string(zmq_msg)
                    else:
                        logging.error(f"ZMQ control socket not initialized {self.ctrl_socket}. Cannot send control message.")

                    # Update the client with the control message.
                    channel.send(f"ZMQ_CTRL::{zmq_msg}")
                else:
                    logging.info(f"Client sent: {message}")
                    await self.send_worker_messages(channel, self.pc, self.websocket)

            elif isinstance(message, bytes):
                if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return
                
                file_name = list(self.received_files.keys())[0]
                self.received_files.get(file_name).extend(message)

    async def on_iceconnectionstatechange(self):
        """Handles ICE connection state changes.

        Args:
            None
        Returns:
            None
        """
        
        # Log the ICE connection state.
        logging.info(f"ICE connection state is now {self.pc.iceConnectionState}")

        # Check the ICE connection state and handle accordingly.
        if self.pc.iceConnectionState == "failed":
            logging.ERROR('ICE connection failed')
            await self.clean_exit(self.pc, self.websocket)
            return
        elif self.pc.iceConnectionState in ["failed", "disconnected", "closed"]:
            logging.info(f"ICE connection {self.pc.iceConnectionState}. Waiting for reconnect...")

            # Wait up to 90 seconds.
            for i in range(90):  
                await asyncio.sleep(1)
                if self.pc.iceConnectionState in ["connected", "completed"]:
                    logging.info("ICE reconnected!")
                    return

            logging.error("Reconnection timed out. Closing connection.")
            await self.clean_exit(self.pc, self.websocket)
        else:
            await self.clean_exit(self.pc, self.websocket)

    async def run_worker(self, pc, peer_id: str, DNS: str, port_number):
        """Main function to run the worker. Contains several event handlers for the WebRTC connection and data channel.
        
        Args:
            pc: RTCPeerConnection object
            peer_id: ID of the worker peer
            DNS: DNS address of the signaling server
            port_number: Port number of the signaling server
        Returns:
            None
        """

        # Register PeerConnection functions with PC object.
        pc.on("datachannel", self.on_datachannel)
        pc.on("iceconnectionstatechange", self.on_iceconnectionstatechange)

        # Set the RTCPeerConnection object for the worker.
        self.pc = pc

        # Establish a WebSocket connection to the signaling server.
        async with websockets.connect(f"{DNS}:{port_number}") as websocket:

            # Set the WebSocket connection for the worker.
            self.websocket = websocket

            # Register the worker with the server.
            await websocket.send(json.dumps({'type': 'register', 'peer_id': peer_id}))
            logging.info(f"{peer_id} sent to signaling server for registration!")

            # Handle incoming messages from server (e.g. answers).
            await self.handle_connection(pc, websocket)
            logging.info(f"{peer_id} connected with client!")

if __name__ == "__main__":
    # Create the worker instance.
    worker = RTCWorkerClient()

    # Create the RTCPeerConnection object.
    pc = RTCPeerConnection()

    # Run the worker 
    asyncio.run(
        worker.run_worker(
            pc,
            peer_id="worker_1",
            DNS="ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com",
            port_number=8080
        )
    )

