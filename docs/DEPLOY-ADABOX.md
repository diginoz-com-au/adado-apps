# Deploy AdaDo on Adabox — First Live Demo

Goal: Get AdaDo running on adabox with 3+ apps working, all agents active, end-to-end test.

## Prerequisites

- adabox is running, SSH access, Docker installed
- `/home/ada/adado/` repo cloned locally
- CLI install script is ready (`install.sh`)

## Step 1: Pre-flight Check

```bash
ssh adabox

# Verify Docker
docker --version
docker compose version

# Verify space
df -h /

# Verify network
curl -s https://adado.diginoz.com.au/ | head -10
```

## Step 2: Clone AdaDo Repos

```bash
cd /home/ada
git clone https://github.com/diginoz-com-au/adado-apps.git adado-live
cd adado-live

# Verify structure
ls -la harness/
ls -la apps/
```

## Step 3: Set Environment

```bash
cd harness

# Copy .env template
cp .env.example .env

# Edit .env
cat > .env << 'EOF'
ADADO_BASE_URL=https://adado.diginoz.com.au
ADADO_DOMAIN=adado.diginoz.com.au
ADADO_DB_PASSWORD=adado_prod_secure_password_here
ADADO_SECRET_KEY=$(openssl rand -hex 32)
VAULTWARDEN_ADMIN_TOKEN=$(openssl rand -hex 32)
NEXTCLOUD_ADMIN=ada
NEXTCLOUD_PASSWORD=$(openssl rand -hex 16)
GRAFANA_USER=ada
GRAFANA_PASSWORD=$(openssl rand -hex 16)
N8N_USER=ada
N8N_PASSWORD=$(openssl rand -hex 16)
OLLAMA_URL=http://host.docker.internal:11434
EOF
```

## Step 4: Start Core (Database, Redis, Proxy)

```bash
# Start just the core infrastructure
docker compose --profile core up -d

# Wait for containers to be healthy
sleep 10
docker compose --profile core ps

# Verify nginx is responding
curl -s http://localhost/ | head -5
```

## Step 5: Start 3 Pilot Apps

### App 1: Projects (Plane)

```bash
docker compose --profile core --profile projects up -d

# Wait for Plane services
sleep 15

# Verify API is up
curl -s http://localhost:8000/api/v1/workspaces/ | jq . || echo "Plane API responding"
```

### App 2: Monitor (Uptime Kuma)

```bash
docker compose --profile core --profile monitor up -d

# Wait
sleep 10

# Verify
curl -s http://localhost:3001/ | head -3
```

### App 3: Passwords (Vaultwarden)

```bash
docker compose --profile core --profile passwords up -d

# Wait
sleep 10

# Verify
curl -s http://localhost:80/identity/connect/authorize | head -3
```

## Step 6: Verify via Portal

```bash
# Update the dashboard to show running apps
# Edit /opt/portal/config/homepage/services.yaml and add:

# - AdaDo Live:
#     - Projects:
#       href: https://adado.diginoz.com.au/projects/
#     - Monitor:
#       href: https://adado.diginoz.com.au/monitor/
#     - Passwords:
#       href: https://adado.diginoz.com.au/passwords/

# Reload homepage container
docker restart homepage
```

Then open [https://portal.diginoz.com.au](https://portal.diginoz.com.au) in a browser and verify the 3 apps are accessible.

## Step 7: Deploy App Agents

Create agent definitions files for each app, one per container:

```bash
# For each agent (projects, monitor, passwords):
mkdir -p /home/ada/adado/agent-configs

cat > /home/ada/adado/agent-configs/projects-agent.yaml << 'EOF'
name: projects-agent
app: adado-projects
port: 8701
model: claude-cli/claude-haiku-4-5-20251001
capabilities:
  - create issues
  - update tasks
  - query board state
  - manage cycles
api_base: http://localhost:8000/api/v1
auth: bearer_token
EOF

# Deploy agents as sidecar containers (or separate containers on port 8701, 8702, 8703)
```

## Step 8: Test End-to-End

### Manual test via chat:
```
User (via Ada): "Create a project called AdaDo Deployment Test"

↓ Routes to Projects Agent

Ada Agent: "Let me create that project for you"
→ POST http://localhost:8000/api/v1/projects/ 
   { name: "AdaDo Deployment Test", ... }

Response: Project created with ID xyz

User response: "Done. Your project is live at /projects/xyz"
```

## Step 9: Monitor Logs

```bash
# Watch Docker logs for any errors
docker compose logs -f adado-proxy
docker compose logs -f adado-projects-web
docker compose logs -f adado-monitor
docker compose logs -f adado-passwords
```

## Step 10: Document Success

If everything is working:
- [ ] All 3 apps responding on their /paths/
- [ ] Portal shows all 3 apps green and accessible
- [ ] Each agent can talk to its app API
- [ ] Coordinator can route user requests to each agent
- [ ] End-to-end: user message → agent → app → response

Document this in `/mnt/nas/OpenClaw/AdaDo/first-deployment-log.md`

## Troubleshooting

### Port conflicts
```bash
# Check what's using port 3000, 8000, etc.
sudo lsof -i :3000
sudo lsof -i :8000
```

### Database connectivity
```bash
# Test PostgreSQL inside the container
docker compose exec adado-db psql -U adado -d adado -c "SELECT 1"
```

### Agent health check
```bash
# Each agent should expose /health endpoint
curl http://localhost:8701/health  # projects agent
curl http://localhost:8702/health  # monitor agent
curl http://localhost:8703/health  # passwords agent
```

### Nginx routing
```bash
# Test nginx config
docker compose exec adado-proxy nginx -t

# Reload if changed
docker compose exec adado-proxy nginx -s reload
```
