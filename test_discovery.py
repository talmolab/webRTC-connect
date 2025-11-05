#!/usr/bin/env python3
"""Test script for signaling server v2.0 discovery features."""

import asyncio
import websockets
import json
import requests
import sys

SIGNALING_HTTP = "http://localhost:8001"
SIGNALING_WS = "ws://localhost:8080"


async def create_room():
    """Create a test room."""
    # Anonymous sign-in
    response = requests.post(f"{SIGNALING_HTTP}/anonymous-signin")
    response.raise_for_status()
    data = response.json()
    id_token = data["id_token"]

    # Create room
    response = requests.post(
        f"{SIGNALING_HTTP}/create-room",
        headers={"Authorization": f"Bearer {id_token}"}
    )
    response.raise_for_status()
    room_data = response.json()

    return {
        "room_id": room_data["room_id"],
        "token": room_data["token"],
        "id_token": id_token
    }


async def test_worker_registration():
    """Test worker registration with metadata."""
    print("\n=== Test 1: Worker Registration ===")

    room = await create_room()
    print(f"Created room: {room['room_id']}")

    # Get worker's own token
    response = requests.post(f"{SIGNALING_HTTP}/anonymous-signin")
    worker_token = response.json()["id_token"]

    # Connect to WebSocket
    async with websockets.connect(SIGNALING_WS) as ws:
        # Register as worker with metadata
        await ws.send(json.dumps({
            "type": "register",
            "peer_id": "Worker-Test-1",
            "room_id": room["room_id"],
            "token": room["token"],
            "id_token": worker_token,
            "role": "worker",
            "metadata": {
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {
                    "gpu_memory_mb": 16384,
                    "gpu_model": "Test GPU",
                    "status": "available",
                    "sleap_version": "1.3.0"
                }
            }
        }))

        # Wait for response
        response = await ws.recv()
        data = json.loads(response)

        if data["type"] == "registered_auth":
            print("✓ Worker registered successfully with metadata")
            return room, worker_token
        else:
            print(f"✗ Unexpected response: {data}")
            sys.exit(1)


async def test_peer_discovery():
    """Test peer discovery."""
    print("\n=== Test 2: Peer Discovery ===")

    room = await create_room()
    print(f"Created room: {room['room_id']}")

    # Register worker
    worker_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
    async with websockets.connect(SIGNALING_WS) as worker_ws:
        await worker_ws.send(json.dumps({
            "type": "register",
            "peer_id": "Worker-Test-2",
            "room_id": room["room_id"],
            "token": room["token"],
            "id_token": worker_token,
            "role": "worker",
            "metadata": {
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {
                    "gpu_memory_mb": 16384,
                    "status": "available"
                }
            }
        }))

        # Wait for registration
        await worker_ws.recv()
        print("✓ Worker registered")

        # Register client in same room
        client_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
        async with websockets.connect(SIGNALING_WS) as client_ws:
            await client_ws.send(json.dumps({
                "type": "register",
                "peer_id": "Client-Test-1",
                "room_id": room["room_id"],
                "token": room["token"],
                "id_token": client_token,
                "role": "client",
                "metadata": {
                    "tags": ["sleap-rtc", "training-client"],
                    "properties": {}
                }
            }))

            # Wait for registration
            await client_ws.recv()
            print("✓ Client registered")

            # Client discovers workers
            await client_ws.send(json.dumps({
                "type": "discover_peers",
                "from_peer_id": "Client-Test-1",
                "filters": {
                    "role": "worker",
                    "tags": ["sleap-rtc", "training-worker"],
                    "properties": {
                        "status": "available"
                    }
                }
            }))

            # Wait for response
            response = await client_ws.recv()
            data = json.loads(response)

            if data["type"] == "peer_list":
                peers = data["peers"]
                print(f"✓ Discovery returned {len(peers)} worker(s)")

                if len(peers) == 1 and peers[0]["peer_id"] == "Worker-Test-2":
                    print("✓ Correct worker discovered")
                    print(f"  - Worker GPU: {peers[0]['metadata']['properties']['gpu_memory_mb']} MB")
                else:
                    print(f"✗ Unexpected peers: {peers}")
                    sys.exit(1)
            else:
                print(f"✗ Unexpected response: {data}")
                sys.exit(1)


async def test_metadata_update():
    """Test metadata updates (status changes)."""
    print("\n=== Test 3: Metadata Updates ===")

    room = await create_room()
    print(f"Created room: {room['room_id']}")

    # Register worker
    worker_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
    async with websockets.connect(SIGNALING_WS) as worker_ws:
        await worker_ws.send(json.dumps({
            "type": "register",
            "peer_id": "Worker-Test-3",
            "room_id": room["room_id"],
            "token": room["token"],
            "id_token": worker_token,
            "role": "worker",
            "metadata": {
                "tags": ["sleap-rtc", "training-worker"],
                "properties": {
                    "status": "available",
                    "gpu_memory_mb": 16384
                }
            }
        }))

        await worker_ws.recv()
        print("✓ Worker registered as 'available'")

        # Update status to busy
        await worker_ws.send(json.dumps({
            "type": "update_metadata",
            "peer_id": "Worker-Test-3",
            "metadata": {
                "properties": {
                    "status": "busy",
                    "current_job_id": "job-123"
                }
            }
        }))

        # Wait for confirmation
        response = await worker_ws.recv()
        data = json.loads(response)

        if data["type"] == "metadata_updated":
            print("✓ Status updated to 'busy'")

            # Verify status in metadata
            if data["metadata"]["properties"]["status"] == "busy":
                print("✓ Confirmed status is 'busy' in server state")
            else:
                print(f"✗ Unexpected status: {data['metadata']['properties']['status']}")
                sys.exit(1)
        else:
            print(f"✗ Unexpected response: {data}")
            sys.exit(1)

        # Register client and verify worker is filtered out
        client_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
        async with websockets.connect(SIGNALING_WS) as client_ws:
            await client_ws.send(json.dumps({
                "type": "register",
                "peer_id": "Client-Test-2",
                "room_id": room["room_id"],
                "token": room["token"],
                "id_token": client_token,
                "role": "client"
            }))

            await client_ws.recv()

            # Discover available workers (should be empty)
            await client_ws.send(json.dumps({
                "type": "discover_peers",
                "from_peer_id": "Client-Test-2",
                "filters": {
                    "role": "worker",
                    "properties": {"status": "available"}
                }
            }))

            response = await client_ws.recv()
            data = json.loads(response)

            if data["type"] == "peer_list" and len(data["peers"]) == 0:
                print("✓ Busy worker correctly filtered out from discovery")
            else:
                print(f"✗ Expected empty peer list, got: {data}")
                sys.exit(1)


async def test_peer_messaging():
    """Test generic peer message routing."""
    print("\n=== Test 4: Peer Message Routing ===")

    room = await create_room()
    print(f"Created room: {room['room_id']}")

    # Register worker
    worker_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
    async with websockets.connect(SIGNALING_WS) as worker_ws:
        await worker_ws.send(json.dumps({
            "type": "register",
            "peer_id": "Worker-Test-4",
            "room_id": room["room_id"],
            "token": room["token"],
            "id_token": worker_token,
            "role": "worker"
        }))
        await worker_ws.recv()
        print("✓ Worker registered")

        # Register client
        client_token = requests.post(f"{SIGNALING_HTTP}/anonymous-signin").json()["id_token"]
        async with websockets.connect(SIGNALING_WS) as client_ws:
            await client_ws.send(json.dumps({
                "type": "register",
                "peer_id": "Client-Test-3",
                "room_id": room["room_id"],
                "token": room["token"],
                "id_token": client_token,
                "role": "client"
            }))
            await client_ws.recv()
            print("✓ Client registered")

            # Client sends peer message to worker
            test_payload = {
                "app_message_type": "test_message",
                "data": "Hello Worker!"
            }

            await client_ws.send(json.dumps({
                "type": "peer_message",
                "from_peer_id": "Client-Test-3",
                "to_peer_id": "Worker-Test-4",
                "payload": test_payload
            }))

            print("✓ Client sent peer message")

            # Worker receives message
            response = await worker_ws.recv()
            data = json.loads(response)

            if (data["type"] == "peer_message" and
                data["from_peer_id"] == "Client-Test-3" and
                data["payload"] == test_payload):
                print("✓ Worker received correct peer message")
            else:
                print(f"✗ Unexpected message: {data}")
                sys.exit(1)


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Signaling Server v2.0")
    print("=" * 60)

    try:
        await test_worker_registration()
        await test_peer_discovery()
        await test_metadata_update()
        await test_peer_messaging()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
