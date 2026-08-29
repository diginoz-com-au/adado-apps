# AdaDo Architecture

**"Your home. Your AI. Your rules."**

AdaDo is a self-hosted AI suite. Every app ships with its own AI agent. You install once, then manage your entire digital life by chat.

---

## The Stack

```
┌─────────────────────────────────────────────────────┐
│                    USER                             │
│         "What's overdue in my projects?"            │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────┐
│              ADA (Coordinator)                      │
│   Routes intent to the right app agent              │
│   Critic-Actor loops for irreversible actions       │
│   Multi-agent validation for complex tasks          │
└───┬───────┬───────┬───────┬───────┬─────────────────┘
    │       │       │       │       │
    ▼       ▼       ▼       ▼       ▼
Projects Finance Trading Monitor  Docs ...
 Agent   Agent   Agent   Agent   Agent
    │       │       │       │       │
    ▼       ▼       ▼       ▼       ▼
  Plane  Firefly Freqtrade Uptime Paperless
                          Kuma
```

---

## Core Repos (platform, not apps)

| Repo | Role |
|---|---|
| `Adado` | CasaOS fork — App Store UI, Docker orchestration |
| `adado-cli` | One-command installer — curl \| bash |
| `adado-dashboard` | gethomepage fork — unified portal |
| `adado-ai` | Dify fork — AI workflow engine, multi-agent pipelines |
| `adado-browser` | Containerized Playwright — browser automation agent |
| `adado-proxy` | nginx config templates — routes all apps |
| `adado-auth` | Authelia SSO — single login for everything |
| `adado-apps` | App manifests — YAML definition for each app |
| `adado-agents` | Agent definitions — what each agent knows and can do |

---

## App Repos (forks, each gets an agent)

16 apps across every life category. Each repo:
- Is a fork of the upstream project
- Has AdaDo branding in README
- Has a matching agent definition in `adado-agents`
- Is available in the AdaDo Store via its manifest in `adado-apps`

---

## Install Flow

```
curl -fsSL https://adado.diginoz.com.au/install.sh | bash
    │
    ├── Detects OS
    ├── Checks Docker
    ├── Clones adado-apps repo → ~/adado/
    ├── Creates .env from template
    ├── docker compose --profile core up -d
    │     └── Starts: nginx proxy, postgres, redis, ada-coordinator
    └── Opens AdaDo Store → http://localhost/store
            │
            └── User picks apps
                    │
                    └── docker compose --profile <app> up -d
                            │
                            └── Agent activates:
                                  • Health checks app
                                  • Auto-configures (API keys, DB, settings)
                                  • Creates first login
                                  • Starts working immediately
```

---

## Agent Model

Each agent:
- Knows its app's data model (entities, relationships)
- Knows its app's API (endpoints, auth, rate limits)
- Runs on `claude-cli/claude-haiku-4-5-20251001` by default (cheap, fast)
- Escalates to Sonnet for complex multi-step tasks
- Routes through `ada-coordinator` for cross-app tasks

### Agentic Workflow Patterns

| Pattern | Used when |
|---|---|
| **ReAct loop** | Any multi-step task (plan → act → observe → repeat) |
| **Critic-Actor** | Irreversible actions (delete, send, trade real money) |
| **Self-consistency** | Ambiguous intent — run N interpretations, take consensus |
| **LLM-as-Judge** | Validating agent output before presenting to user |
| **Supervisor** | Ada delegates to app agents, aggregates results |
| **Human-in-the-loop** | Anything external/irreversible — confirm before acting |

---

## Business Model

```
OPEN SOURCE CORE (Apache 2.0)
└── Anyone can self-host free
└── Builds community, reputation, contributions

DIGINOZ CLOUD (subscription)
└── Managed hosting — one-click deploy, auto-updates
└── Premium agents — more capable, more integrations
└── Priority support, SLA

ENTERPRISE (white-label)
└── Custom branding (your company's AI suite)
└── Custom agents built to your workflows
└── On-premise or VPC deployment
└── SOC2 compliance path
```

---

## Roadmap

### Phase 1 — Foundation (NOW)
- [x] 16 app repos forked
- [x] Docker Compose harness
- [x] 16 app manifests
- [x] 16 agent definitions
- [x] Website live
- [x] Pitch deck
- [ ] adado-browser (in progress)
- [ ] Install script (in progress)

### Phase 2 — First Working Deploy
- [ ] Full stack running on adabox
- [ ] 3+ agents actively managing apps
- [ ] AdaDo Store UI working
- [ ] Video demo

### Phase 3 — Product
- [ ] adado.com.au domain live (pending Diginoz Pty Ltd reinstatement)
- [ ] Diginoz Cloud beta
- [ ] Docker Hub diginoz/adado published
- [ ] First external users

### Phase 4 — Scale
- [ ] Move off GitHub → self-hosted Gitea
- [ ] Own CDN for app distribution
- [ ] CLI installable via npm/brew/apt
- [ ] Enterprise pilot customers
