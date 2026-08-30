# AdaDo Post-Deploy Checklist

Work through this after `deploy.sh` completes. Tick each item once verified against the live site.

---

## DNS & SSL

- [ ] DNS A record for `adadoai.com` → VPS IP (verify: `dig adadoai.com +short`)
- [ ] DNS A record for `www.adadoai.com` → VPS IP
- [ ] SSL cert obtained: `certbot --nginx -d adadoai.com -d www.adadoai.com`
- [ ] HTTPS redirect working: `curl -I http://adadoai.com` returns 301 to https
- [ ] Certificate auto-renew active: `systemctl status certbot.timer`

## Application

- [ ] `ANTHROPIC_API_KEY` updated to dedicated AdaDo API key (not the placeholder)
- [ ] `JWT_SECRET` is strong random hex (grep `/opt/adado/harness/.env` — must not be `change_me_in_production`)
- [ ] All containers healthy: `docker ps --filter name=adado` shows Up, not Restarting
- [ ] Health endpoint responds: `curl -s https://adadoai.com/api/health`

## User Flows

- [ ] Landing page loads correctly at `https://adadoai.com`
- [ ] Sign-up flow works — can register a new account
- [ ] Login works — can log in with test account
- [ ] Chat works — can send a message and receive a Claude response
- [ ] WebSocket stays connected (no immediate disconnect)

## Static Pages

- [ ] `robots.txt` accessible: `curl https://adadoai.com/robots.txt`
- [ ] `sitemap.xml` accessible: `curl https://adadoai.com/sitemap.xml`
- [ ] Privacy page: `https://adadoai.com/privacy`
- [ ] Terms page: `https://adadoai.com/terms`
- [ ] Trial/signup page: `https://adadoai.com/trial`

## Comparison Pages

- [ ] `https://adadoai.com/vs/chatgpt` loads correctly
- [ ] `https://adadoai.com/vs/claude` loads correctly
- [ ] `https://adadoai.com/vs/openclaw` loads correctly
- [ ] `https://adadoai.com/vs/hermes` loads correctly
- [ ] All vs/ pages link to `adadoai.com` (not `adado.diginoz.com.au`)

## Security

- [ ] Rate limiting active on nginx (check `/etc/nginx/sites-available/adado`)
- [ ] ufw enabled: `ufw status` shows active, only 22/80/443 open
- [ ] fail2ban running: `systemctl status fail2ban`
- [ ] `.env` file permissions: `ls -la /opt/adado/harness/.env` shows 600
- [ ] No secrets in nginx logs or docker inspect output

## Launch

- [ ] Google Ads campaign activated (once site verifies)
- [ ] Facebook launch post published
- [ ] Dan notified site is live
