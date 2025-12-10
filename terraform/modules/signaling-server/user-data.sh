#!/bin/bash
set -e

# Update and install Docker
apt-get update
apt-get install -y docker.io docker-compose awscli curl

# Start Docker service
systemctl start docker
systemctl enable docker

# Configure Docker daemon for CloudWatch logs
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "${cognito_region}",
    "awslogs-group": "/aws/ec2/sleap-rtc-signaling-${environment}",
    "awslogs-create-group": "true"
  }
}
EOF

# Restart Docker to apply configuration
systemctl restart docker

# Pull signaling server Docker image
docker pull ${docker_image}

# Run signaling server container
docker run -d \
  --name sleap-rtc-signaling \
  --restart unless-stopped \
  -p ${websocket_port}:${websocket_port} \
  -p ${http_port}:${http_port} \
  -e COGNITO_REGION=${cognito_region} \
  -e COGNITO_USER_POOL_ID=${cognito_user_pool_id} \
  -e COGNITO_APP_CLIENT_ID=${cognito_client_id} \
  ${docker_image}

# Create health check script
cat > /usr/local/bin/healthcheck.sh <<'HEALTH'
#!/bin/bash
# Check if container is running
if ! docker ps | grep -q sleap-rtc-signaling; then
  echo "$(date): Container not running, attempting restart..." >> /var/log/healthcheck.log
  docker start sleap-rtc-signaling || {
    echo "$(date): Failed to start container, recreating..." >> /var/log/healthcheck.log
    docker rm sleap-rtc-signaling 2>/dev/null || true
    docker run -d \
      --name sleap-rtc-signaling \
      --restart unless-stopped \
      -p ${websocket_port}:${websocket_port} \
      -p ${http_port}:${http_port} \
      -e COGNITO_REGION=${cognito_region} \
      -e COGNITO_USER_POOL_ID=${cognito_user_pool_id} \
      -e COGNITO_APP_CLIENT_ID=${cognito_client_id} \
      ${docker_image}
  }
fi

# Check if container is responding (if health endpoint exists)
if ! curl -f http://localhost:${http_port}/health 2>/dev/null; then
  echo "$(date): Container not responding, restarting..." >> /var/log/healthcheck.log
  docker restart sleap-rtc-signaling
fi
HEALTH

chmod +x /usr/local/bin/healthcheck.sh

# Add health check to crontab (run every 5 minutes)
(crontab -l 2>/dev/null || true; echo "*/5 * * * * /usr/local/bin/healthcheck.sh") | crontab -

echo "Signaling server setup complete" >> /var/log/user-data-complete.log
