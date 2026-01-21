# /// script
# dependencies = [
#   "boto3",
#   "fastapi",
#   "python-jose[cryptography]",
#   "requests",
#   "uvicorn",
#   "websockets",
# ]
# ///

import asyncio
import base64
import boto3
import hashlib
import hmac
import requests
import secrets
import threading
import time
import json
import logging
import websockets
import uvicorn
import uuid
import os

from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, jwk
from jose.exceptions import JWTError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from pydantic import BaseModel
from typing import Optional
from ice_config import get_ice_servers

# Setup logging.
logging.basicConfig(level=logging.INFO)

# Global variables to store rooms and peer connections for websocket objects.
# Enhanced structure to support roles and metadata:
# ROOMS[room_id] = {
#     "created_by": uid,
#     "token": token,
#     "expires_at": timestamp,
#     "peers": {
#         peer_id: {
#             "websocket": <WebSocket>,
#             "role": "worker" | "client" | "peer",
#             "metadata": {...},  # Application-specific data
#             "connected_at": timestamp
#         }
#     }
# }
ROOMS = {}
PEER_TO_ROOM = {}
ROOM_ADMINS = {}  # room_id -> admin_peer_id (tracks admin per room for mesh networking)

# Metrics tracking
METRICS = {
    "total_connections": 0,
    "total_messages": 0,
    "active_connections": 0,
    "rooms_created": 0
}

# AWS Cognito and DynamoDB initialization/configuration.
# Cognito is optional (legacy auth) - new auth uses GitHub OAuth
COGNITO_REGION = os.environ.get('COGNITO_REGION', 'us-west-1')
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_APP_CLIENT_ID = os.environ.get('COGNITO_APP_CLIENT_ID', '')

# Only fetch Cognito JWKS if configured
JWKS = []
if COGNITO_USER_POOL_ID:
    try:
        COGNITO_KEYS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
        JWKS = requests.get(COGNITO_KEYS_URL).json()["keys"]
    except Exception as e:
        logging.warning(f"Could not fetch Cognito JWKS: {e}")

# Initialize AWS SDK (boto3 Python API for AWS).
cognito_client = boto3.client('cognito-idp', region_name=COGNITO_REGION) if COGNITO_USER_POOL_ID else None
dynamodb = boto3.resource('dynamodb', region_name=COGNITO_REGION)
rooms_table = dynamodb.Table('rooms')

# =============================================================================
# GitHub OAuth Configuration (New Auth System)
# =============================================================================
GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
GITHUB_REDIRECT_URI = os.environ.get('GITHUB_REDIRECT_URI', '')

# JWT Configuration for SLEAP-RTC tokens
# Keys use '|' as newline separator for single-line env vars
SLEAP_JWT_PRIVATE_KEY_RAW = os.environ.get('SLEAP_JWT_PRIVATE_KEY', '').replace('|', '\n')
SLEAP_JWT_PUBLIC_KEY_RAW = os.environ.get('SLEAP_JWT_PUBLIC_KEY', '').replace('|', '\n')
SLEAP_JWT_ALGORITHM = "RS256"
SLEAP_JWT_ISSUER = "sleap-rtc"
SLEAP_JWT_AUDIENCE = "sleap-rtc"
SLEAP_JWT_EXPIRY_DAYS = 7

# New DynamoDB tables for auth
users_table = dynamodb.Table('sleap_users')
worker_tokens_table = dynamodb.Table('sleap_worker_tokens')
room_memberships_table = dynamodb.Table('sleap_room_memberships')

# In-memory store for room invites (short-lived, no need for DynamoDB)
ROOM_INVITES = {}  # invite_code -> {room_id, created_by, expires_at}

# =============================================================================
# FastAPI App (Room Creation + Metrics)
# =============================================================================
app = FastAPI(title="SLEAP-RTC Signaling Server", version="3.0.0")

# Add CORS middleware for GitHub Pages dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your GitHub Pages domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Pydantic Models for Request/Response Validation
# =============================================================================
class GitHubCallbackRequest(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


class CreateTokenRequest(BaseModel):
    room_id: str
    worker_name: str
    expires_in_days: Optional[int] = 7


class JoinRoomRequest(BaseModel):
    invite_code: str


class CreateRoomRequest(BaseModel):
    name: Optional[str] = None


def verify_cognito_token(token):
    """Verify a Cognito JWT token (legacy auth)."""
    if not JWKS or not COGNITO_USER_POOL_ID:
        raise HTTPException(status_code=501, detail="Cognito authentication not configured")
    try:
        claims = jwt.decode(
            token,
            JWKS,
            algorithms=["RS256"],
            audience=COGNITO_APP_CLIENT_ID,
            issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        )
        return claims
    except jwt.JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {e}")


# =============================================================================
# JWT Utilities for SLEAP-RTC Authentication (2.1)
# =============================================================================
def generate_sleap_jwt(user_id: str, username: str) -> str:
    """Generate a SLEAP-RTC JWT token for an authenticated user.

    Args:
        user_id: GitHub user ID
        username: GitHub username

    Returns:
        Signed JWT token string
    """
    if not SLEAP_JWT_PRIVATE_KEY_RAW:
        raise HTTPException(status_code=500, detail="JWT private key not configured")

    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(days=SLEAP_JWT_EXPIRY_DAYS),
        "iss": SLEAP_JWT_ISSUER,
        "aud": SLEAP_JWT_AUDIENCE,
    }

    token = jwt.encode(
        payload,
        SLEAP_JWT_PRIVATE_KEY_RAW,
        algorithm=SLEAP_JWT_ALGORITHM
    )
    return token


def verify_sleap_jwt(token: str) -> dict:
    """Verify a SLEAP-RTC JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded JWT claims

    Raises:
        HTTPException: If token is invalid or expired
    """
    if not SLEAP_JWT_PUBLIC_KEY_RAW:
        logging.error("[AUTH] JWT public key not configured")
        raise HTTPException(status_code=500, detail="JWT public key not configured")

    try:
        logging.info(f"[AUTH] Verifying JWT token: {token[:50]}...")
        logging.info(f"[AUTH] Public key length: {len(SLEAP_JWT_PUBLIC_KEY_RAW)}")
        claims = jwt.decode(
            token,
            SLEAP_JWT_PUBLIC_KEY_RAW,
            algorithms=[SLEAP_JWT_ALGORITHM],
            audience=SLEAP_JWT_AUDIENCE,
            issuer=SLEAP_JWT_ISSUER
        )
        logging.info(f"[AUTH] JWT verified successfully for user: {claims.get('username')}")
        return claims
    except JWTError as e:
        logging.error(f"[AUTH] JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def get_user_from_auth_header(authorization: str) -> dict:
    """Extract and verify user from Authorization header.

    Args:
        authorization: Authorization header value (Bearer <token>)

    Returns:
        JWT claims dict with user_id and username
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.replace("Bearer ", "")
    return verify_sleap_jwt(token)


def generate_api_key() -> str:
    """Generate a unique API key for worker authentication.

    Returns:
        API key string with 'slp_' prefix
    """
    # Generate 24 random bytes (192 bits) and encode as base64
    random_bytes = secrets.token_bytes(24)
    key = base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')
    return f"slp_{key}"


def generate_otp_secret() -> str:
    """Generate a TOTP secret for P2P authentication.

    Returns:
        Base32-encoded secret (160 bits / 32 characters)
    """
    # Generate 20 random bytes (160 bits) for TOTP
    random_bytes = secrets.token_bytes(20)
    # Encode as base32 (standard for TOTP)
    secret = base64.b32encode(random_bytes).decode('utf-8')
    return secret


# =============================================================================
# GitHub OAuth Endpoints (2.2)
# =============================================================================
@app.post("/api/auth/github/callback")
async def github_oauth_callback(request: GitHubCallbackRequest):
    """Exchange GitHub OAuth code for SLEAP-RTC JWT.

    This endpoint:
    1. Exchanges the authorization code for a GitHub access token
    2. Fetches GitHub user info
    3. Creates/updates user in DynamoDB
    4. Returns a SLEAP-RTC JWT
    """
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

    # Debug logging
    redirect_uri_to_use = request.redirect_uri or GITHUB_REDIRECT_URI
    logging.info(f"[AUTH] GitHub callback - code: {request.code[:10]}..., redirect_uri: {redirect_uri_to_use}")

    # Exchange code for access token
    token_response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": request.code,
            "redirect_uri": redirect_uri_to_use,
        }
    )

    token_data = token_response.json()
    logging.info(f"[AUTH] GitHub token response: {token_data}")
    if "error" in token_data:
        raise HTTPException(
            status_code=400,
            detail=f"GitHub OAuth error: {token_data.get('error_description', token_data['error'])}"
        )

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token received from GitHub")

    # Fetch GitHub user info
    user_response = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
    )

    if user_response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch GitHub user info")

    github_user = user_response.json()
    user_id = str(github_user["id"])
    username = github_user["login"]
    avatar_url = github_user.get("avatar_url", "")
    email = github_user.get("email", "")

    # Create/update user in DynamoDB
    now = datetime.utcnow().isoformat()
    try:
        # Check if user exists
        existing = users_table.get_item(Key={"user_id": user_id})

        if "Item" in existing:
            # Update last_login
            users_table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="SET last_login = :now, avatar_url = :avatar",
                ExpressionAttributeValues={":now": now, ":avatar": avatar_url}
            )
        else:
            # Create new user
            users_table.put_item(Item={
                "user_id": user_id,
                "username": username,
                "email": email,
                "avatar_url": avatar_url,
                "created_at": now,
                "last_login": now,
            })

        logging.info(f"[AUTH] GitHub user logged in: {username} ({user_id})")

    except Exception as e:
        logging.error(f"[AUTH] Failed to save user: {e}")
        # Continue anyway - user can still get JWT

    # Generate SLEAP-RTC JWT
    jwt_token = generate_sleap_jwt(user_id, username)

    return {
        "token": jwt_token,
        "user": {
            "user_id": user_id,
            "username": username,
            "avatar_url": avatar_url,
        }
    }


# =============================================================================
# Token Management Endpoints (2.3)
# =============================================================================
@app.post("/api/auth/token")
async def create_worker_token(request: CreateTokenRequest, authorization: str = Header(...)):
    """Create a new worker API token for a room.

    The token is an API key (slp_xxx) for signaling server auth.
    OTP verification uses the room's OTP secret (one per room, not per worker).
    """
    # Verify JWT and get user
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]
    username = claims.get("username", "unknown")

    # Verify user has access to the room
    try:
        membership = room_memberships_table.get_item(
            Key={"user_id": user_id, "room_id": request.room_id}
        )
        if "Item" not in membership:
            raise HTTPException(status_code=403, detail="You don't have access to this room")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[TOKEN] Failed to check room membership: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify room access")

    # Generate API key (no OTP - that's per room now)
    token_id = generate_api_key()
    now = datetime.utcnow()
    expires_at = (now + timedelta(days=request.expires_in_days)).isoformat() if request.expires_in_days else None

    # Store token in DynamoDB (no otp_secret - it's on the room)
    token_item = {
        "token_id": token_id,
        "user_id": user_id,
        "room_id": request.room_id,
        "worker_name": request.worker_name,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
        "revoked_at": None,
    }

    try:
        worker_tokens_table.put_item(Item=token_item)
        logging.info(f"[TOKEN] Created token for {username}: {request.worker_name} in room {request.room_id}")
    except Exception as e:
        logging.error(f"[TOKEN] Failed to create token: {e}")
        raise HTTPException(status_code=500, detail="Failed to create token")

    return {
        "token_id": token_id,
        "room_id": request.room_id,
        "worker_name": request.worker_name,
        "expires_at": expires_at,
        "note": "OTP verification uses the room's OTP secret (from room creation)",
    }


@app.get("/api/auth/tokens")
async def list_tokens(authorization: str = Header(...)):
    """List all tokens owned by the authenticated user."""
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    try:
        response = worker_tokens_table.query(
            IndexName="user_id-index",
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id}
        )

        # Return tokens without otp_secret
        tokens = []
        for item in response.get("Items", []):
            tokens.append({
                "token_id": item["token_id"],
                "room_id": item["room_id"],
                "worker_name": item["worker_name"],
                "created_at": item["created_at"],
                "expires_at": item.get("expires_at"),
                "revoked_at": item.get("revoked_at"),
                "is_active": item.get("revoked_at") is None,
            })

        return {"tokens": tokens}

    except Exception as e:
        logging.error(f"[TOKEN] Failed to list tokens: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tokens")


@app.delete("/api/auth/token/{token_id}")
async def revoke_token(token_id: str, authorization: str = Header(...)):
    """Revoke a worker token."""
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    try:
        # Get token to verify ownership
        response = worker_tokens_table.get_item(Key={"token_id": token_id})
        if "Item" not in response:
            raise HTTPException(status_code=404, detail="Token not found")

        token = response["Item"]
        if token["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this token")

        # Revoke token
        now = datetime.utcnow().isoformat()
        worker_tokens_table.update_item(
            Key={"token_id": token_id},
            UpdateExpression="SET revoked_at = :now",
            ExpressionAttributeValues={":now": now}
        )

        logging.info(f"[TOKEN] Revoked token: {token_id}")
        return {"revoked": True, "token_id": token_id}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[TOKEN] Failed to revoke token: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke token")


# =============================================================================
# Room Management Endpoints (2.4)
# =============================================================================
@app.get("/api/auth/rooms")
async def list_rooms(authorization: str = Header(...)):
    """List all rooms the authenticated user has access to."""
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    try:
        response = room_memberships_table.query(
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id}
        )

        rooms = []
        for item in response.get("Items", []):
            rooms.append({
                "room_id": item["room_id"],
                "role": item["role"],
                "joined_at": item["joined_at"],
            })

        return {"rooms": rooms}

    except Exception as e:
        logging.error(f"[ROOM] Failed to list rooms: {e}")
        raise HTTPException(status_code=500, detail="Failed to list rooms")


@app.post("/api/auth/rooms/{room_id}/invite")
async def create_room_invite(room_id: str, authorization: str = Header(...)):
    """Generate an invite code for a room. Only room owners can create invites."""
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    try:
        # Verify user is owner of the room
        membership = room_memberships_table.get_item(
            Key={"user_id": user_id, "room_id": room_id}
        )
        if "Item" not in membership or membership["Item"].get("role") != "owner":
            raise HTTPException(status_code=403, detail="Only room owners can create invites")

        # Generate invite code (6 characters, 1 hour expiry)
        invite_code = secrets.token_urlsafe(6)[:8].upper()
        expires_at = time.time() + 3600  # 1 hour

        ROOM_INVITES[invite_code] = {
            "room_id": room_id,
            "created_by": user_id,
            "expires_at": expires_at,
        }

        logging.info(f"[ROOM] Created invite {invite_code} for room {room_id}")

        return {
            "invite_code": invite_code,
            "room_id": room_id,
            "expires_in_seconds": 3600,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[ROOM] Failed to create invite: {e}")
        raise HTTPException(status_code=500, detail="Failed to create invite")


@app.post("/api/auth/rooms/join")
async def join_room(request: JoinRoomRequest, authorization: str = Header(...)):
    """Join a room using an invite code."""
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    invite_code = request.invite_code.upper()

    # Validate invite code
    if invite_code not in ROOM_INVITES:
        raise HTTPException(status_code=400, detail="Invalid invite code")

    invite = ROOM_INVITES[invite_code]

    if time.time() > invite["expires_at"]:
        del ROOM_INVITES[invite_code]
        raise HTTPException(status_code=400, detail="Invite code expired")

    room_id = invite["room_id"]
    invited_by = invite["created_by"]

    try:
        # Check if already a member
        existing = room_memberships_table.get_item(
            Key={"user_id": user_id, "room_id": room_id}
        )
        if "Item" in existing:
            return {"message": "Already a member", "room_id": room_id}

        # Add membership
        now = datetime.utcnow().isoformat()
        room_memberships_table.put_item(Item={
            "user_id": user_id,
            "room_id": room_id,
            "role": "member",
            "invited_by": invited_by,
            "joined_at": now,
        })

        logging.info(f"[ROOM] User {user_id} joined room {room_id}")

        # Clean up used invite (optional - could allow multiple uses)
        # del ROOM_INVITES[invite_code]

        return {"message": "Joined successfully", "room_id": room_id}

    except Exception as e:
        logging.error(f"[ROOM] Failed to join room: {e}")
        raise HTTPException(status_code=500, detail="Failed to join room")


@app.post("/api/auth/rooms")
async def create_authenticated_room(
    request: CreateRoomRequest = None,
    authorization: str = Header(...)
):
    """Create a new room for an authenticated user.

    This creates both the room in DynamoDB and the ownership record.
    Also generates a single OTP secret for the room (shared by all workers).
    """
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]
    username = claims.get("username", "user")

    # Get optional room name from request
    room_name = request.name if request else None

    # Generate room ID, token, and OTP secret
    room_id = str(uuid.uuid4())[:8]
    room_token = str(uuid.uuid4())[:6]
    otp_secret = generate_otp_secret()  # One OTP per room, not per worker
    expires_at = int((datetime.utcnow() + timedelta(hours=24)).timestamp())  # 24 hours TTL
    now = datetime.utcnow().isoformat()

    # Generate OTP URI for authenticator apps
    otp_uri = f"otpauth://totp/SLEAP-RTC:{room_id}?secret={otp_secret}&issuer=SLEAP-RTC"

    try:
        # Create room in rooms table (now includes otp_secret)
        room_item = {
            "room_id": room_id,
            "created_by": user_id,
            "token": room_token,
            "otp_secret": otp_secret,  # Room-level OTP
            "expires_at": expires_at,
        }
        if room_name:
            room_item["name"] = room_name
        rooms_table.put_item(Item=room_item)

        # Create ownership record
        room_memberships_table.put_item(Item={
            "user_id": user_id,
            "room_id": room_id,
            "role": "owner",
            "invited_by": None,
            "joined_at": now,
        })

        METRICS["rooms_created"] += 1
        logging.info(f"[ROOM] User {user_id} created room {room_id}")

        return {
            "room_id": room_id,
            "room_token": room_token,
            "name": room_name,
            "otp_secret": otp_secret,
            "otp_uri": otp_uri,
            "expires_at": expires_at,
        }

    except Exception as e:
        logging.error(f"[ROOM] Failed to create room: {e}")
        raise HTTPException(status_code=500, detail="Failed to create room")


@app.delete("/api/auth/rooms/{room_id}")
async def delete_room(room_id: str, authorization: str = Header(...)):
    """Delete a room and all associated data.

    Only the room owner can delete a room. This will:
    1. Delete the room from the rooms table
    2. Delete all membership records for this room
    3. Delete all worker tokens for this room
    """
    claims = get_user_from_auth_header(authorization)
    user_id = claims["sub"]

    try:
        # Verify user is the owner of this room
        membership = room_memberships_table.get_item(
            Key={"user_id": user_id, "room_id": room_id}
        ).get("Item")

        if not membership:
            raise HTTPException(status_code=404, detail="Room not found or you don't have access")

        if membership.get("role") != "owner":
            raise HTTPException(status_code=403, detail="Only the room owner can delete a room")

        # 1. Delete all memberships for this room (query by room_id GSI)
        memberships_response = room_memberships_table.query(
            IndexName="room_id-index",
            KeyConditionExpression="room_id = :rid",
            ExpressionAttributeValues={":rid": room_id}
        )
        for item in memberships_response.get("Items", []):
            room_memberships_table.delete_item(
                Key={"user_id": item["user_id"], "room_id": room_id}
            )

        # 2. Delete all tokens for this room (query by room_id GSI)
        tokens_response = worker_tokens_table.query(
            IndexName="room_id-index",
            KeyConditionExpression="room_id = :rid",
            ExpressionAttributeValues={":rid": room_id}
        )
        for item in tokens_response.get("Items", []):
            worker_tokens_table.delete_item(
                Key={"token_id": item["token_id"]}
            )

        # 3. Delete the room itself
        rooms_table.delete_item(Key={"room_id": room_id})

        logging.info(f"[ROOM] User {user_id} deleted room {room_id}")
        return {"status": "deleted", "room_id": room_id}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[ROOM] Failed to delete room: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete room")


# =============================================================================
# Legacy Endpoints (to be deprecated)
# =============================================================================
@app.post("/delete-peer")
async def delete_peer(json_data: dict):
    """Deletes a peer from its room without deleting the room itself."""
    # ROOMS[room_id]["peers"]: { Worker-3108: <Worker websocket object>, Client-1489: <Client websocket object> }
    # PEER_TO_ROOM: { Worker-3108: room-7462, Client-1489: room-7462 }

    peer_id = json_data.get("peer_id")

    # Always try to delete from Cognito first (peer_id IS the Cognito username)
    # This ensures cleanup happens even if WebSocket disconnect already removed
    # the peer from in-memory mappings
    try:
        logging.info(f"[DELETE] Deleting Cognito user: {peer_id} from pool: {COGNITO_USER_POOL_ID}")
        cognito_client.admin_delete_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=peer_id
        )
        logging.info(f"[DELETE] Successfully deleted Cognito user {peer_id}")
    except cognito_client.exceptions.UserNotFoundException:
        logging.info(f"[DELETE] Cognito user {peer_id} already deleted (not found)")
    except Exception as e:
        logging.error(f"[DELETE] Failed to delete Cognito user {peer_id}: {e}")
        logging.exception("Full traceback:")

    # Now clean up in-memory mappings if they still exist
    room_id = PEER_TO_ROOM.get(peer_id)
    if not room_id:
        logging.debug(f"[DELETE] Peer {peer_id} not in PEER_TO_ROOM (likely already cleaned up by WebSocket disconnect)")
        return {"status": "peer deleted from Cognito (already removed from room)"}

    room = ROOMS.get(room_id)
    if not room:
        logging.warning(f"[DELETE] Room {room_id} not found in ROOMS")
        return {"status": "peer deleted from Cognito (room not found)"}

    # Remove peer from in-memory mappings
    try:
        del PEER_TO_ROOM[peer_id]
        if peer_id in room["peers"]:
            del room["peers"][peer_id]
    except KeyError:
        pass  # Already removed

    # If the room has no more peers, delete from memory and DynamoDB.
    if not room["peers"]:
        del ROOMS[room_id]
        try:
            rooms_table.delete_item(Key={"room_id": room_id})
            logging.info(f"Room {room_id} deleted from DynamoDB as it has no more peers.")
        except Exception as e:
            logging.error(f"Failed to delete room {room_id} from DynamoDB: {e}")

    return {"status": f"peer deleted successfully. Room status: {'deleted' if room_id not in ROOMS else 'active'}"}


@app.post("/delete-peers-and-room")
async def delete_peer_and_room(json_data: dict):
    """Deletes all peers from their room and cleans up if the room is empty.

    Accepts either:
    - room_id directly (preferred to avoid race condition with WebSocket cleanup)
    - peer_id (fallback for backward compatibility)
    """
    # ROOMS[room_id]["peers"]: { Worker-3108: <Worker websocket object>, Client-1489: <Client websocket object> }
    # PEER_TO_ROOM: { Worker-3108: room-7462, Client-1489: room-7462 }

    # Get room_id either directly or via peer_id lookup
    room_id = json_data.get("room_id")
    if not room_id:
        # Fallback to peer_id lookup (backward compatibility)
        peer_id = json_data.get("peer_id")
        room_id = PEER_TO_ROOM.get(peer_id)
        if not room_id:
            logging.warning(f"[DELETE] Peer {peer_id} not found in PEER_TO_ROOM mapping")
            return {"status": "peer not found"}

    room = ROOMS.get(room_id)
    if not room:
        logging.warning(f"[DELETE] Room {room_id} not found in ROOMS")
        return {"status": "room not found"}

    # Delete all Users in the room from Cognito.
    # NOTE: peer_id IS the Cognito username (from /anonymous-signin response)
    peer_ids = list(room["peers"].keys())
    logging.info(f"[DELETE] Attempting to delete {len(peer_ids)} Cognito users from room {room_id}")
    for peer_id in peer_ids:
        try:
            # Delete the Cognito user (peer_id IS the Cognito username)
            logging.info(f"[DELETE] Deleting Cognito user: {peer_id} from pool: {COGNITO_USER_POOL_ID}")
            cognito_client.admin_delete_user(
                UserPoolId=COGNITO_USER_POOL_ID,
                Username=peer_id
            )
            logging.info(f"[DELETE] Successfully deleted Cognito user {peer_id}")

            # Remove peer from PEER_TO_ROOM mapping (if it still exists)
            if peer_id in PEER_TO_ROOM:
                del PEER_TO_ROOM[peer_id]
        except Exception as e:
            logging.error(f"[DELETE] Failed to delete Cognito user {peer_id}: {e}")
            logging.exception("Full traceback:")

    # If the room has no more peers, delete from memory and DynamoDB.
    if not room["peers"]:
        del ROOMS[room_id]
        try:
            rooms_table.delete_item(Key={"room_id": room_id})
            logging.info(f"Room {room_id} deleted from DynamoDB as it has no more peers.")
        except Exception as e:
            logging.error(f"Failed to delete room {room_id} from DynamoDB: {e}")

    return {"status": "peer deleted successfully"}


@app.post("/anonymous-signin")
async def anonymous_signin():
    """Handles anonymous sign-in and returns a Cognito ID token."""
    
    # Create a random username and password.
    username = str(uuid.uuid4())
    password = f"Aa{uuid.uuid4().hex}!"

    try:
        # Sign up the user with the random credentials.
        response = cognito_client.sign_up(
            ClientId=COGNITO_APP_CLIENT_ID,
            Username=username,
            Password=password
        )

        # If sign-up is successful, the user is confirmed automatically.
        cognito_client.admin_confirm_sign_up(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=username
        )

        # Sign in the user to get tokens.
        response = cognito_client.initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password
            }
        )

        return {
            "id_token": response["AuthenticationResult"]["IdToken"],
            "username": username # return username to later identify for deletion
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Anonymous sign-in failed: {e}")


@app.post("/create-room")
async def create_room(authorization: str = Header(...)):
    """Creates a new room and returns the room ID and token."""
    # Extract token from Authorization header
    token = authorization.replace("Bearer ", "")

    # Cognito ID token verification.
    claims = verify_cognito_token(token)
    uid = claims["sub"]

    # Generate a unique room ID and token to be associated with this verified Cognito ID token.
    room_id = str(uuid.uuid4())[:8]
    token = str(uuid.uuid4())[:6]
    expires_at = int((datetime.utcnow() + timedelta(hours=2)).timestamp())  # 2 hours TTL

    # Create a new room item in DynamoDB.
    item = {
        "room_id": room_id,
        "created_by": uid, # user ID from Cognito ID token
        "token": token,
        "expires_at": expires_at  # 2 hours TTL
    }

    # Store the room in DynamoDB.
    rooms_table.put_item(Item=item)

    # Update metrics
    METRICS["rooms_created"] += 1

    return { "room_id": room_id, "token": token }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0"
    }


@app.get("/metrics")
async def get_metrics():
    """Get system metrics (public endpoint for monitoring)."""
    # Calculate real-time metrics
    total_rooms = len(ROOMS)
    total_peers = sum(len(room["peers"]) for room in ROOMS.values())

    # Count peers by role
    peers_by_role = {}
    for room in ROOMS.values():
        for peer_data in room["peers"].values():
            role = peer_data.get("role", "peer")
            peers_by_role[role] = peers_by_role.get(role, 0) + 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "active_rooms": total_rooms,
        "active_connections": total_peers,
        "peers_by_role": peers_by_role,
        "total_connections": METRICS["total_connections"],
        "total_messages": METRICS["total_messages"],
        "rooms_created": METRICS["rooms_created"]
    }


async def handle_register(websocket, message):
    """Handles the registration of a peer in a room w/ its websocket.

    Enhanced to support:
    - Role and metadata for generic peer discovery
    - API key authentication for workers (new auth system)
    - JWT authentication for clients (new auth system)
    - Legacy Cognito authentication (backward compatibility)
    """

    peer_id = message.get('peer_id')  # identify a peer uniquely in the room
    role = message.get('role', 'peer')  # Default to 'peer' for backward compatibility
    metadata = message.get('metadata', {})  # Arbitrary application data
    is_admin = message.get('is_admin', False)  # Admin flag for mesh networking

    # ==========================================================================
    # Authentication: Support multiple auth methods
    # ==========================================================================
    api_key = message.get('api_key')  # NEW: Worker API key (slp_xxx)
    jwt_token = message.get('jwt')  # NEW: Client JWT token
    id_token = message.get('id_token')  # LEGACY: Cognito ID token
    room_id = message.get('room_id')  # Room ID (not needed for API key auth)
    token = message.get('token')  # Room password (not needed for API key auth)

    uid = None
    room_data = None

    # --------------------------------------------------------------------------
    # Path 1: API Key Authentication (Workers - New Auth System)
    # --------------------------------------------------------------------------
    if api_key and api_key.startswith('slp_'):
        logging.info(f"[REGISTER] Attempting API key auth for worker")
        try:
            # Look up token in DynamoDB
            token_response = worker_tokens_table.get_item(Key={"token_id": api_key})
            if "Item" not in token_response:
                await websocket.send(json.dumps({"type": "error", "reason": "Invalid API key"}))
                return

            token_data = token_response["Item"]

            # Check if token is revoked
            if token_data.get("revoked_at"):
                await websocket.send(json.dumps({"type": "error", "reason": "Token revoked"}))
                return

            # Check if token is expired
            if token_data.get("expires_at"):
                expires_at = datetime.fromisoformat(token_data["expires_at"])
                if datetime.utcnow() > expires_at:
                    await websocket.send(json.dumps({"type": "error", "reason": "Token expired"}))
                    return

            # Extract room_id from token
            room_id = token_data["room_id"]
            uid = token_data["user_id"]
            peer_id = peer_id or f"worker-{token_data['worker_name']}-{uuid.uuid4().hex[:4]}"

            # Get room data
            response = rooms_table.get_item(Key={"room_id": room_id})
            room_data = response.get('Item')

            if not room_data:
                await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
                return

            # Store OTP secret in metadata for P2P verification (from room, not token)
            metadata["_otp_secret"] = room_data.get("otp_secret")
            metadata["_worker_name"] = token_data.get("worker_name")

            logging.info(f"[REGISTER] API key auth successful for worker in room {room_id}")

        except Exception as e:
            logging.error(f"[REGISTER] API key auth failed: {e}")
            await websocket.send(json.dumps({"type": "error", "reason": "API key validation failed"}))
            return

    # --------------------------------------------------------------------------
    # Path 2: JWT Authentication (Clients - New Auth System)
    # --------------------------------------------------------------------------
    elif jwt_token:
        logging.info(f"[REGISTER] Attempting JWT auth for client")
        try:
            claims = verify_sleap_jwt(jwt_token)
            uid = claims["sub"]

            # Validate room access
            if not room_id:
                await websocket.send(json.dumps({"type": "error", "reason": "room_id required for JWT auth"}))
                return

            # Check room membership
            membership = room_memberships_table.get_item(
                Key={"user_id": uid, "room_id": room_id}
            )
            if "Item" not in membership:
                await websocket.send(json.dumps({"type": "error", "reason": "No access to this room"}))
                return

            # Get room data
            response = rooms_table.get_item(Key={"room_id": room_id})
            room_data = response.get('Item')

            if not room_data:
                await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
                return

            peer_id = peer_id or f"client-{claims.get('username', 'user')}-{uuid.uuid4().hex[:4]}"

            logging.info(f"[REGISTER] JWT auth successful for {claims.get('username')} in room {room_id}")

        except HTTPException as e:
            logging.error(f"[REGISTER] JWT auth failed: {e.detail}")
            await websocket.send(json.dumps({"type": "error", "reason": str(e.detail)}))
            return
        except Exception as e:
            logging.error(f"[REGISTER] JWT auth failed: {e}")
            await websocket.send(json.dumps({"type": "error", "reason": "JWT validation failed"}))
            return

    # --------------------------------------------------------------------------
    # Path 3: Legacy Cognito Authentication (Backward Compatibility)
    # --------------------------------------------------------------------------
    elif id_token:
        logging.info(f"[REGISTER] Attempting legacy Cognito auth")
        # Validate required fields for legacy auth
        if not all([peer_id, room_id, token, id_token]):
            await websocket.send(json.dumps({"type": "error", "reason": "Missing required fields during registration."}))
            return

        # Verify Cognito ID token
        try:
            claims = verify_cognito_token(id_token)
            uid = claims["sub"]
        except Exception as e:
            logging.error(f"Token verification failed: {e}")
            await websocket.send(json.dumps({"error": "Invalid token"}))
            return

        # Get room data
        response = rooms_table.get_item(Key={"room_id": room_id})
        room_data = response.get('Item')

    # --------------------------------------------------------------------------
    # No valid authentication provided
    # --------------------------------------------------------------------------
    else:
        await websocket.send(json.dumps({
            "type": "error",
            "reason": "No authentication provided. Use api_key, jwt, or id_token."
        }))
        return

    # ==========================================================================
    # Room Validation (common for all auth paths)
    # ==========================================================================
    try:
        if not room_data:
            await websocket.send(json.dumps({"type": "error", "reason": "Room not found"}))
            return

        # For legacy auth, verify room token
        if id_token and not api_key and not jwt_token:
            if token != room_data.get("token"):
                await websocket.send(json.dumps({"type": "error", "reason": "Invalid token"}))
                return

        # Check room expiration
        if time.time() > room_data.get("expires_at", float('inf')):
            await websocket.send(json.dumps({"type": "error", "reason": "Room expired"}))
            return

        # Initialize room in memory if needed
        if room_id not in ROOMS:
            ROOMS[room_id] = {
                "created_by": uid,
                "token": room_data.get("token"),
                "expires_at": room_data.get("expires_at"),
                "peers": {}
            }

        # =======================================================================
        # Peer Registration (common for all auth paths)
        # =======================================================================
        # Store peer data object in memory
        # ROOMS[room_id]["peers"]: {
        #   Worker-3108: {websocket: <ws>, role: "worker", metadata: {...}, connected_at: timestamp}
        # }
        # NOTE: peer_id IS the Cognito username (from /anonymous-signin response)
        ROOMS[room_id]["peers"][peer_id] = {
            "websocket": websocket,
            "role": role,
            "metadata": metadata,
            "connected_at": time.time()
        }
        PEER_TO_ROOM[peer_id] = room_id

        # NEW: Handle admin registration for mesh networking
        if is_admin:
            current_admin = ROOM_ADMINS.get(room_id)
            if current_admin and current_admin != peer_id:
                # CONFLICT: Another admin already exists
                await websocket.send(json.dumps({
                    "type": "admin_conflict",
                    "room_id": room_id,
                    "current_admin": current_admin,
                }))
                logging.info(f"[ADMIN_CONFLICT] {peer_id} tried to be admin, but {current_admin} already is")
            else:
                # First admin or re-registration of same admin
                ROOM_ADMINS[room_id] = peer_id
                logging.info(f"[ADMIN] {peer_id} is now admin of room {room_id}")

        # Update metrics
        METRICS["total_connections"] += 1
        METRICS["active_connections"] = sum(len(room["peers"]) for room in ROOMS.values())

        # Build peer list and metadata for discovery
        peer_list = [pid for pid in ROOMS[room_id]["peers"].keys() if pid != peer_id]
        peer_metadata = {}
        for pid in peer_list:
            peer_metadata[pid] = ROOMS[room_id]["peers"][pid].get("metadata", {})

        # Build response with discovery info
        response = {
            "type": "registered_auth",
            "room_id": room_id,
            "token": token,
            "peer_id": peer_id,
            "admin_peer_id": ROOM_ADMINS.get(room_id),  # Current admin
            "peer_list": peer_list,  # Other peers in room
            "peer_metadata": peer_metadata,  # Metadata of other peers
            "ice_servers": get_ice_servers("client"),  # STUN + TURN for client connections
            "mesh_ice_servers": get_ice_servers("mesh"),  # STUN only for worker-to-worker
        }

        # Include OTP secret for workers (for P2P authentication)
        # Workers need this to validate TOTP codes from clients
        if role == "worker" and metadata.get("_otp_secret"):
            response["otp_secret"] = metadata["_otp_secret"]

        # Send registration confirmation to the peer
        await websocket.send(json.dumps(response))

        logging.info(f"[REGISTERED] peer_id: {peer_id} (role: {role}) in room: {room_id}")

    except Exception as e:
        logging.error(f"Failed to fetch room {room_id}: {e}")
        await websocket.send(json.dumps({"type": "error", "reason": "DynamoDB error"}))
        return


async def forward_message(sender_pid: str, target_pid: str, data):
    """Forward a message from one peer to another.

    Args:
        sender_pid (str): The Peer ID of the sending peer.
        target_pid (str): The Peer ID of the receiving peer.
        data (dict): The data to forward.
    """

    room_id = PEER_TO_ROOM.get(sender_pid)
    if not room_id:
        logging.error(f"Room not found for peer {sender_pid}")
        return

    room = ROOMS.get(room_id)
    if not room:
        logging.error(f"Room {room_id} not found in memory.")
        return

    # Select target peer's data from room's peers dictionary.
    target_peer = room["peers"].get(target_pid)
    if not target_peer:
        logging.error(f"Target peer {target_pid} not found in room {room_id}.")
        return

    # Extract websocket from peer data object
    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        logging.info(f"Forwarding message from {sender_pid} to {target_pid}: {data}")
        await target_websocket.send(json.dumps(data))
        METRICS["total_messages"] += 1
    except Exception as e:
        logging.error(f"Failed to send message from {sender_pid} to {target_pid}. Error: {e}")
    

def matches_filters(peer_data: dict, filters: dict) -> bool:
    """Check if peer matches discovery filters.

    Args:
        peer_data: Peer data dict with role, metadata, etc.
        filters: Filter criteria dict with optional role, tags, properties.

    Returns:
        bool: True if peer matches all filters, False otherwise.
    """
    # Role filter
    if "role" in filters and peer_data.get("role") != filters["role"]:
        return False

    metadata = peer_data.get("metadata", {})

    # Tag filter (match any)
    if "tags" in filters:
        peer_tags = set(metadata.get("tags", []))
        filter_tags = set(filters["tags"])
        if not peer_tags.intersection(filter_tags):
            return False

    # Property filters with operators
    if "properties" in filters:
        peer_props = metadata.get("properties", {})
        for key, value in filters["properties"].items():
            peer_value = peer_props.get(key)

            if isinstance(value, dict):
                # Operator syntax: {"$gte": 8192}
                if "$gte" in value and (peer_value is None or peer_value < value["$gte"]):
                    return False
                if "$lte" in value and (peer_value is None or peer_value > value["$lte"]):
                    return False
                if "$eq" in value and peer_value != value["$eq"]:
                    return False
            elif peer_value != value:
                return False

    return True


async def handle_discover_peers(websocket, message):
    """Handle peer discovery request with filtering.

    Message format:
    {
        "type": "discover_peers",
        "from_peer_id": "Client-1489",
        "filters": {
            "role": "worker",
            "tags": ["gpu", "training"],
            "properties": {
                "gpu_memory_mb": {"$gte": 8192}
            }
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    filters = message.get("filters", {})

    # Get requester's room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Peer not registered in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "ROOM_NOT_FOUND",
            "message": f"Room {room_id} not found"
        }))
        return

    # Find matching peers (exclude self)
    matching_peers = []
    for peer_id, peer_data in room["peers"].items():
        if peer_id == from_peer_id:
            continue  # Don't return self

        if matches_filters(peer_data, filters):
            # Return peer info without websocket object
            matching_peers.append({
                "peer_id": peer_id,
                "role": peer_data.get("role", "peer"),
                "metadata": peer_data.get("metadata", {}),
                "connected_at": peer_data.get("connected_at", 0)
            })

    # Send response
    await websocket.send(json.dumps({
        "type": "peer_list",
        "to_peer_id": from_peer_id,
        "peers": matching_peers,
        "count": len(matching_peers)
    }))

    logging.info(f"[DISCOVER] {from_peer_id} found {len(matching_peers)} matching peers in room {room_id}")


async def handle_update_metadata(websocket, message):
    """Handle metadata update from peer.

    Allows peers to update their metadata in real-time (e.g., status changes).

    Message format:
    {
        "type": "update_metadata",
        "peer_id": "Worker-3108",
        "metadata": {
            "tags": ["sleap-rtc", "training-worker"],
            "properties": {
                "status": "busy",
                ...
            }
        }
    }
    """
    peer_id = message.get("peer_id")
    new_metadata = message.get("metadata")

    if not peer_id or not new_metadata:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: peer_id, metadata"
        }))
        return

    # Verify peer is in a room
    room_id = PEER_TO_ROOM.get(peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Peer not in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room or peer_id not in room["peers"]:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": f"Peer {peer_id} not found in room"
        }))
        return

    # Update metadata (merge with existing)
    peer_data = room["peers"][peer_id]

    # Merge tags (union of old and new)
    if "tags" in new_metadata:
        existing_tags = set(peer_data.get("metadata", {}).get("tags", []))
        new_tags = set(new_metadata.get("tags", []))
        merged_tags = list(existing_tags.union(new_tags))
        new_metadata["tags"] = merged_tags

    # Merge properties (new properties override old)
    if "properties" in new_metadata:
        existing_props = peer_data.get("metadata", {}).get("properties", {})
        new_props = new_metadata.get("properties", {})
        merged_props = {**existing_props, **new_props}
        new_metadata["properties"] = merged_props

    # Update stored metadata
    peer_data["metadata"] = new_metadata

    # Send confirmation
    await websocket.send(json.dumps({
        "type": "metadata_updated",
        "peer_id": peer_id,
        "metadata": new_metadata
    }))

    logging.info(f"[METADATA_UPDATE] {peer_id} updated metadata in room {room_id}")


async def handle_peer_message(websocket, message):
    """Handle generic peer-to-peer message routing.

    Message format:
    {
        "type": "peer_message",
        "from_peer_id": "Client-1489",
        "to_peer_id": "Worker-3108",
        "payload": {
            // Application-specific data (not interpreted by signaling server)
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    to_peer_id = message.get("to_peer_id")
    payload = message.get("payload")

    if not all([from_peer_id, to_peer_id, payload]):
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: from_peer_id, to_peer_id, payload"
        }))
        return

    # Verify sender is in a room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Sender not in any room"
        }))
        return

    # Verify target is in same room
    target_room_id = PEER_TO_ROOM.get(to_peer_id)
    if target_room_id != room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_IN_ROOM",
            "message": f"Target peer {to_peer_id} not in same room"
        }))
        return

    # Forward payload to target peer
    room = ROOMS.get(room_id)
    target_peer = room["peers"].get(to_peer_id)

    if not target_peer:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": f"Target peer {to_peer_id} not found"
        }))
        return

    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        await target_websocket.send(json.dumps({
            "type": "peer_message",
            "from_peer_id": from_peer_id,
            "to_peer_id": to_peer_id,
            "payload": payload  # Pass through without interpretation
        }))
        METRICS["total_messages"] += 1
        logging.info(f"[PEER_MESSAGE] Routed message from {from_peer_id} to {to_peer_id}")
    except Exception as e:
        logging.error(f"Failed to route peer message: {e}")
        await websocket.send(json.dumps({
            "type": "error",
            "code": "DELIVERY_FAILED",
            "message": f"Failed to deliver message to {to_peer_id}"
        }))


async def handle_mesh_connect(websocket, message):
    """Handle mesh connection request (relay offer to target peer).

    Message format:
    {
        "type": "mesh_connect",
        "from_peer_id": "worker-3",
        "target_peer_id": "worker-2",
        "offer": {
            "sdp": "...",
            "type": "offer"
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    target_peer_id = message.get("target_peer_id")
    offer = message.get("offer")

    if not all([from_peer_id, target_peer_id, offer]):
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: from_peer_id, target_peer_id, offer"
        }))
        return

    # Verify sender is in a room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Sender not in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "ROOM_NOT_FOUND",
            "message": f"Room {room_id} not found"
        }))
        return

    # Get target peer's websocket
    target_peer = room["peers"].get(target_peer_id)
    if not target_peer:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "reason": "peer_not_found",
            "target_peer_id": target_peer_id,
        }))
        return

    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        # Relay as mesh_offer to target
        await target_websocket.send(json.dumps({
            "type": "mesh_offer",
            "from_peer_id": from_peer_id,
            "offer": offer,
        }))
        METRICS["total_messages"] += 1
        logging.info(f"[MESH_CONNECT] Relayed offer from {from_peer_id} to {target_peer_id}")
    except Exception as e:
        logging.error(f"Failed to relay mesh_connect: {e}")
        await websocket.send(json.dumps({
            "type": "error",
            "code": "DELIVERY_FAILED",
            "message": f"Failed to deliver mesh offer to {target_peer_id}"
        }))


async def handle_mesh_answer(websocket, message):
    """Handle mesh connection answer (relay answer to original peer).

    Message format:
    {
        "type": "mesh_answer",
        "from_peer_id": "worker-2",
        "target_peer_id": "worker-3",
        "answer": {
            "sdp": "...",
            "type": "answer"
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    target_peer_id = message.get("target_peer_id")
    answer = message.get("answer")

    if not all([from_peer_id, target_peer_id, answer]):
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: from_peer_id, target_peer_id, answer"
        }))
        return

    # Verify sender is in a room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Sender not in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "ROOM_NOT_FOUND",
            "message": f"Room {room_id} not found"
        }))
        return

    # Get target peer's websocket
    target_peer = room["peers"].get(target_peer_id)
    if not target_peer:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": f"Target peer {target_peer_id} not found"
        }))
        return

    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        # Relay answer to original peer
        await target_websocket.send(json.dumps({
            "type": "mesh_answer",
            "from_peer_id": from_peer_id,
            "answer": answer,
        }))
        METRICS["total_messages"] += 1
        logging.info(f"[MESH_ANSWER] Relayed answer from {from_peer_id} to {target_peer_id}")
    except Exception as e:
        logging.error(f"Failed to relay mesh_answer: {e}")
        await websocket.send(json.dumps({
            "type": "error",
            "code": "DELIVERY_FAILED",
            "message": f"Failed to deliver mesh answer to {target_peer_id}"
        }))


async def handle_ice_candidate(websocket, message):
    """Handle ICE candidate relay between peers.

    Message format:
    {
        "type": "ice_candidate",
        "from_peer_id": "worker-3",
        "target_peer_id": "worker-2",
        "candidate": {
            "candidate": "candidate:1 1 UDP...",
            "sdpMLineIndex": 0,
            "sdpMid": "0"
        }
    }
    """
    from_peer_id = message.get("from_peer_id")
    target_peer_id = message.get("target_peer_id")
    candidate = message.get("candidate")

    if not all([from_peer_id, target_peer_id, candidate]):
        await websocket.send(json.dumps({
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing required fields: from_peer_id, target_peer_id, candidate"
        }))
        return

    # Verify sender is in a room
    room_id = PEER_TO_ROOM.get(from_peer_id)
    if not room_id:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "NOT_IN_ROOM",
            "message": "Sender not in any room"
        }))
        return

    room = ROOMS.get(room_id)
    if not room:
        await websocket.send(json.dumps({
            "type": "error",
            "code": "ROOM_NOT_FOUND",
            "message": f"Room {room_id} not found"
        }))
        return

    # Get target peer's websocket
    target_peer = room["peers"].get(target_peer_id)
    if not target_peer:
        # Target might have disconnected - log but don't error
        logging.warning(f"[ICE_CANDIDATE] Target peer {target_peer_id} not found in room {room_id}")
        return

    target_websocket = target_peer.get("websocket") if isinstance(target_peer, dict) else target_peer

    try:
        # Relay ICE candidate to target
        await target_websocket.send(json.dumps({
            "type": "ice_candidate",
            "from_peer_id": from_peer_id,
            "candidate": candidate,
        }))
        METRICS["total_messages"] += 1
        logging.info(f"[ICE_CANDIDATE] Relayed candidate from {from_peer_id} to {target_peer_id}")
    except Exception as e:
        logging.error(f"Failed to relay ice_candidate: {e}")


async def cleanup_peer(peer_id: str):
    """Clean up peer data on disconnect.

    Removes the peer from:
    - PEER_TO_ROOM mapping
    - Room's peers dict
    - ROOM_ADMINS (if this peer was admin)

    Also cleans up empty rooms.

    Args:
        peer_id: The ID of the peer that disconnected
    """
    room_id = PEER_TO_ROOM.get(peer_id)
    if not room_id:
        return

    # Remove from PEER_TO_ROOM
    del PEER_TO_ROOM[peer_id]

    # Remove from room's peers
    room = ROOMS.get(room_id)
    if room and peer_id in room["peers"]:
        del room["peers"][peer_id]
        logging.info(f"[CLEANUP] Removed peer {peer_id} from room {room_id}")

    # If this was admin, clear admin mapping
    if ROOM_ADMINS.get(room_id) == peer_id:
        del ROOM_ADMINS[room_id]
        logging.info(f"[CLEANUP] Room {room_id}: admin {peer_id} disconnected")

    # Clean up empty rooms
    if room and not room["peers"]:
        del ROOMS[room_id]
        if room_id in ROOM_ADMINS:
            del ROOM_ADMINS[room_id]
        logging.info(f"[CLEANUP] Room {room_id} is now empty and removed from memory")

    # Update metrics
    METRICS["active_connections"] = sum(len(r["peers"]) for r in ROOMS.values())


def get_room(room_id: str):
    """Fetches a room document from DynamoDB by room_id.

    Args:
        room_id (str): The ID of the room to fetch.
    Returns:
        dict: The room document data if found, otherwise None.
    """

    # Fetch the room document from DynamoDB.

    response = rooms_table.get_item(Key={"room_id": room_id})
    doc = response.get('Item')

    # Check if the document exists.
    if not doc:
        logging.error(f"Room {room_id} not found in DynamoDB.")
        return None

    # Return the document data as a dictionary.
    return doc


async def handle_client(websocket):
    """Handles incoming messages between peers to facilitate exchange of SDP & ICE candidates.

    Enhanced to support:
    - Peer registration with role and metadata
    - Peer discovery with filtering
    - Generic peer-to-peer message routing
    - WebRTC signaling (offer/answer/candidate)

    Args:
		websocket: A websocket connection object between peer1 (client) and peer2 (worker)
	Returns:
		None
	Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the client
    """

    peer_id = None  # Track for cleanup

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                logging.info(f"Received message type: {msg_type}")

                # Registration (enhanced with role/metadata)
                if msg_type == "register":
                    await handle_register(websocket, data)
                    peer_id = data.get('peer_id')

                # NEW: Peer discovery
                elif msg_type == "discover_peers":
                    await handle_discover_peers(websocket, data)

                # NEW: Metadata updates
                elif msg_type == "update_metadata":
                    await handle_update_metadata(websocket, data)

                # NEW: Generic peer message routing
                elif msg_type == "peer_message":
                    await handle_peer_message(websocket, data)

                # NEW: Mesh networking handlers
                elif msg_type == "mesh_connect":
                    await handle_mesh_connect(websocket, data)

                elif msg_type == "mesh_answer":
                    await handle_mesh_answer(websocket, data)

                elif msg_type == "ice_candidate":
                    await handle_ice_candidate(websocket, data)

                # Existing: WebRTC signaling (offer/answer for backward compatibility)
                elif msg_type in ["offer", "answer"]:
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')

                    if not sender_pid or not target_pid:
                        logging.warning("Missing sender or target peer ID in signaling message.")
                        continue

                    await forward_message(sender_pid, target_pid, data)

                # ICE candidates (if you add support for them)
                elif msg_type == "candidate":
                    sender_pid = data.get('sender')
                    target_pid = data.get('target')

                    if not sender_pid or not target_pid:
                        logging.warning("Missing sender or target peer ID in candidate message.")
                        continue

                    await forward_message(sender_pid, target_pid, data)

                else:
                    logging.warning(f"Unknown message type: {msg_type}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "UNKNOWN_MESSAGE_TYPE",
                        "message": f"Unknown message type: {msg_type}"
                    }))

            except json.JSONDecodeError:
                logging.error("Invalid JSON received")
                await websocket.send(json.dumps({
                    "type": "error",
                    "code": "INVALID_JSON",
                    "message": "Invalid JSON format"
                }))

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"Client {peer_id} disconnected cleanly.")

    except Exception as e:
        logging.error(f"Error handling client {peer_id}: {e}")

    finally:
        # Clean up peer on disconnect
        if peer_id:
            await cleanup_peer(peer_id)


def run_fastapi_server():
    """Runs the FastAPI server for room creation."""

    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
    

async def main():
    """Main function to run server indefinitely.
    
    Creates a websocket server to handle incoming connections and passes them to handle_client.
    
    Args:
		None
	Returns:
		None
    """

    async with websockets.serve(handler=handle_client, host="0.0.0.0", port=8080): # use 0.0.0.0 to allow external connections from anywhere (as the signaling server)
        # run server indefinitely
        logging.info("Server started!")
        await asyncio.Future()

if __name__ == "__main__":
    # Start FastAPI server in a separate thread.
    threading.Thread(target=run_fastapi_server, daemon=True).start() 
    asyncio.run(main())