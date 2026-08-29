# AdaDo — Your Self-Hosted AI Suite

**"Your home. Your AI. Your rules."**

AdaDo is a self-hosted AI assistant platform. Every app you install gets its own pre-wired AI agent. You manage your entire digital life by chat — no dashboards, no learning curves, just natural language.

Install once, pick your apps, and each one starts working immediately with an AI that understands it.

---

## What Is AdaDo?

Think of it like this:

```
Traditional way:
  Plane → create issues manually
  Firefly → log expenses manually  
  Freqtrade → set up strategy files
  
AdaDo way:
  You: "Create a project called 'Home Renovation'"
  Ada: [creates it in Plane instantly]
  
  You: "I spent $200 on groceries"
  Ada: [logs it in Firefly, asks if you want to update your budget]
  
  You: "Show me my trading strategy performance"
  Ada: [fetches data from Freqtrade, explains the numbers]
```

Every app runs in a container. Every app has an agent. Every agent knows your data and can act on it.

---

## Installation

**One command:**

```bash
curl -fsSL https://adado.diginoz.com.au/install.sh | bash
```

That's it. The script will:
1. Check Docker is installed
2. Clone this repo
3. Start the AdaDo core (database, proxy, auth)
4. Open the app store at `http://localhost/store`

Then you pick which apps you want. Each one installs in ~30 seconds.

---

## The 16 Apps (and Their Agents)

| App | What It Does | Agent Speciality |
|---|---|---|
| **Projects** (Plane) | Task management, boards, sprints | Creates issues, updates tasks, queries status |
| **Passwords** (Vaultwarden) | Password vault | Stores/retrieves secure passwords by category |
| **Finance** (Firefly III) | Personal budgeting | Logs expenses, tracks budgets, generates reports |
| **Inbox** (Chatwoot) | Customer support/messaging | Manages conversations, assigns tickets, tags issues |
| **CRM** (Twenty) | Contacts & deals | Manages relationships, tracks deal progress |
| **Monitor** (Uptime Kuma) | Uptime monitoring | Checks service health, sends alerts |
| **Trading** (Freqtrade) | Crypto trading bot | Manages strategies, reports PnL, backtests |
| **Media** (Jellyfin) | Movies & TV streaming | Organizes library, recommends content |
| **Photos** (Immich) | Photo management | Organizes by date/person, AI tagging, sharing |
| **Automation** (n8n) | Workflow automation | Creates workflows, triggers, integrations |
| **AI Engine** (Dify) | AI workflows | The harness that powers all agents |
| **Docs** (Paperless) | Document archive | Stores receipts, bills, contracts; full-text search |
| **Files** (Nextcloud) | File storage | Sync files across devices, share, backup |
| **Git** (Gitea) | Code repositories | Host your own git server, self-hosted GitHub |
| **Chat** (Open WebUI) | AI chat UI | Chat with any LLM (local or cloud) |
| **Metrics** (Grafana) | Dashboards & alerts | Visualize data from any app |

---

## How It Works

### Architecture

```
┌─────────────────────────┐
│  User (Chat Interface)  │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Ada Coordinator Agent  │
│  (Routes & validates)   │
└────────────┬────────────┘
             │
      ┌──────┼──────┐
      ▼      ▼      ▼
   Projects Finance Trading
   Agent    Agent   Agent ...
      │      │      │
      ▼      ▼      ▼
    Plane  Firefly Freqtrade
           (Your Data)
```

1. **You talk** to Ada in plain English
2. **Ada understands** what you want
3. **Coordinator routes** to the right agent(s)
4. **App agent** calls its app's API
5. **Ada synthesizes** the response in natural language

### Validation & Safety

For dangerous actions (delete, send money, execute trades), Ada asks for confirmation:

```
You: "Delete all trades before September"
Ada: "This will permanently erase 47 trades. Confirm? (yes/no)"
You: "yes"
Ada: "Done. Your trading history is cleared."
```

---

## Your Data Is Yours

- **All data stays on your machine.** No cloud sync, no remote servers.
- **All code is open source.** Fork it, audit it, modify it.
- **Full API access.** If you want to use your data elsewhere, everything's documented.
- **No vendor lock-in.** Export your data anytime.

---

## File Structure

```
adado/
├── harness/               # Docker Compose setup (core infrastructure)
│   ├── docker-compose.yml # All 16 apps + core services
│   ├── nginx.conf         # Routing all apps under one domain
│   ├── .env.example       # Configuration template
│   └── ...
├── apps/                  # App manifests (YAML descriptions)
│   ├── projects.yaml
│   ├── finance.yaml
│   ├── ... (16 total)
│   └── browser.yaml
├── agents/                # Agent definitions (what each agent knows)
│   ├── projects.md
│   ├── finance.md
│   ├── ... (16 total)
│   └── ada-coordinator.md
├── docs/                  # Documentation
│   ├── ARCHITECTURE.md    # Full system design
│   ├── DEPLOY-ADABOX.md   # Deployment walkthrough
│   └── ...
└── README.md (this file)
```

---

## Quick Start

### 1. Install

```bash
curl -fsSL https://adado.diginoz.com.au/install.sh | bash
```

### 2. Pick Your Apps

Open [http://localhost/store](http://localhost/store) and click install on the apps you want.

### 3. Start Using

Open your chat interface and start talking:

- "Create a project called Q4 Goals"
- "Log that I spent $45 on coffee"
- "Show me my Uptime Kuma monitors"
- "What's the latest email from my team?"

---

## Contributing

All 16 core apps are forks of their upstream projects. If you want to:

- **Fix a bug in Plane?** → Fork & PR upstream, we sync regularly
- **Add a feature to an agent?** → Edit `/agents/project-name.md`
- **Create a new app?** → Fork a new container, add manifest + agent definition

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full workflow.

---

## Roadmap

### Phase 1 ✅ — Foundation
- [x] 16 apps forked & containerized
- [x] Docker Compose harness
- [x] Agent definitions for each app
- [x] Ada Coordinator routing
- [ ] First live deployment on adabox

### Phase 2 — Polish
- [ ] Mobile app (iOS/Android)
- [ ] Offline mode (cache + sync)
- [ ] Multi-user (shared AdaDo instance)
- [ ] Custom agent training

### Phase 3 — Scale
- [ ] AdaDo Cloud (managed hosting)
- [ ] Enterprise (white-label + SLA)
- [ ] Marketplace (community agents)
- [ ] API for third-party integrations

---

## License

AdaDo core is **Apache 2.0**. All forked apps keep their original licenses (most are open source too).

---

## Questions?

- **How do I add a new app?** → See [ARCHITECTURE.md](./docs/ARCHITECTURE.md)
- **How do I customize an agent?** → Edit the `.md` file in `/agents/`
- **How do I export my data?** → All data is standard (PostgreSQL, files, etc.) — use any export tool
- **Can I run this in production?** → Yes, it's designed for single-user self-hosting. For multi-user, see the roadmap.

---

## Made by Diginoz

AdaDo is built and maintained by Diginoz. We also offer:

- **Diginoz Cloud** — Managed AdaDo hosting (coming soon)
- **Custom Agents** — AI agents trained on your specific workflows
- **Support** — Professional support & consulting

---

**Start here:** [https://adado.diginoz.com.au](https://adado.diginoz.com.au)

**Install now:** `curl -fsSL https://adado.diginoz.com.au/install.sh | bash`
