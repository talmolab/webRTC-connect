"""SLEAP-RTC Relay Server.

Lightweight SSE fanout server for training progress and filesystem responses.
Accepts HTTP POST publishes and fans them out to subscribed browser SSE connections.
Buffers recent events per channel for late-joining browsers.

Channels:
  - job_{hex}        : Training events (epoch metrics, status changes)
  - worker:{peer_id} : Worker events (filesystem responses)

Run: uvicorn relay:app --host 0.0.0.0 --port 8081
"""

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

MAX_BUFFER = 200

# Event buffers keyed by channel ID
buffers: dict[str, deque] = {}

# SSE subscribers: each gets an asyncio.Queue
subscribers: dict[str, list[asyncio.Queue]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Relay server starting on :8081")
    yield
    logger.info("Relay server shutting down")


app = FastAPI(title="SLEAP-RTC Relay", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.post("/publish/{channel}", status_code=204)
async def publish(channel: str, request: Request):
    """Publish an event to a channel.

    Called by:
      - Signaling server (localhost) for fs_list_res, job status
      - Training process (HPC outbound) for epoch metrics
    """
    data = await request.json()
    data["_ts"] = time.time()

    # Buffer the event
    if channel not in buffers:
        buffers[channel] = deque(maxlen=MAX_BUFFER)
    buffers[channel].append(data)

    # Fan out to all subscribers
    queues = subscribers.get(channel, [])
    for queue in queues:
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(f"Subscriber queue full for channel {channel}, dropping event")

    logger.info(
        f"Published to {channel}: type={data.get('type', '?')} "
        f"({len(queues)} subscribers)"
    )
    return Response(status_code=204)


@app.get("/stream/{channel}")
async def stream(channel: str, request: Request):
    """SSE stream for a channel.

    Called by dashboard browser. Replays buffered events, then streams live.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    # Register subscriber
    if channel not in subscribers:
        subscribers[channel] = []
    subscribers[channel].append(queue)

    logger.info(
        f"SSE subscriber connected to {channel} "
        f"({len(subscribers[channel])} total)"
    )

    async def event_generator():
        try:
            # Replay buffered events
            if channel in buffers:
                for event in buffers[channel]:
                    yield f"data: {json.dumps(event)}\n\n"

            # Stream live events
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
        finally:
            # Cleanup subscriber
            if channel in subscribers:
                try:
                    subscribers[channel].remove(queue)
                except ValueError:
                    pass
                if not subscribers[channel]:
                    del subscribers[channel]
            logger.info(f"SSE subscriber disconnected from {channel}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "channels": len(buffers),
        "total_subscribers": sum(len(q) for q in subscribers.values()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
