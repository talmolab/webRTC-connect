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

# Get instance metadata for TURN server
PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)

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
  -e TURN_HOST=$PUBLIC_IP \
  -e TURN_PORT=${turn_port} \
  -e TURN_USERNAME=${turn_username} \
  -e TURN_PASSWORD=${turn_password} \
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
    PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)
    docker run -d \
      --name sleap-rtc-signaling \
      --restart unless-stopped \
      -p ${websocket_port}:${websocket_port} \
      -p ${http_port}:${http_port} \
      -e COGNITO_REGION=${cognito_region} \
      -e COGNITO_USER_POOL_ID=${cognito_user_pool_id} \
      -e COGNITO_APP_CLIENT_ID=${cognito_client_id} \
      -e TURN_HOST=$PUBLIC_IP \
      -e TURN_PORT=${turn_port} \
      -e TURN_USERNAME=${turn_username} \
      -e TURN_PASSWORD=${turn_password} \
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

# ============================================
# TURN Server (coturn) Setup
# ============================================
%{ if enable_turn && turn_password != "" }
echo "Setting up TURN server..." >> /var/log/user-data-complete.log

# Install coturn
apt-get install -y coturn

# Enable coturn service
sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn

# Configure coturn
cat > /etc/turnserver.conf <<TURNEOF
# Network configuration
listening-port=${turn_port}
listening-ip=0.0.0.0
external-ip=$PUBLIC_IP/$PRIVATE_IP
relay-ip=$PRIVATE_IP

# Relay port range
min-port=49152
max-port=65535

# Authentication
realm=sleap-rtc
server-name=sleap-rtc-turn
fingerprint
lt-cred-mech

# Credentials
user=${turn_username}:${turn_password}

# Logging
log-file=/var/log/turnserver.log
verbose

# Security
no-multicast-peers
TURNEOF

# Create log file with proper permissions
touch /var/log/turnserver.log
chown turnserver:turnserver /var/log/turnserver.log

# Start and enable coturn
systemctl restart coturn
systemctl enable coturn

echo "TURN server setup complete" >> /var/log/user-data-complete.log
%{ endif }

echo "All setup complete" >> /var/log/user-data-complete.log
