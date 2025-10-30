# Project Context

## Purpose

**webRTC-connect** (also known as sleap-webRTC) is a WebRTC-based remote training infrastructure for SLEAP (Social LEAP Estimates Animal Poses), a deep learning framework for animal pose estimation. The project enables:

- Remote training of machine learning models on GPU-equipped servers
- Real-time peer-to-peer communication between clients and worker nodes
- Secure file transfer for training data and trained models
- Live training progress monitoring via WebRTC data channels
- Session-based access control using AWS Cognito temporary authentication

The system uses a client-server-worker architecture where the signaling server facilitates WebRTC connection establishment, but actual data transfer happens peer-to-peer between clients and GPU workers.

## Tech Stack

### Languages
- **Python 3.9+** (Primary language, ≥3.11 for external server, 3.12 preferred)

### Core Frameworks & Libraries
- **aiortc** (1.0.0+): Python WebRTC implementation for P2P connections
- **websockets** (11.0.0 - 14.1): WebSocket protocol for signaling
- **FastAPI** (0.115.6): REST API framework
- **uvicorn** (0.34.0): ASGI server
- **asyncio**: Asynchronous I/O foundation

### AWS Services
- **boto3** (1.35.86): AWS SDK
  - **AWS Cognito**: Anonymous user authentication with JWT tokens
  - **AWS DynamoDB**: Room persistence and metadata storage
- **python-jose[cryptography]** (3.3.0): JWT token verification

### Machine Learning (SLEAP Worker)
- **SLEAP**: Animal pose estimation framework (GPU-accelerated)
- **pyzmq** (25.0.0+): ZeroMQ for training progress communication

### Infrastructure
- **Docker**: All components containerized
- **GitHub Container Registry (ghcr.io)**: Image hosting
- **GitHub Actions**: CI/CD for multi-platform builds (linux/amd64, linux/arm64)
- **uv**: Modern Python package installer
- **pyproject.toml**: PEP 621 project metadata

## Project Conventions

### Code Style
- **File naming**: snake_case for Python files (`worker_class.py`, `run_training.py`)
- **Variable naming**: snake_case for variables and functions
- **Class naming**: PascalCase (`RTCWorkerClient`)
- **Formatting**: black and ruff for linting (dev dependencies)
- **Type hints**: Modern Python typing annotations encouraged
- **Docstrings**: Google-style docstrings for functions

Example:
```python
def request_create_room(self, id_token: str):
    """Requests the signaling server to create a room.

    Args:
        id_token (str): Firebase ID token for authentication.
    Returns:
        dict: Contains room_id and token if successful.
    """
```

### Architecture Patterns

#### Event-Driven Async Architecture
All networking code uses Python's asyncio with decorator-based event handlers:

```python
@pc.on("datachannel")
def on_datachannel(channel):
    # Handle incoming data channel

@channel.on("message")
async def on_message(message):
    # Process messages
```

#### Message Protocol Pattern
Messages use double-colon prefixes for type identification:
- `FILE_META::filename:size:directory`
- `PROGRESS_REPORT::json_data`
- `TRAIN_LOG::log_message`
- `ZMQ_CTRL::command`
- `TRAIN_JOB_START::job_name`
- `TRAIN_JOB_END::job_name`

#### Chunked File Transfer
Large files sent in 32-64KB chunks with flow control:
```python
CHUNK_SIZE = 32 * 1024
while chunk := file.read(CHUNK_SIZE):
    while channel.bufferedAmount > 16 * 1024 * 1024:  # Wait if buffer >16MB
        await asyncio.sleep(0.1)
    channel.send(chunk)
channel.send("END_OF_FILE")
```

#### Authentication Flow
1. Client/Worker → POST `/anonymous-signin` → Server creates temp AWS Cognito user
2. Server returns JWT ID token
3. Worker → POST `/create-room` (with ID token) → Server creates DynamoDB room
4. Server returns room_id + token
5. Client/Worker → WebSocket register (with room_id, token, ID token)
6. Server verifies credentials → Connection established

#### Connection Resilience
90-second reconnection timeout with ICE state monitoring for failed connections.

### Testing Strategy

**Current Status**: Limited formal testing infrastructure

- **Framework**: pytest (listed in dev dependencies)
- **Test Data**: Available in `webRTC_external/test_files/` (SLEAP training packages, configs)
- **Testing Approach**: Primarily manual end-to-end testing
- **CI/CD**: GitHub Actions workflows for build verification and multi-platform Docker builds
- **ZMQ Utility**: `zmq_check.py` for testing training progress communication

**Manual Testing Workflow**:
1. Start signaling server: `python3 webRTC_external/server.py`
2. Start worker in container: `docker run -it [image] bash`
3. Start client: `python3 webRTC_external/client.py`

### Git Workflow

- **Main Branch**: `main`
- **Branch Naming**: `<username>/<feature-description>` (e.g., `amick/delete-room-and-user`)
- **CI/CD Workflows**:
  - Test workflows for PR validation (`webrtc_*_test.yml`)
  - Production workflows for main branch (`webrtc_*_production.yml`)
  - Automatic Docker image tagging with git SHA and platform

#### Development Workflow Rules

**CRITICAL**: Follow these rules strictly:

1. **NEVER commit directly to main** - Always work on a feature branch
2. **NEVER merge without PR** - All changes must go through pull requests for review
3. **ALWAYS squash merge** - Keep main branch history clean with single commits per feature
4. **Use gh CLI for all GitHub operations** - Consistent tooling for issues, PRs, and checks
5. **Stage files carefully** - Review all changes before committing (use `git diff --staged`)
6. **Update .gitignore with local dev files** - Keep local development workflow files out of the repo

**Typical Workflow**:
```bash
# 1. Create feature branch
git checkout -b username/feature-name

# 2. Make changes and review carefully
git status
git diff

# 3. Stage specific files (NOT git add .)
git add path/to/specific/file.py

# 4. Review staged changes
git diff --staged

# 5. Commit with descriptive message
git commit -m "feat: description of changes"

# 6. Push branch
git push -u origin username/feature-name

# 7. Create PR using gh CLI
gh pr create --title "Title" --body "Description"

# 8. After approval, squash merge via GitHub UI or:
gh pr merge --squash --delete-branch
```

## Domain Context

### WebRTC Signaling
- **SDP (Session Description Protocol)**: Negotiates connection parameters via offer/answer exchange
- **ICE (Interactive Connectivity Establishment)**: Finds best path through NATs/firewalls
- **Data Channels**: Reliable/ordered channels for file transfer and messaging (not media streams)
- **Connection States**: `new` → `connecting` → `connected` → `completed` (or `failed`/`disconnected`)

### SLEAP Training
- **Training Package**: `.slp` files containing labeled video frames
- **Configuration**: `initial_config.json` with model architecture, training parameters
- **Multi-Job Training**: Supports multiple sequential training runs with different configs
- **Progress Monitoring**: Real-time metrics via ZMQ PUB/SUB pattern
- **Training Control**: Stop/cancel commands sent via ZMQ control socket

### Room-Based Model
Similar to video conferencing:
- Worker creates a "room" with a unique ID and token
- Clients join the room using room ID + token
- Multiple clients can connect to the same worker
- Rooms expire after 2 hours (DynamoDB TTL)

## Important Constraints

### Technical Constraints
- **Python Version**: ≥3.9 for workers, ≥3.11 for server (asyncio features)
- **GPU Requirement**: SLEAP worker requires NVIDIA GPU with CUDA support
- **Network Access**: Requires internet connectivity for AWS services
- **Port Requirements**:
  - 8080 (WebSocket signaling)
  - 8001 (REST API)
  - 9000-9001 (ZMQ training communication)
  - 3478 (TURN server)
- **File Size**: Large training datasets transferred in chunks (flow control at 16MB buffer)

### AWS Constraints
- **Cognito**: Temporary users auto-confirmed, no email verification
- **DynamoDB**: Single table design, 2-hour TTL on rooms
- **IAM Permissions**: Requires Cognito user pool access, DynamoDB read/write

### Container Constraints
- **Base Image**: SLEAP worker must use CUDA-enabled base image
- **Platform Support**: Server/basic worker support multi-arch (amd64/arm64), SLEAP worker is amd64 only
- **Environment Variables**: Required for AWS service configuration

### Operational Constraints
- **Session Management**: Rooms automatically expire, no persistent user accounts
- **Single Signaling Server**: Centralized signaling (potential SPOF)
- **P2P Dependency**: Data transfer fails if P2P connection cannot be established

## External Dependencies

### AWS Cloud Services
- **AWS Cognito**: Anonymous authentication, JWT token generation/verification
- **AWS DynamoDB**: Room metadata persistence (table: `rooms`)
- **AWS EC2**: Signaling server hosting

### Communication Infrastructure
- **ZeroMQ (ZMQ)**: Training progress monitoring
  - Port 9000: Control socket (PUB) - receive training commands
  - Port 9001: Progress socket (SUB) - broadcast training metrics
- **WebSocket Server**: Custom signaling protocol on port 8080
- **REST API**: FastAPI endpoints on port 8001

### Machine Learning
- **SLEAP Framework**: https://github.com/talmolab/sleap
  - GPU-accelerated pose estimation
  - CLI training interface with ZMQ support
  - `.slp` package format

### Container Registries
- **GitHub Container Registry (ghcr.io)**:
  - `ghcr.io/talmolab/webrtc-server`
  - `ghcr.io/talmolab/webrtc-worker`
  - `ghcr.io/talmolab/webrtc-sleap-worker`
- **Docker Hub**: Python base images

### Development Tools
- **GitHub Actions**: CI/CD automation
- **black**: Code formatting
- **ruff**: Python linting
- **pytest**: Testing framework
