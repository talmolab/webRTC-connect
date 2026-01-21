"""Proof-of-concept: Stream video frames over WebRTC DataChannel.

This client reads an MP4 file, extracts frames, and streams them
to a worker over WebRTC. This is preparation for future sleap-nn
streaming inference support.

Supports frame selection:
- Frame ranges: --frames 0-100,200-300
- Sampling: --sample-rate 10 (every 10th frame)
- Batch mode: --batch-size 8 (send 8 frames per batch)

Usage:
    python frame_client.py <video_path> [options]

Examples:
    python frame_client.py test_video.mp4
    python frame_client.py test_video.mp4 --frames 0-100
    python frame_client.py test_video.mp4 --sample-rate 5
    python frame_client.py test_video.mp4 --batch-size 8
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import cv2
import numpy as np
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

logging.basicConfig(level=logging.INFO)

# Frame transfer settings
CHUNK_SIZE = 64 * 1024  # 64KB chunks (same as file transfer)
MAX_BUFFER = 16 * 1024 * 1024  # 16MB buffer limit


def parse_frame_ranges(frame_spec: str, total_frames: int) -> list[int]:
    """Parse frame specification string into list of frame indices.

    Args:
        frame_spec: Comma-separated ranges, e.g., "0-100,200-300,500"
        total_frames: Total number of frames in video

    Returns:
        Sorted list of unique frame indices
    """
    if not frame_spec:
        return list(range(total_frames))

    frames = set()
    for part in frame_spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            start = int(start)
            end = min(int(end), total_frames - 1)
            frames.update(range(start, end + 1))
        else:
            idx = int(part)
            if idx < total_frames:
                frames.add(idx)

    return sorted(frames)


class FrameStreamClient:
    """Client that streams video frames over WebRTC DataChannel."""

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.pc = RTCPeerConnection()
        self.channel = None
        self.frames_sent = 0
        self.streaming = False

    async def stream_frames(
        self,
        frame_indices: list[int] = None,
        sample_rate: int = 1,
        batch_size: int = 1,
        target_fps: float = None,
    ):
        """Extract frames from video and send over DataChannel.

        Args:
            frame_indices: Specific frame indices to send. If None, sends all frames.
            sample_rate: Send every Nth frame (1 = all frames, 10 = every 10th).
            batch_size: Number of frames to group in each batch message.
            target_fps: Target frames per second. If None, sends as fast as possible.
        """
        if not self.channel or self.channel.readyState != "open":
            logging.error("DataChannel not open")
            return

        # Open video file
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            logging.error(f"Failed to open video: {self.video_path}")
            return

        # Get video properties
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        logging.info(f"Video: {width}x{height}, {total_frames} frames @ {video_fps} FPS")

        # Determine which frames to send
        if frame_indices is None:
            frame_indices = list(range(total_frames))

        # Apply sampling
        if sample_rate > 1:
            frame_indices = frame_indices[::sample_rate]

        frames_to_send = len(frame_indices)
        logging.info(f"Will send {frames_to_send} frames (sample_rate={sample_rate}, batch_size={batch_size})")

        # Send stream start metadata
        self.channel.send(json.dumps({
            "type": "STREAM_START",
            "video_name": os.path.basename(self.video_path),
            "total_frames": total_frames,
            "frames_to_send": frames_to_send,
            "frame_indices": frame_indices[:100] if len(frame_indices) > 100 else frame_indices,  # First 100 for reference
            "sample_rate": sample_rate,
            "batch_size": batch_size,
            "fps": video_fps,
            "width": width,
            "height": height,
        }))

        # Calculate frame delay
        frame_delay = 1.0 / target_fps if target_fps and target_fps > 0 else 0

        self.streaming = True
        self.frames_sent = 0
        current_batch = []

        for i, frame_idx in enumerate(frame_indices):
            if not self.streaming:
                break

            # Seek to frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()

            if not ret:
                logging.warning(f"Failed to read frame {frame_idx}")
                continue

            # Batch mode: accumulate frames
            if batch_size > 1:
                current_batch.append((frame_idx, frame))
                if len(current_batch) >= batch_size:
                    await self._send_batch(current_batch)
                    self.frames_sent += len(current_batch)
                    current_batch = []
            else:
                # Single frame mode
                await self._send_frame(frame, frame_idx)
                self.frames_sent += 1

            # Progress logging
            if self.frames_sent % 30 == 0 or self.frames_sent == frames_to_send:
                logging.info(f"Sent {self.frames_sent}/{frames_to_send} frames")

            # Rate limiting
            if frame_delay > 0:
                await asyncio.sleep(frame_delay)

        # Send remaining batch
        if current_batch:
            await self._send_batch(current_batch)
            self.frames_sent += len(current_batch)

        cap.release()

        # Send stream end
        self.channel.send(json.dumps({
            "type": "STREAM_END",
            "total_sent": self.frames_sent,
        }))
        logging.info(f"Stream complete: {self.frames_sent} frames sent")

    async def _send_batch(self, batch: list[tuple[int, np.ndarray]]):
        """Send a batch of frames.

        Batch protocol:
        1. Send "BATCH_START::{num_frames}"
        2. Send each frame (using _send_frame)
        3. Send "BATCH_END::{num_frames}"
        """
        self.channel.send(f"BATCH_START::{len(batch)}")

        for frame_idx, frame in batch:
            await self._send_frame(frame, frame_idx)

        self.channel.send(f"BATCH_END::{len(batch)}")

    async def _send_frame(self, frame: np.ndarray, frame_id: int):
        """Send a single frame over the DataChannel.

        Frame protocol:
        1. Send metadata: "FRAME_META::{frame_id}:{height}:{width}:{channels}:{dtype}:{nbytes}"
        2. Send binary chunks (64KB each)
        3. Send "FRAME_END::{frame_id}"
        """
        height, width = frame.shape[:2]
        channels = frame.shape[2] if len(frame.shape) > 2 else 1
        dtype = str(frame.dtype)

        # Convert frame to bytes
        frame_bytes = frame.tobytes()
        nbytes = len(frame_bytes)

        # Send metadata
        meta = f"FRAME_META::{frame_id}:{height}:{width}:{channels}:{dtype}:{nbytes}"
        self.channel.send(meta)

        # Send frame data in chunks
        offset = 0
        while offset < nbytes:
            # Wait if buffer is full
            while self.channel.bufferedAmount > MAX_BUFFER:
                await asyncio.sleep(0.01)

            chunk = frame_bytes[offset:offset + CHUNK_SIZE]
            self.channel.send(chunk)
            offset += len(chunk)

        # Send frame end marker
        self.channel.send(f"FRAME_END::{frame_id}")

    def stop_streaming(self):
        """Stop the frame streaming."""
        self.streaming = False


async def run_frame_client(
    video_path: str,
    dns: str,
    port: int,
    frame_spec: str = None,
    sample_rate: int = 1,
    batch_size: int = 1,
    target_fps: float = None,
):
    """Main client function to stream video frames to a worker."""

    # Pre-calculate frame indices
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    frame_indices = parse_frame_ranges(frame_spec, total_frames) if frame_spec else None

    client = FrameStreamClient(video_path)

    # Create DataChannel
    client.channel = client.pc.createDataChannel("frame-stream")
    logging.info("DataChannel created")

    @client.channel.on("open")
    async def on_channel_open():
        logging.info("DataChannel open - starting frame stream")
        await client.stream_frames(
            frame_indices=frame_indices,
            sample_rate=sample_rate,
            batch_size=batch_size,
            target_fps=target_fps,
        )

    @client.channel.on("message")
    def on_channel_message(message):
        logging.info(f"Worker response: {message}")

    @client.pc.on("iceconnectionstatechange")
    async def on_ice_state_change():
        logging.info(f"ICE state: {client.pc.iceConnectionState}")
        if client.pc.iceConnectionState == "failed":
            client.stop_streaming()
            await client.pc.close()

    # Connect to signaling server
    async with websockets.connect(f"{dns}:{port}") as websocket:
        # Register
        await websocket.send(json.dumps({
            "type": "register",
            "peer_id": "frame-client"
        }))
        logging.info("Registered with signaling server")

        # Query for workers
        await websocket.send(json.dumps({"type": "query"}))
        response = await websocket.recv()
        workers = json.loads(response).get("peers", [])

        if not workers:
            logging.error("No workers available")
            return

        target_worker = workers[0]
        logging.info(f"Connecting to worker: {target_worker}")

        # Create and send offer
        await client.pc.setLocalDescription(await client.pc.createOffer())
        await websocket.send(json.dumps({
            "type": "offer",
            "target": target_worker,
            "sdp": client.pc.localDescription.sdp
        }))
        logging.info("Offer sent")

        # Handle signaling messages
        async for message in websocket:
            data = json.loads(message)

            if data.get("type") == "answer":
                await client.pc.setRemoteDescription(
                    RTCSessionDescription(sdp=data["sdp"], type="answer")
                )
                logging.info("Answer received - connection establishing")

            elif data.get("type") == "candidate":
                candidate = data.get("candidate")
                if candidate:
                    await client.pc.addIceCandidate(candidate)

            elif data.get("type") == "quit":
                logging.info("Worker quit")
                break

        # Wait for streaming to complete
        while client.streaming:
            await asyncio.sleep(0.5)

    await client.pc.close()
    logging.info("Client closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stream video frames over WebRTC to a worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stream all frames
  python frame_client.py video.mp4

  # Stream frames 0-100 and 200-300
  python frame_client.py video.mp4 --frames 0-100,200-300

  # Stream every 10th frame
  python frame_client.py video.mp4 --sample-rate 10

  # Send frames in batches of 8
  python frame_client.py video.mp4 --batch-size 8

  # Combine options
  python frame_client.py video.mp4 --frames 0-1000 --sample-rate 5 --batch-size 4
        """,
    )
    parser.add_argument("video_path", help="Path to video file (MP4, AVI, etc.)")
    parser.add_argument("--host", default="ws://localhost", help="Signaling server URL (default: ws://localhost)")
    parser.add_argument("--port", type=int, default=8080, help="Signaling server port (default: 8080)")
    parser.add_argument("--frames", dest="frame_spec", help="Frame ranges to send, e.g., '0-100,200-300'")
    parser.add_argument("--sample-rate", type=int, default=1, help="Send every Nth frame (default: 1 = all frames)")
    parser.add_argument("--batch-size", type=int, default=1, help="Frames per batch (default: 1 = no batching)")
    parser.add_argument("--fps", type=float, default=None, help="Target FPS rate limit (default: no limit)")

    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found: {args.video_path}")
        sys.exit(1)

    try:
        asyncio.run(run_frame_client(
            video_path=args.video_path,
            dns=args.host,
            port=args.port,
            frame_spec=args.frame_spec,
            sample_rate=args.sample_rate,
            batch_size=args.batch_size,
            target_fps=args.fps,
        ))
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
