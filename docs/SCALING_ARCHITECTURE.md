# Scaling Architecture for Signaling Server

## Overview

This document outlines the scaling strategy for the signaling server from single-instance (dev) to distributed production deployment.

---

## Stage 2: Redis-Backed Architecture

### Architecture Diagram

```
                                    ┌─────────────┐
                                    │   Route53   │
                                    │   (DNS)     │
                                    └──────┬──────┘
                                           │
                                    ┌──────▼──────┐
                                    │ Application │
                                    │Load Balancer│
                                    │    (ALB)    │
                                    └──────┬──────┘
                                           │
                        ┌──────────────────┼──────────────────┐
                        │                  │                  │
                  ┌─────▼─────┐     ┌─────▼─────┐     ┌─────▼─────┐
                  │ Signaling │     │ Signaling │     │ Signaling │
                  │ Server 1  │     │ Server 2  │     │ Server 3  │
                  │ (EC2 Auto │     │ (EC2 Auto │     │ (EC2 Auto │
                  │  Scaling) │     │  Scaling) │     │  Scaling) │
                  └─────┬─────┘     └─────┬─────┘     └─────┬─────┘
                        │                  │                  │
                        └──────────────────┼──────────────────┘
                                           │
                                    ┌──────▼──────┐
                                    │    Redis    │
                                    │  (ElastiCache)│
                                    │  Pub/Sub +   │
                                    │   Storage    │
                                    └─────────────┘
```

### Implementation

#### File: `app/redis_state.py`

```python
import redis.asyncio as redis
import json
from typing import Dict, List, Optional
import asyncio

class RedisStateManager:
    """Distributed state management using Redis"""

    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)
        self.pubsub = self.redis.pubsub()
        self.server_id = str(uuid.uuid4())  # Unique ID for this server instance

    # Room management
    async def add_peer_to_room(self, room_id: str, peer_id: str, peer_data: dict):
        """Add peer to room (stored in Redis)"""
        # Store peer metadata in Redis hash
        await self.redis.hset(
            f"room:{room_id}:peers",
            peer_id,
            json.dumps({
                "role": peer_data["role"],
                "metadata": peer_data["metadata"],
                "connected_at": peer_data["connected_at"],
                "server_id": self.server_id  # Which server holds the WebSocket
            })
        )

        # Set room expiration (2 hours)
        await self.redis.expire(f"room:{room_id}:peers", 7200)

        # Store peer-to-room mapping
        await self.redis.set(f"peer:{peer_id}:room", room_id, ex=7200)

    async def get_room_peers(self, room_id: str) -> Dict[str, dict]:
        """Get all peers in a room"""
        peers_raw = await self.redis.hgetall(f"room:{room_id}:peers")
        return {
            peer_id.decode(): json.loads(data.decode())
            for peer_id, data in peers_raw.items()
        }

    async def remove_peer(self, peer_id: str):
        """Remove peer from room"""
        room_id = await self.redis.get(f"peer:{peer_id}:room")
        if room_id:
            room_id = room_id.decode()
            await self.redis.hdel(f"room:{room_id}:peers", peer_id)
            await self.redis.delete(f"peer:{peer_id}:room")

    async def discover_peers(self, room_id: str, filters: dict) -> List[dict]:
        """Find peers matching filters"""
        all_peers = await self.get_room_peers(room_id)

        matching = []
        for peer_id, peer_data in all_peers.items():
            if self._matches_filters(peer_data, filters):
                matching.append({
                    "peer_id": peer_id,
                    **peer_data
                })

        return matching

    # Message routing with Redis Pub/Sub
    async def route_message(self, to_peer_id: str, message: dict):
        """Route message to peer (may be on different server)"""
        # Get which server has this peer's WebSocket
        peer_info = await self.redis.get(f"peer:{to_peer_id}:room")
        if not peer_info:
            raise PeerNotFoundError(f"Peer {to_peer_id} not found")

        room_id = peer_info.decode()
        peers = await self.get_room_peers(room_id)
        target_peer = peers.get(to_peer_id)

        if not target_peer:
            raise PeerNotFoundError(f"Peer {to_peer_id} not in room")

        # Publish message to the server that has the WebSocket
        target_server = target_peer["server_id"]
        await self.redis.publish(
            f"server:{target_server}:messages",
            json.dumps({
                "to_peer_id": to_peer_id,
                "message": message
            })
        )

    async def subscribe_to_messages(self, callback):
        """Subscribe to messages for this server instance"""
        await self.pubsub.subscribe(f"server:{self.server_id}:messages")

        async for message in self.pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                await callback(data["to_peer_id"], data["message"])

    def _matches_filters(self, peer_data: dict, filters: dict) -> bool:
        """Filter matching logic (same as before)"""
        if "role" in filters and peer_data["role"] != filters["role"]:
            return False

        if "tags" in filters:
            peer_tags = set(peer_data["metadata"].get("tags", []))
            filter_tags = set(filters["tags"])
            if not peer_tags.intersection(filter_tags):
                return False

        if "properties" in filters:
            for key, value in filters["properties"].items():
                peer_value = peer_data["metadata"].get("properties", {}).get(key)
                if isinstance(value, dict):
                    if "$gte" in value and peer_value < value["$gte"]:
                        return False
                    if "$lte" in value and peer_value > value["$lte"]:
                        return False
                elif peer_value != value:
                    return False

        return True
```

#### File: `app/websocket.py` (Updated)

```python
from app.redis_state import RedisStateManager
from fastapi import WebSocket, WebSocketDisconnect
import os

# Initialize Redis connection
redis_state = RedisStateManager(os.getenv("REDIS_URL", "redis://localhost:6379"))

# Local WebSocket storage (only stores connections for THIS server)
LOCAL_WEBSOCKETS: Dict[str, WebSocket] = {}

async def handle_register(websocket: WebSocket, message: dict):
    """Register peer with Redis-backed state"""
    peer_id = message["peer_id"]
    room_id = message["room_id"]

    # Store WebSocket connection locally (this server only)
    LOCAL_WEBSOCKETS[peer_id] = websocket

    # Store peer metadata in Redis (shared across all servers)
    await redis_state.add_peer_to_room(room_id, peer_id, {
        "role": message.get("role", "peer"),
        "metadata": message.get("metadata", {}),
        "connected_at": time.time()
    })

    await websocket.send_json({
        "type": "registered",
        "peer_id": peer_id,
        "room_id": room_id
    })

async def handle_discover_peers(websocket: WebSocket, message: dict):
    """Discover peers using Redis state"""
    room_id = await redis_state.redis.get(f"peer:{message['from_peer_id']}:room")
    if not room_id:
        return

    peers = await redis_state.discover_peers(room_id.decode(), message.get("filters", {}))

    await websocket.send_json({
        "type": "peer_list",
        "peers": peers
    })

async def handle_peer_message(websocket: WebSocket, message: dict):
    """Route message to target peer (may be on different server)"""
    try:
        # Use Redis pub/sub for cross-server routing
        await redis_state.route_message(
            message["to_peer_id"],
            {
                "type": "peer_message",
                "from_peer_id": message["from_peer_id"],
                "payload": message["payload"]
            }
        )
    except PeerNotFoundError as e:
        await websocket.send_json({
            "type": "error",
            "code": "PEER_NOT_FOUND",
            "message": str(e)
        })

async def message_delivery_worker():
    """Background worker to deliver messages from Redis to local WebSockets"""
    async def deliver_message(peer_id: str, message: dict):
        ws = LOCAL_WEBSOCKETS.get(peer_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to deliver message to {peer_id}: {e}")
                # Clean up dead connection
                await handle_disconnect(peer_id)

    await redis_state.subscribe_to_messages(deliver_message)

# Start background worker on app startup
@app.on_event("startup")
async def startup():
    asyncio.create_task(message_delivery_worker())
```

### Deployment Changes

#### Terraform Updates

```hcl
# Add Redis (ElastiCache)
resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "sleap-rtc-redis-${var.environment}"
  engine               = "redis"
  node_type            = "cache.t3.micro"  # Start small
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379

  tags = {
    Name        = "sleap-rtc-redis-${var.environment}"
    Environment = var.environment
  }
}

# Update EC2 instance to use Auto Scaling Group
resource "aws_launch_template" "signaling" {
  name_prefix   = "sleap-rtc-signaling-${var.environment}"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.signaling.name
  }

  vpc_security_group_ids = [aws_security_group.signaling.id]

  user_data = base64encode(templatefile("${path.module}/user-data.sh", {
    docker_image          = var.docker_image
    redis_url            = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379"
    cognito_region        = var.cognito_region
    cognito_user_pool_id  = var.cognito_user_pool_id
    cognito_client_id     = var.cognito_app_client_id
    websocket_port        = var.websocket_port
    http_port             = var.http_port
    environment           = var.environment
  }))
}

resource "aws_autoscaling_group" "signaling" {
  name                = "sleap-rtc-signaling-${var.environment}"
  vpc_zone_identifier = var.subnet_ids
  target_group_arns   = [aws_lb_target_group.signaling.arn]
  health_check_type   = "ELB"

  min_size         = 1
  max_size         = 5
  desired_capacity = 2  # Start with 2 instances

  launch_template {
    id      = aws_launch_template.signaling.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "sleap-rtc-signaling-${var.environment}"
    propagate_at_launch = true
  }
}

# Add Application Load Balancer
resource "aws_lb" "signaling" {
  name               = "sleap-rtc-alb-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids

  enable_deletion_protection = false
}

resource "aws_lb_target_group" "signaling" {
  name     = "sleap-rtc-tg-${var.environment}"
  port     = var.http_port
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
  }

  # Enable sticky sessions for WebSocket connections
  stickiness {
    type            = "lb_cookie"
    cookie_duration = 7200  # 2 hours
    enabled         = true
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.signaling.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.signaling.arn
  }
}

resource "aws_lb_listener" "websocket" {
  load_balancer_arn = aws_lb.signaling.arn
  port              = var.websocket_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.signaling.arn
  }
}
```

### Scaling Characteristics

| Metric | Single Instance | Redis + ASG |
|--------|----------------|-------------|
| Max Connections | ~1,000 | ~10,000 |
| Fault Tolerance | None (single point of failure) | High (multiple instances) |
| Downtime for Deploy | Yes (~30s) | No (rolling updates) |
| Cost (Dev) | $15/month | $45/month |
| Cost (Prod) | N/A | $150/month |

---

## Stage 3: Enterprise Scale (100,000+ connections)

For massive scale, consider:

### Option A: Managed WebSocket Service
- **AWS API Gateway WebSocket API** - Fully managed, auto-scaling
- **Pusher** / **Ably** - Third-party managed services
- No infrastructure management required

### Option B: Message Queue Architecture
```
Client → API Gateway → SQS → Lambda → Redis → WebSocket Servers
```

### Option C: Kubernetes + Redis Cluster
- Horizontal pod autoscaling
- Redis Cluster mode for distributed state
- Service mesh for load balancing

---

## Monitoring & Metrics

### Key Metrics to Track

```python
# app/metrics.py
from prometheus_client import Counter, Gauge, Histogram

# Connection metrics
active_connections = Gauge('websocket_connections_active', 'Number of active WebSocket connections')
total_connections = Counter('websocket_connections_total', 'Total WebSocket connections')
failed_connections = Counter('websocket_connections_failed', 'Failed WebSocket connections')

# Room metrics
active_rooms = Gauge('rooms_active', 'Number of active rooms')
peers_per_room = Histogram('peers_per_room', 'Distribution of peers per room')

# Message metrics
messages_sent = Counter('messages_sent_total', 'Total messages sent')
messages_failed = Counter('messages_failed_total', 'Failed message deliveries')
message_latency = Histogram('message_latency_seconds', 'Message delivery latency')

# Redis metrics
redis_operations = Counter('redis_operations_total', 'Total Redis operations', ['operation'])
redis_failures = Counter('redis_failures_total', 'Failed Redis operations')
```

### CloudWatch Dashboard

Monitor:
- WebSocket connections per instance
- Redis connection pool utilization
- Message throughput (messages/second)
- ALB target health
- Auto Scaling Group size

---

## Recommendations by Use Case

### Small Lab (5-10 users)
- **Stage 1**: Single EC2 instance
- **Cost**: ~$15/month
- **Effort**: Current setup works fine

### Research Team (50-100 users)
- **Stage 2**: Redis + Auto Scaling (2-3 instances)
- **Cost**: ~$50/month
- **Effort**: Moderate (1-2 days implementation)

### Enterprise (1000+ users)
- **Stage 3**: Managed service or Kubernetes
- **Cost**: $500+/month
- **Effort**: High (1-2 weeks)
