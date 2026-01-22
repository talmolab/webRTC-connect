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
  -e GITHUB_CLIENT_ID=${github_client_id} \
  -e GITHUB_CLIENT_SECRET=${github_client_secret} \
  -e SLEAP_JWT_PRIVATE_KEY='${jwt_private_key}' \
  -e SLEAP_JWT_PUBLIC_KEY='${jwt_public_key}' \
  ${docker_image}

# =============================================================================
# HTTPS Setup via Caddy + Let's Encrypt (if enabled)
# =============================================================================
%{ if enable_https }
echo "Setting up HTTPS with Caddy..."

# Update DuckDNS with current public IP
DUCKDNS_DOMAIN="${duckdns_subdomain}"
DUCKDNS_TOKEN="${duckdns_token}"
curl -s "https://www.duckdns.org/update?domains=$DUCKDNS_DOMAIN&token=$DUCKDNS_TOKEN&ip=$PUBLIC_IP"

# Install Caddy
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy

# Create Caddyfile for reverse proxy
cat > /etc/caddy/Caddyfile << 'CADDYEOF'
${duckdns_subdomain}.duckdns.org {
    # TLS configuration
    tls ${admin_email}

    # HTTP API endpoints
    handle /api/* {
        reverse_proxy localhost:${http_port}
    }

    handle /health {
        reverse_proxy localhost:${http_port}
    }

    handle /metrics {
        reverse_proxy localhost:${http_port}
    }

    # Legacy endpoints (Cognito-based)
    handle /anonymous-signin {
        reverse_proxy localhost:${http_port}
    }

    handle /create-room {
        reverse_proxy localhost:${http_port}
    }

    handle /delete-peer {
        reverse_proxy localhost:${http_port}
    }

    handle /delete-peers-and-room {
        reverse_proxy localhost:${http_port}
    }

    # WebSocket signaling (catch-all for everything else)
    handle {
        reverse_proxy localhost:${websocket_port}
    }
}
CADDYEOF

# Restart Caddy to apply config
systemctl restart caddy
systemctl enable caddy

echo "HTTPS configured at https://${duckdns_subdomain}.duckdns.org"
%{ endif }

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

%{ if enable_https }
# Add DuckDNS update to health check script (ensures DNS stays current)
cat >> /usr/local/bin/healthcheck.sh <<'DUCKDNS'

# Update DuckDNS with current public IP
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)
curl -s "https://www.duckdns.org/update?domains=${duckdns_subdomain}&token=${duckdns_token}&ip=$PUBLIC_IP" > /dev/null
DUCKDNS
%{ endif }

# Add health check to crontab (run every 5 minutes)
(crontab -l 2>/dev/null || true; echo "*/5 * * * * /usr/local/bin/healthcheck.sh") | crontab -

echo "Signaling server setup complete" >> /var/log/user-data-complete.log
