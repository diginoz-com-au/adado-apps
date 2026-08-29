# AdaDo Changelog

## [0.3.0] — 2026-08-30

### Added
- Onboarding wizard: multi-step setup on first login collects role, use cases, experience level, and goals
- Personalised Ada: system prompt dynamically adapts to each user's profile from onboarding data
- `GET /api/auth/me` endpoint: returns full user profile including tier, trial status, onboarding state
- `POST /api/auth/onboard` endpoint: saves onboarding data and marks setup complete
- 7-day free trial built into signup: `trial_ends_at` tracked per user
- Tier system: `cloud`, `cli`, `vps`, `enterprise` — tracked in DB, exposed in `/api/auth/me`
- 14 new app definitions: email, notes, calendar, network, homelab, backup, vpn, writing, analytics, links, security, shopping, social, health
- Agent definitions for all 14 new apps with capabilities, example conversations, and API references
- Docker services for all new apps in `docker-compose.yml`
- Web Speech API voice input in chat UI (mic button, en-AU locale)
- `ado` CLI: cross-platform Python stdlib WebSocket client for terminal access
- Linux install: `curl -sL adado.diginoz.com.au/install-cli.sh | bash`
- Windows install: `irm adado.diginoz.com.au/install-cli.ps1 | iex`
- 7-day trial landing page at `/trial`
- Standalone login page at `/login`
- Marketing homepage at root

### Changed
- AI streaming: `stream_anthropic()` and `stream_ollama()` now accept a `soul` parameter (personalised per user)
- DB schema: `users` table gets `tier`, `trial_ends_at`, `onboarding_complete`, `onboarding_data` columns with safe migration
- Status endpoint now includes `version` field

### Fixed
- WebSocket API/auth routing conflict in nginx (specific location blocks before general `/api/` block)
- adado-proxy port 80 conflict with host nginx (moved to optional `proxy` profile, core on `127.0.0.1:8200`)

---

## [0.2.0] — 2026-08-30

### Added
- Dual AI backend: Anthropic API (sk-* key) or Ollama (local, free) via `USE_ANTHROPIC` flag
- `stream_ollama()` using httpx with SSE streaming against OpenAI-compatible `/v1/chat/completions`
- `host.docker.internal:host-gateway` extra_hosts for container→host Ollama access
- Logo SVG (purple triangle mark) in chat topbar, welcome screen, and as favicon
- Voice input via Web Speech API (mic button, en-AU)
- Session persistence in SQLite

### Fixed
- Model name defaulting to `claude-sonnet` in compose; fixed to `qwen2.5:14b` for Ollama

---

## [0.1.0] — 2026-08-29 (initial build)

### Added
- FastAPI core: JWT auth, SQLite sessions, YAML app catalog loader
- WebSocket chat with streaming AI responses
- React-free single-file chat UI with dark purple theme
- 17 initial app definitions and agent files
- Docker Compose harness with profiles (core, projects, passwords, finance, inbox, crm, monitor, trading, media, photos, automation, ai, docs, files, git, chat, metrics)
- Host nginx config for adado.diginoz.com.au with wildcard SSL
