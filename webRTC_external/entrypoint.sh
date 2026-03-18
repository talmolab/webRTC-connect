#!/bin/bash
# Start relay server in background, then signaling server in foreground
uv run python3 relay.py &
RELAY_PID=$!

# If signaling server exits, also kill relay
trap "kill $RELAY_PID 2>/dev/null" EXIT

uv run python3 server.py
