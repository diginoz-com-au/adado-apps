# AdaDo Agent Definition Spec

**Status:** v1 (stable) · **Schema:** [`schema/agent.schema.json`](../schema/agent.schema.json) · **Reference impl:** `apps/projects.yaml` (Plane / AdaDo Projects)

Every AdaDo app can ship exactly one **agent** — the thing a user talks to when they say *"Ada, do X."* This document defines the canonical schema for those agent definitions so every agent is declared the same way, is machine-validatable, and can be provisioned automatically by the harness.

## Where an agent is defined

An agent lives in two places, by design:

| File | Role | Format |
|------|------|--------|
| `apps/<slug>.yaml` → `agent:` block | **Machine contract** — what the harness provisions and enforces (id, model, tools, auth, triggers). | Validated against `schema/agent.schema.json`. |
| `agents/<slug>.md` | **Soul / instructions** — persona, knowledge, example conversations, API reference the agent reads at runtime. | Free-form Markdown with a `## Soul` header. |

The YAML block is the source of truth for *capabilities the platform grants*. The Markdown file is the source of truth for *how the agent behaves*. The `agent.id` in YAML MUST match the `agent=` parameter in the app's `chat_endpoint`, and SHOULD match the Markdown filename slug.

## The schema

Fields (see `schema/agent.schema.json` for the authoritative definition):

### Required

- **`id`** *(string)* — Unique, stable agent identifier. Pattern `^[a-z][a-z0-9-]*-agent$`. Must match `chat_endpoint`'s `agent=` value. Never change once shipped.
- **`display_name`** *(string)* — Human-facing name in the AdaDo UI (≤60 chars).
- **`description`** *(string)* — One plain-language line: what this agent does for the user (≤200 chars).
- **`capabilities`** *(string[])* — Concrete, user-facing things the agent can do. Each item is a short phrase or example. Shown on the app tile and used to route intent. At least one.

### Recommended

- **`model`** *(string)* — Model id (default `claude-cli/claude-haiku-4-5-20251001`). Pick the **cheapest tier that does the job**; reserve larger models for genuine reasoning agents.
- **`required_apps`** *(string[])* — App ids (`adado-*`) this agent needs installed/running. Almost always includes its own app. The harness blocks activation until these are present.
- **`mcp_tools`** *(string[])* — MCP tool name patterns the agent may call, globs allowed (e.g. `mcp__plane__*`). This is an allow-list: the harness scopes the agent's tool access to exactly these.
- **`trigger_hooks`** *(object[])* — What activates the agent. Each has a `type` (`message` | `cron` | `webhook` | `event` | `manual`) plus optional `config` (cron expr, webhook path, event name, or message pattern) and `description`. Omit → defaults to `message`.
- **`auth`** *(object)* — `{ required: bool, credentials: [...] }`. Each credential has a `name`, a `type` (`api_key` | `bearer_token` | `oauth` | `basic` | `session_cookie` | `none`), a `location` (e.g. `header:X-API-Key`, `env:PLANE_API_TOKEN`), and a `description`. Declares what the provisioning flow must collect from the user.

### Optional

- **`api_base`** *(string)* — Base URL of the app API the agent talks to.

## Rules

1. **The YAML block is an allow-list, not documentation.** If a tool or app isn't declared, the harness does not grant it. Declare the minimum needed.
2. **`id` is immutable.** It's referenced by the chat endpoint, the router, and stored sessions.
3. **Least privilege.** `mcp_tools` and `auth.credentials` should be the smallest set that delivers the capabilities.
4. **Every agent validates.** `scripts/validate-agents.py` runs in CI-spirit; a manifest whose `agent:` block fails the schema is a broken app.
5. **Capabilities are promises.** Don't list a capability the agent can't actually perform with its declared tools/auth.

## Validate

```bash
python3 scripts/validate-agents.py          # validate every apps/*.yaml agent block
python3 scripts/validate-agents.py apps/projects.yaml   # one file
```

Exit code is non-zero if any agent block fails. No third-party dependencies required.

## Reference implementation

`apps/projects.yaml` (the Plane-backed **AdaDo Projects** agent) is the canonical, fully-populated example every new agent should be copied from. It exercises every field: scoped `mcp_tools` (`mcp__plane__*`), `required_apps`, `message` + `cron` triggers, and an `api_key` auth declaration. Its behaviour/persona lives in `agents/projects.md`.
