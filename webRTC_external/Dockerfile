FROM python:3.9-slim

# Copy the requirements file into the image
COPY requirements.txt /app/requirements.txt

# Install the requirements
RUN pip install -r /app/requirements.txt

# Copy the just server.py into the image's root dir
COPY server.py /app/server.py 

# Ensure executable
RUN chmod +x /app/server.py

# Port exposed by the container, NOT computer (localhost), 8080 = websocket server, 3478 = TURN server
EXPOSE 8080 5000 3478/udp 3478/tcp 8001

# Run the server.py on container startup
ENTRYPOINT ["sh", "-c", "python3 /app/server.py"]