# Base image with SLEAP and GPU support
# https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general
# FROM eberrigan/sleap-cuda:latest

# Use a lightweight multi-architecture base image 
FROM python:slim

# Install dependencies
# opencv requires opengl https://github.com/conda-forge/opencv-feedstock/issues/401
# Default python3 is 3.8 in ubuntu 20.04 https://wiki.ubuntu.com/FocalFossa/ReleaseNotes#Python3_by_default
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     apt-get clean && \
#     rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* 

# Goal: Connect to Remote Signaling Server w/ aiortc and websockets
  
# Install websocket dependencies onto image @ build time (asyncio is included in python3.8)
# put into requirement.txt later
RUN pip install aiortc asyncio websockets

# Copy the sleap_webRTC directory into the image's root dir
COPY worker.py /app/worker.py

# Ensure executable
RUN chmod +x /app/worker.py

# Port exposed by the container, NOT computer (localhost)
EXPOSE 8080 5000 3478/udp 3478/tcp

# Signaling server -> worker -> client in sequenece
# Worker makes req to signaling server, signaling server sends offer to client and accepts offer
# Connection is established between client and worker
ENTRYPOINT ["sh", "-c", "python3 /app/worker.py"]




