"""ICE server configuration for WebRTC connections."""

import os


def get_ice_servers(connection_type: str = "client") -> list[dict]:
    """Return ICE server configuration based on connection type.

    Args:
        connection_type:
            - "client": Client-to-worker connections (includes TURN)
            - "mesh": Worker-to-worker connections (STUN only)

    Returns:
        List of ICE server configurations.
    """
    # STUN servers (free, always included)
    stun_servers = [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {"urls": ["stun:stun1.l.google.com:19302"]},  # Backup
    ]

    if connection_type == "mesh":
        # Worker-to-worker: STUN only (same network, direct connection)
        return stun_servers

    # Client-to-worker: STUN + TURN
    turn_host = os.environ.get("TURN_HOST", "")
    turn_port = os.environ.get("TURN_PORT", "3478")
    turn_username = os.environ.get("TURN_USERNAME", "sleap")
    turn_password = os.environ.get("TURN_PASSWORD", "")

    ice_servers = stun_servers.copy()

    # Add TURN only if credentials are configured
    if turn_host and turn_password:
        ice_servers.append({
            "urls": [
                f"turn:{turn_host}:{turn_port}?transport=udp",
                f"turn:{turn_host}:{turn_port}?transport=tcp",
            ],
            "username": turn_username,
            "credential": turn_password,
        })

    return ice_servers
