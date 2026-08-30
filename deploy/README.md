# AdaDo Production Deployment

One-shot deploy to a fresh Ubuntu 24.04 VPS.

## Prerequisites

1. **SSH key** — `~/.ssh/id_ed25519_contabo` (or set `SSH_KEY` env var)
2. **DNS** — adadoai.com A record pointing to the new VPS IP (can be done in parallel; certbot needs it)
3. **Anthropic API key** — a dedicated AdaDo key (get from console.anthropic.com)
4. **Website built** — `/var/www/adado/` must contain the current site files (already present on Ada's box)

## Run the deploy

```bash
cd /home/ada/adado/deploy
bash deploy.sh <VPS_IP> adadoai.com
```

The script:
1. Installs Docker, nginx, certbot, ufw, fail2ban
2. Uploads the harness (docker-compose + configs) and website
3. Generates strong random secrets and writes `/opt/adado/harness/.env`
4. Configures nginx reverse proxy (port 80 → core API on 8200)
5. Starts `adado-core`, `adado-db`, `adado-redis`, `adado-proxy` containers
6. Prints next steps

Estimated time: ~5–8 minutes on a typical Contabo VPS.

## Post-deploy steps

### 1. Set DNS

Create an A record: `adadoai.com → <VPS_IP>` and `www.adadoai.com → <VPS_IP>`.
Propagation is usually a few minutes with low TTL.

### 2. Get SSL certificate

```bash
ssh root@<VPS_IP>
certbot --nginx -d adadoai.com -d www.adadoai.com \
    --non-interactive --agree-tos -m dan@diginoz.com.au
```

Certbot auto-renews via its systemd timer — no cron needed.

### 3. Update the Anthropic API key

```bash
ssh root@<VPS_IP>
nano /opt/adado/harness/.env
# Change ANTHROPIC_API_KEY=REPLACE_WITH_ADADO_API_KEY to the real key
cd /opt/adado/harness
docker compose --profile core restart adado-core
```

### 4. Verify everything works

```bash
# Health check
curl -s https://adadoai.com/api/health

# Test chat (expect a JSON response)
curl -s -X POST https://adadoai.com/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'

# Check containers
ssh root@<VPS_IP> "docker ps --filter name=adado"
```

### 5. Activate Google Ads campaign

Once the site is live at https://adadoai.com and the landing page verifies, activate the prepared Google Ads campaign.

## Updating the site later

```bash
# Push updated site files
scp -r /var/www/adado/* root@<VPS_IP>:/var/www/adado/

# Push updated harness
scp /home/ada/adado/harness/docker-compose.yml root@<VPS_IP>:/opt/adado/harness/
ssh root@<VPS_IP> "cd /opt/adado/harness && docker compose --profile core pull && docker compose --profile core up -d"
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Can't SSH | Check VPS firewall / ufw / SSH key matches |
| nginx 502 | `ssh root@VPS docker ps` — is adado-core running? |
| Chat fails | Is `ANTHROPIC_API_KEY` set and valid? |
| SSL cert fails | Did DNS propagate? `dig adadoai.com` should return the VPS IP |
| Containers crash | `ssh root@VPS docker logs adado-core` |
