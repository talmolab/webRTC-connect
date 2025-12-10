"""ICE server configuration for WebRTC connections.

With Tailscale networking, STUN/TURN servers are generally not needed since
all devices can communicate directly over the Tailscale mesh network.
This module provides a minimal configuration that can be extended if needed.
"""


def get_ice_servers(connection_type: str = "client") -> list[dict]:  # noqa: ARG001
    """Return ICE server configuration based on connection type.

    When using Tailscale, ICE servers are typically not required since
    Tailscale provides direct connectivity between all devices on the network.

    Args:
        connection_type:
            - "client": Client-to-worker connections
            - "mesh": Worker-to-worker connections

    Returns:
        List of ICE server configurations (empty by default with Tailscale).
    """
    # With Tailscale, direct connections work without STUN/TURN
    # Return empty list by default - Tailscale handles NAT traversal
    return []
