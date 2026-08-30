#!/bin/bash
# AdaDo Production Deployment Script
# Usage: bash deploy.sh <vps-ip> <domain>
# Example: bash deploy.sh 1.2.3.4 adadoai.com
set -euo pipefail

VPS_IP="${1:?VPS IP required}"
DOMAIN="${2:-adadoai.com}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519_contabo}"
ADADO_DIR="/home/ada/adado"

SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no root@$VPS_IP"
SCP="scp -i $SSH_KEY"

echo "=== AdaDo Production Deploy ==="
echo "VPS: $VPS_IP | Domain: $DOMAIN"
echo ""

# ----------------------------------------------------------------
# 1. Install Docker + dependencies
# ----------------------------------------------------------------
echo "[1/6] Installing Docker and dependencies..."
$SSH bash << 'REMOTE'
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q \
    docker.io docker-compose-v2 \
    certbot python3-certbot-nginx \
    nginx git curl ufw fail2ban
systemctl enable --now docker nginx
ufw --force enable
ufw allow ssh
ufw allow http
ufw allow https
echo "Docker: $(docker --version)"
echo "Compose: $(docker compose version)"
REMOTE
echo "  done."

# ----------------------------------------------------------------
# 2. Create directory structure on VPS
# ----------------------------------------------------------------
echo "[2/6] Creating /opt/adado structure..."
$SSH mkdir -p /opt/adado/{harness,core,agents,apps,data}

# ----------------------------------------------------------------
# 3. Copy harness files
# ----------------------------------------------------------------
echo "[3/6] Copying AdaDo harness..."
$SCP -r "$ADADO_DIR/harness/docker-compose.yml"    root@"$VPS_IP":/opt/adado/harness/
$SCP -r "$ADADO_DIR/harness/docker-compose.override.yml" root@"$VPS_IP":/opt/adado/harness/ 2>/dev/null || true
$SCP -r "$ADADO_DIR/harness/nginx.conf"            root@"$VPS_IP":/opt/adado/harness/
$SCP -r "$ADADO_DIR/harness/.env.example"          root@"$VPS_IP":/opt/adado/harness/
$SCP -r "$ADADO_DIR/apps"                          root@"$VPS_IP":/opt/adado/
$SCP -r "$ADADO_DIR/agents"                        root@"$VPS_IP":/opt/adado/
[ -d "$ADADO_DIR/core" ] && $SCP -r "$ADADO_DIR/core" root@"$VPS_IP":/opt/adado/ || true
echo "  done."

# ----------------------------------------------------------------
# 4. Generate secrets and write .env
# ----------------------------------------------------------------
echo "[4/6] Generating secrets and writing .env..."

JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
ADADO_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
ADADO_DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
FIREFLY_APP_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")

# Write .env — API key left as placeholder; update before first run
$SSH "cat > /opt/adado/harness/.env" << ENVEOF
# AdaDo Production Environment
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# IMPORTANT: Set ANTHROPIC_API_KEY before starting services

ADADO_BASE_URL=https://$DOMAIN
ADADO_DOMAIN=$DOMAIN

# === SECRETS (auto-generated — do not change after first run) ===
JWT_SECRET=$JWT_SECRET
ADADO_SECRET_KEY=$ADADO_SECRET_KEY
ADADO_DB_PASSWORD=$ADADO_DB_PASSWORD

# === ANTHROPIC (required — add dedicated API key) ===
ANTHROPIC_API_KEY=REPLACE_WITH_ADADO_API_KEY
CLAUDE_MODEL=claude-sonnet-4-6
USE_ANTHROPIC=true

# === INSTANCE ===
INSTANCE_NAME=AdaDo
TZ=UTC

# === APP CREDENTIALS (change before enabling these apps) ===
VAULTWARDEN_ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
FIREFLY_APP_KEY=${FIREFLY_APP_KEY}0000000000000000
N8N_USER=admin
N8N_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
NEXTCLOUD_ADMIN=admin
NEXTCLOUD_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
GRAFANA_USER=admin
GRAFANA_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

# === OPTIONAL ===
OLLAMA_URL=http://host.docker.internal:11434
MEDIA_DIR=/mnt/media
CONSUME_DIR=/opt/adado/data/inbox
ENVEOF
$SSH chmod 600 /opt/adado/harness/.env
echo "  done. (Update ANTHROPIC_API_KEY before starting)"

# ----------------------------------------------------------------
# 5. Configure nginx + copy website
# ----------------------------------------------------------------
echo "[5/6] Configuring nginx..."
$SSH bash << REMOTE
set -e
cat > /etc/nginx/sites-available/adado << 'NGINX'
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    # Website static files
    root /var/www/adado;
    index index.html;

    # AdaDo core API
    location /api/ {
        proxy_pass http://127.0.0.1:8200/api/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # WebSocket (chat)
    location /ws/ {
        proxy_pass http://127.0.0.1:8200/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }

    # Static site routing (SPA)
    location / {
        try_files \$uri \$uri/ \$uri.html /index.html;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/adado /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "nginx configured OK"
REMOTE

# Copy website files
echo "  Copying website..."
$SSH mkdir -p /var/www/adado
$SCP -r /var/www/adado/* root@"$VPS_IP":/var/www/adado/
$SSH chown -R www-data:www-data /var/www/adado
echo "  done."

# ----------------------------------------------------------------
# 6. Start AdaDo core stack
# ----------------------------------------------------------------
echo "[6/6] Starting AdaDo core stack..."
$SSH bash << 'REMOTE'
set -e
cd /opt/adado/harness

# Warn if API key not set
API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2)
if [ "$API_KEY" = "REPLACE_WITH_ADADO_API_KEY" ]; then
    echo "  WARNING: ANTHROPIC_API_KEY not set — chat will not work until updated"
fi

docker compose --profile core up -d --build
sleep 15
echo ""
echo "Running containers:"
docker ps --filter "name=adado" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
REMOTE

# ----------------------------------------------------------------
# Summary
# ----------------------------------------------------------------
echo ""
echo "================================================================"
echo "  AdaDo deploy complete!"
echo "================================================================"
echo ""
echo "  Site:    http://$VPS_IP  (DNS pending)"
echo "  Domain:  https://$DOMAIN  (after DNS + SSL)"
echo ""
echo "  Next steps:"
echo "    1. Point adadoai.com A record to $VPS_IP"
echo "    2. Set ANTHROPIC_API_KEY in /opt/adado/harness/.env"
echo "    3. Get SSL cert:"
echo "         ssh root@$VPS_IP"
echo "         certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m dan@diginoz.com.au"
echo "    4. Restart stack: cd /opt/adado/harness && docker compose --profile core restart"
echo "    5. Verify: curl -s https://$DOMAIN/api/health"
echo ""
echo "  Credentials file: /opt/adado/harness/.env  (chmod 600)"
echo "================================================================"
