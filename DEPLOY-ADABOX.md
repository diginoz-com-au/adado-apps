# Deploy AdaDo to adabox

Deploy AdaDo (the AdaDo Apps platform) to the existing adabox Contabo VPS.

## Prerequisites

- adabox running at `217.216.76.135` (Tailscale: `100.127.152.116`)
- SSH key: `~/.ssh/id_ed25519_contabo`
- Domain: use `adado.diginoz.com.au` (existing subdomain)
- `deploy.sh` script in `/home/ada/adado/deploy/`

## Deploy AdaDo to adabox

```bash
cd /home/ada/adado
bash deploy/deploy.sh 217.216.76.135 adado.diginoz.com.au
```

The script will:
1. Install Docker, Compose, nginx, certbot, ufw, fail2ban
2. Clone AdaDo apps and core manifests
3. Generate `.env` with API keys and JWT secret
4. Start Docker Compose stack (database, proxy, harness, agent coordinator)
5. Configure nginx reverse proxy and SSL via certbot
6. Enable and start fail2ban, ufw firewall

**Expected runtime:** 5–10 minutes.

## Post-Deployment Verification

After the deploy completes, work through the [post-deploy checklist](deploy/post-deploy-checklist.md):

1. **DNS & SSL** — Verify DNS records and HTTPS redirect; check certbot renewal timer
2. **Application** — Verify containers are healthy, health endpoint responds
3. **User flows** — Sign-up, login, chat, WebSocket connectivity
4. **Static pages** — Landing, privacy, terms, trial pages load
5. **Comparison pages** — `/vs/chatgpt`, `/vs/claude`, `/vs/openclaw`, `/vs/hermes`
6. **Security** — Rate limiting, firewall, fail2ban, `.env` permissions
7. **Launch** — Notify Dan, activate campaigns

## Rollback

If deployment fails mid-way:

```bash
ssh -i ~/.ssh/id_ed25519_contabo root@217.216.76.135 'docker compose -f /opt/adado/docker-compose.yml down'
# Fix the issue locally, re-run deploy.sh
```

## Monitoring

Post-deploy, monitor with:

```bash
# SSH to adabox
ssh adabox

# Check compose status
docker compose -f /opt/adado/docker-compose.yml ps

# View logs
docker compose -f /opt/adado/docker-compose.yml logs -f harness

# Check health
curl -s https://adado.diginoz.com.au/api/health | jq .

# Firewall status
ufw status
systemctl status fail2ban
```

## Next Steps

1. Update DNS A records for `adado.diginoz.com.au` if not yet pointing to adabox
2. Run the checklist in [post-deploy-checklist.md](deploy/post-deploy-checklist.md)
3. Activate launch campaigns (Google Ads, Facebook)
4. Notify Dan deployment is live
