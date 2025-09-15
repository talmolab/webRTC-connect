# FROM python:3.9-slim-trixie
# Install image w/ uv pre-installed
# FROM ghcr.io/astral-sh/uv:python3.9-trixie-slim
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy the just server.py into the image's root dir
COPY server.py /app/server.py 

# Ensure executable
RUN chmod +x /app/server.py

# Port exposed by the container, NOT computer (localhost), 8080 = websocket server, 3478 = TURN server
EXPOSE 8080 5000 3478/udp 3478/tcp 8001

# Run the server.py on container startup
ENTRYPOINT ["uv run", "python3 /app/server.py"]