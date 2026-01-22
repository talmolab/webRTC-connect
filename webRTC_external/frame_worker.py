"""Proof-of-concept: Receive video frames over WebRTC DataChannel.

This worker receives frames streamed from a client and saves them
to an output folder. This is preparation for future sleap-nn
streaming inference support.

Usage:
    python frame_worker.py [output_dir] [signaling_server_url] [port]

Example:
    python frame_worker.py ./received_frames ws://localhost 8080
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate

logging.basicConfig(level=logging.INFO)


class FrameStreamWorker:
    """Worker that receives video frames over WebRTC DataChannel."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.pc = RTCPeerConnection()
        self.websocket = None
        self.peer_id = f"frame-worker-{uuid.uuid4().hex[:8]}"

        # Frame reception state
        self.current_frame_id = None
        self.current_frame_meta = None
        self.current_frame_buffer = bytearray()
        self.frames_received = 0

        # Batch state
        self.in_batch = False
        self.batch_frames = []

        # Stream metadata
        self.stream_info = None
        self.frame_output_dir = None

    def setup_handlers(self):
        """Setup WebRTC event handlers."""

        @self.pc.on("datachannel")
        def on_datachannel(channel):
            logging.info(f"DataChannel received: {channel.label}")
            self._setup_channel_handlers(channel)

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_state_change():
            logging.info(f"ICE state: {self.pc.iceConnectionState}")
            if self.pc.iceConnectionState == "failed":
                await self.cleanup()

    def _setup_channel_handlers(self, channel):
        """Setup handlers for the data channel."""

        @channel.on("open")
        def on_open():
            logging.info(f"DataChannel {channel.label} is open")

        @channel.on("message")
        async def on_message(message):
            await self._handle_message(message, channel)

    async def _handle_message(self, message, channel):
        """Handle incoming messages (metadata or frame data)."""

        if isinstance(message, str):
            # Handle string messages (metadata, control)
            if message.startswith("{"):
                # JSON message
                data = json.loads(message)
                await self._handle_json_message(data, channel)

            elif message.startswith("BATCH_START::"):
                # Start of a batch
                _, num_frames = message.split("::", 1)
                self.in_batch = True
                self.batch_frames = []
                logging.debug(f"Batch starting: {num_frames} frames")

            elif message.startswith("BATCH_END::"):
                # End of a batch - process all frames
                _, num_frames = message.split("::", 1)
                self.in_batch = False
                logging.info(f"Batch complete: {len(self.batch_frames)} frames")
                # Batch frames are already saved individually
                self.batch_frames = []

            elif message.startswith("FRAME_META::"):
                # Frame metadata: FRAME_META::{frame_id}:{height}:{width}:{channels}:{dtype}:{nbytes}
                _, meta = message.split("::", 1)
                parts = meta.split(":")
                self.current_frame_id = int(parts[0])
                self.current_frame_meta = {
                    "frame_id": int(parts[0]),
                    "height": int(parts[1]),
                    "width": int(parts[2]),
                    "channels": int(parts[3]),
                    "dtype": parts[4],
                    "nbytes": int(parts[5]),
                }
                self.current_frame_buffer = bytearray()

            elif message.startswith("FRAME_END::"):
                # Frame complete
                _, frame_id_str = message.split("::", 1)
                frame_id = int(frame_id_str)
                await self._save_frame(frame_id, channel)

                # Track batch frames
                if self.in_batch:
                    self.batch_frames.append(frame_id)

        elif isinstance(message, bytes):
            # Binary frame data
            if message == b"KEEP_ALIVE":
                return
            self.current_frame_buffer.extend(message)

    async def _handle_json_message(self, data: dict, channel):
        """Handle JSON control messages."""
        msg_type = data.get("type")

        if msg_type == "STREAM_START":
            self.stream_info = data
            video_name = data.get("video_name", "unknown").replace(".", "_")
            self.frame_output_dir = self.output_dir / f"frames_{video_name}"
            self.frame_output_dir.mkdir(parents=True, exist_ok=True)
            self.frames_received = 0

            logging.info(f"Stream starting: {data}")
            logging.info(f"Saving frames to: {self.frame_output_dir}")

            channel.send(json.dumps({
                "type": "STREAM_ACK",
                "status": "ready",
                "output_dir": str(self.frame_output_dir),
            }))

        elif msg_type == "STREAM_END":
            logging.info(f"Stream ended. Received {self.frames_received} frames")
            logging.info(f"Frames saved to: {self.frame_output_dir}")

            channel.send(json.dumps({
                "type": "STREAM_COMPLETE",
                "frames_received": self.frames_received,
                "output_dir": str(self.frame_output_dir),
            }))

    async def _save_frame(self, frame_id: int, channel):
        """Reconstruct and save frame from buffer."""
        if self.current_frame_meta is None:
            logging.error(f"No metadata for frame {frame_id}")
            return

        meta = self.current_frame_meta

        # Verify data size
        expected_size = meta["nbytes"]
        actual_size = len(self.current_frame_buffer)

        if actual_size != expected_size:
            logging.warning(
                f"Frame {frame_id} size mismatch: expected {expected_size}, got {actual_size}"
            )

        # Reconstruct numpy array
        dtype = np.dtype(meta["dtype"])
        frame = np.frombuffer(self.current_frame_buffer, dtype=dtype)

        # Reshape to image dimensions
        if meta["channels"] == 1:
            shape = (meta["height"], meta["width"])
        else:
            shape = (meta["height"], meta["width"], meta["channels"])

        try:
            frame = frame.reshape(shape)
        except ValueError as e:
            logging.error(f"Failed to reshape frame {frame_id}: {e}")
            return

        # Save frame as image
        if self.frame_output_dir:
            frame_path = self.frame_output_dir / f"frame_{frame_id:06d}.png"
            cv2.imwrite(str(frame_path), frame)

        self.frames_received += 1

        # Log progress
        if self.frames_received % 30 == 0:
            total = self.stream_info.get("total_frames", "?") if self.stream_info else "?"
            logging.info(f"Received frame {self.frames_received}/{total}")

        # Clear buffer for next frame
        self.current_frame_buffer = bytearray()
        self.current_frame_meta = None

    async def cleanup(self):
        """Cleanup connections."""
        logging.info("Cleaning up...")
        if self.pc:
            await self.pc.close()
        if self.websocket:
            await self.websocket.close()


async def run_frame_worker(output_dir: str, dns: str, port: int):
    """Main worker function to receive video frames from a client."""

    worker = FrameStreamWorker(output_dir)
    worker.setup_handlers()

    logging.info(f"Worker {worker.peer_id} starting")
    logging.info(f"Frames will be saved to: {output_dir}")

    # Connect to signaling server
    async with websockets.connect(f"{dns}:{port}") as websocket:
        worker.websocket = websocket

        # Register as worker
        await websocket.send(json.dumps({
            "type": "register",
            "peer_id": worker.peer_id,
        }))
        logging.info(f"Registered as {worker.peer_id}")

        # Handle signaling messages
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "offer":
                logging.info("Received offer from client")
                sender = data.get("sender")

                # Set remote description
                await worker.pc.setRemoteDescription(
                    RTCSessionDescription(sdp=data["sdp"], type="offer")
                )

                # Create and send answer
                await worker.pc.setLocalDescription(await worker.pc.createAnswer())
                await websocket.send(json.dumps({
                    "type": "answer",
                    "target": sender,
                    "sdp": worker.pc.localDescription.sdp,
                }))
                logging.info("Answer sent - waiting for frames...")

            elif msg_type == "candidate":
                candidate = data.get("candidate")
                if candidate:
                    await worker.pc.addIceCandidate(candidate)

            elif msg_type == "quit":
                logging.info("Client quit")
                break

    await worker.cleanup()
    logging.info("Worker closed")


if __name__ == "__main__":
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "./received_frames"
    dns = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8080

    try:
        asyncio.run(run_frame_worker(output_dir, dns, port))
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
