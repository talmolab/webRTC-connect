# Use Python base image and install uv
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy project files needed by uv
COPY pyproject.toml /app/

# Copy all Python source files (avoids missing module issues)
COPY *.py /app/

# Install dependencies
RUN uv sync

# Port exposed by the container, NOT computer (localhost)
# 8080 = websocket server, 8001 = HTTP API, 8081 = relay server, 3478 = TURN/STUN
EXPOSE 8080 5000 3478/udp 3478/tcp 8001 8081

# Copy entrypoint script
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# Run both signaling server and relay server
ENTRYPOINT ["/app/entrypoint.sh"]