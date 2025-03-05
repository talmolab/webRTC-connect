FROM python:3.9-slim

RUN pip install aiortc asyncio websockets

# Copy the just server.py into the image's root dir
COPY server.py /app/server.py 

# Ensure executable
RUN chmod +x /app/server.py

# Port exposed by the container, NOT computer (localhost), 8080 = websocket server, 3478 = TURN server
EXPOSE 8080 5000 3478/udp 3478/tcp

# Run the server.py on container startup
ENTRYPOINT ["sh", "-c", "python3 /app/server.py"]