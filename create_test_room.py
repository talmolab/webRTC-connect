#!/usr/bin/env python3
"""Create a test room for local testing."""

import requests
import sys

SIGNALING_HTTP = "http://localhost:8001"


def create_room():
    """Create a test room and print credentials."""
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

    print("✓ Room created successfully!")
    print(f"\nRoom ID: {room_data['room_id']}")
    print(f"Token: {room_data['token']}")
    print("\nExport these for your worker and client:")
    print(f'export SLEAP_RTC_ROOM_ID="{room_data["room_id"]}"')
    print(f'export SLEAP_RTC_ROOM_TOKEN="{room_data["token"]}"')

    return room_data


if __name__ == "__main__":
    try:
        create_room()
    except Exception as e:
        print(f"✗ Failed to create room: {e}")
        sys.exit(1)
