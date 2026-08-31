"""
AdaDo Core — The Ada runtime for customers.
FastAPI server: auth, onboarding, WebSocket chat, app tiles.
Supports Anthropic API (sk-ant-* key) or Ollama (local, free).
"""
import os, json, sqlite3, hashlib, secrets, time, yaml, bcrypt
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt

# ─── Config ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "qwen2.5:14b")
JWT_SECRET        = os.getenv("JWT_SECRET", secrets.token_hex(32))
DB_PATH           = os.getenv("DB_PATH", "/data/adado.db")
APPS_DIR          = os.getenv("APPS_DIR", "/apps")
AGENTS_DIR        = os.getenv("AGENTS_DIR", "/agents")
CORE_DIR          = os.getenv("CORE_DIR",   "/agents/core")
INSTANCE_NAME     = os.getenv("INSTANCE_NAME", "Ada")

USE_ANTHROPIC = bool(ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-"))

TRIAL_DAYS = 7

# ─── Locked template loader ───────────────────────────────────────────────────

def _load_core_md(filename: str) -> str:
    """Load a locked template MD from the core directory."""
    try:
        return Path(CORE_DIR, filename).read_text().strip()
    except Exception:
        return ""

_SOUL_TEMPLATE:   str = ""
_AGENTS_TEMPLATE: str = ""

def _get_soul_template() -> str:
    global _SOUL_TEMPLATE
    if not _SOUL_TEMPLATE:
        _SOUL_TEMPLATE = _load_core_md("SOUL.md")
    return _SOUL_TEMPLATE

def _get_agents_template() -> str:
    global _AGENTS_TEMPLATE
    if not _AGENTS_TEMPLATE:
        _AGENTS_TEMPLATE = _load_core_md("AGENTS.md")
    return _AGENTS_TEMPLATE

DEFAULT_SOUL = (
    "You are Ada, an AI assistant built on AdaDo. "
    "You are direct, capable, and proactive. You get things done. "
    "You help the user manage their life through their connected apps. "
    "Talk like a capable colleague, not a customer service bot."
)

def build_soul(name: str, onboarding: dict) -> str:
    """Build the system prompt for a user's Ada agent from the locked template."""
    base = _get_soul_template() or DEFAULT_SOUL

    # User-specific context appended after the locked base — never replaces it
    parts = [base, f"\n\n## Your User\nYour user's name is {name}."]

    role = onboarding.get("role", "")
    if role:
        parts.append(f"They are a {role}.")

    uses = onboarding.get("use_cases", [])
    if uses:
        parts.append(f"They primarily use Ada for: {', '.join(uses)}.")

    goals = onboarding.get("goals", "")
    if goals:
        parts.append(f"Context they shared: {goals}")

    exp_map = {
        "beginner":     "They're new to AI — explain things simply, avoid jargon.",
        "intermediate": "They're comfortable with AI tools — be efficient.",
        "advanced":     "They're an experienced AI user — be terse and technical when appropriate.",
    }
    exp = exp_map.get(onboarding.get("experience", ""), "")
    if exp:
        parts.append(exp)

    agents_ctx = _get_agents_template()
    if agents_ctx:
        parts.append(f"\n\n## App & Agent System\n{agents_ctx[:800]}")

    return "\n".join(parts)

# ─── Agent coordinator ────────────────────────────────────────────────────────

# Keyword-to-agent routing table. First match wins; order matters.
_AGENT_ROUTES: list[tuple[list[str], str]] = [
    (["email", "inbox", "mail", "message", "reply", "send", "unsubscribe"], "inbox"),
    (["calendar", "meeting", "schedule", "event", "reminder", "appointment"], "calendar"),
    (["task", "project", "plane", "issue", "sprint", "backlog", "ticket"], "projects"),
    (["file", "folder", "document", "upload", "download", "storage", "nextcloud"], "files"),
    (["git", "github", "commit", "pull request", "pr", "branch", "repo", "code"], "git"),
    (["automat", "workflow", "n8n", "trigger", "cron", "zapier"], "automation"),
    (["finance", "money", "invoice", "expense", "budget", "firefly", "transaction"], "finance"),
    (["password", "secret", "credential", "vault", "bitwarden"], "passwords"),
    (["monitor", "metric", "grafana", "alert", "cpu", "memory", "disk", "uptime"], "monitor"),
    (["analytics", "data", "report", "chart", "dashboard"], "analytics"),
    (["note", "writing", "draft", "doc", "blog", "obsidian"], "notes"),
    (["health", "sleep", "exercise", "habit", "wellbeing"], "health"),
    (["photo", "image", "picture", "album", "gallery"], "photos"),
    (["legal", "contract", "compliance"], "legal"),
    (["network", "vpn", "tailscale", "firewall", "dns", "port"], "network"),
    (["homelab", "docker", "server", "container", "deploy", "infra"], "homelab"),
    (["backup", "restore", "snapshot"], "backup"),
    (["research", "search", "find", "explain", "why", "how", "what is"], "ai"),
    (["chat", "social", "chatwoot", "support", "ticket"], "chat"),
    (["shop", "buy", "order", "product", "cart"], "shopping"),
    (["crm", "customer", "contact", "lead", "sales"], "crm"),
    (["trading", "crypto", "stock", "market", "portfolio"], "trading"),
]

_agent_cache: dict[str, str] = {}

def load_agents() -> dict[str, str]:
    if _agent_cache:
        return _agent_cache
    agents_path = Path(AGENTS_DIR)
    if not agents_path.exists():
        return {}
    for f in agents_path.glob("*.md"):
        try:
            _agent_cache[f.stem] = f.read_text()
        except Exception:
            pass
    return _agent_cache

def route_agent(message: str) -> str:
    msg_lower = message.lower()
    for keywords, agent_name in _AGENT_ROUTES:
        if any(kw in msg_lower for kw in keywords):
            return agent_name
    return "ai"  # default: general reasoning agent

def agent_context(agent_name: str) -> str:
    agents = load_agents()
    content = agents.get(agent_name, "")
    if not content:
        return ""
    # Strip YAML frontmatter if present
    lines = content.strip().splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
            lines = lines[end + 1:]
        except ValueError:
            pass
    # Return first 600 chars of agent context — enough for role/scope
    return "\n".join(lines)[:600].strip()

def build_soul_with_agent(name: str, onboarding: dict, agent_name: str) -> str:
    base = build_soul(name, onboarding)
    ctx = agent_context(agent_name)
    if ctx:
        return base + f"\n\n--- Active Mode: {agent_name} ---\n{ctx}"
    return base

# ─── Per-app pinned agent support ─────────────────────────────────────────────

def load_agent_by_id(agent_id: str) -> str:
    """Return agent markdown content, tolerating -agent suffix conventions."""
    agents = load_agents()
    if agent_id in agents:
        return agents[agent_id]
    base = agent_id.replace("-agent", "").replace("_agent", "")
    return agents.get(base, "")

def build_pinned_agent_soul(name: str, onboarding: dict, agent_id: str) -> str:
    """Build a soul that IS the specified app agent — not just augmented with it."""
    base = build_soul(name, onboarding)
    content = load_agent_by_id(agent_id)
    if not content:
        return base
    lines = content.strip().splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
            lines = lines[end + 1:]
        except ValueError:
            pass
    agent_text = "\n".join(lines).strip()
    return (
        base
        + f"\n\n## You are the {agent_id} specialist agent.\n"
        + "You are operating in a dedicated single-app context. Stay focused on this "
        + "app's domain. Do not route to other agents.\n\n"
        + agent_text
    )

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            api_key TEXT,
            tier TEXT DEFAULT 'cloud',
            trial_ends_at INTEGER,
            onboarding_complete INTEGER DEFAULT 0,
            onboarding_data TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            messages TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            task_type TEXT NOT NULL DEFAULT 'chat',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS burn_limits (
            user_id INTEGER PRIMARY KEY,
            daily_limit_usd REAL,
            monthly_limit_usd REAL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'unknown',
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, token)
        );
        CREATE INDEX IF NOT EXISTS idx_push_user ON push_tokens(user_id);

        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_webhooks_user ON webhooks(user_id);
        CREATE INDEX IF NOT EXISTS idx_webhooks_token ON webhooks(token);

        CREATE TABLE IF NOT EXISTS cron_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            schedule TEXT NOT NULL,
            prompt TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            created_at INTEGER NOT NULL,
            last_run INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_cron_user ON cron_jobs(user_id);

        CREATE TABLE IF NOT EXISTS standing_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_so_user ON standing_orders(user_id);
    """)
    # Migrate existing tables — add columns if missing (SQLite doesn't support IF NOT EXISTS on ALTER)
    for col, defn in [
        ("tier",                    "TEXT DEFAULT 'cloud'"),
        ("trial_ends_at",           "INTEGER"),
        ("onboarding_complete",     "INTEGER DEFAULT 0"),
        ("onboarding_data",         "TEXT DEFAULT '{}'"),
        ("preferred_model",         "TEXT"),
        ("stripe_customer_id",      "TEXT"),
        ("stripe_subscription_id",  "TEXT"),
        ("subscription_status",     "TEXT DEFAULT 'free'"),
        ("email_address",           "TEXT"),
        ("email_provisioned",       "INTEGER DEFAULT 0"),
        ("interests",               "TEXT DEFAULT '[]'"),
    ]:
        try:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
        except Exception:
            pass
    # Migrate sessions — add status column if missing
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN status TEXT DEFAULT 'active'")
    except Exception:
        pass
    # Email forwards table
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_forwards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            from_address TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, stored_hash: str) -> bool:
    # Legacy migration: old SHA-256 hashes don't start with $2b$
    if not stored_hash.startswith("$2b$"):
        return stored_hash == hashlib.sha256(pw.encode()).hexdigest()
    return bcrypt.checkpw(pw.encode(), stored_hash.encode())

def make_token(user_id: int, email: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": time.time() + 86400 * 30},
        JWT_SECRET, algorithm="HS256"
    )

def verify_token(token: str) -> Optional[dict]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        data["sub"] = int(data["sub"])  # normalise back to int for DB queries
        return data
    except Exception:
        return None

def get_auth_user(request: Request) -> Optional[dict]:
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token:
        token = request.query_params.get("token", "")
    return verify_token(token) if token else None

# ─── Token cost tracking ──────────────────────────────────────────────────────

# Approximate USD per 1M tokens — update when pricing changes
_MODEL_COSTS = {
    "claude-3-haiku-20240307":     {"input": 0.25,  "output": 1.25},
    "claude-haiku-4-5-20251001":   {"input": 0.80,  "output": 4.00},
    "claude-3-5-sonnet-20241022":  {"input": 3.00,  "output": 15.0},
    "claude-sonnet-4-6":           {"input": 3.00,  "output": 15.0},
    "claude-opus-4-8":             {"input": 15.0,  "output": 75.0},
}

def _model_cost(model: str, input_tok: int, output_tok: int) -> float:
    rates = _MODEL_COSTS.get(model) or _MODEL_COSTS.get(model.split("/")[-1], {"input": 0.0, "output": 0.0})
    return (input_tok * rates["input"] + output_tok * rates["output"]) / 1_000_000

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

MAX_CONTEXT_TOKENS = 80_000  # safe budget for history (leaves room for soul + output)
MAX_SINGLE_MSG_TOKENS = 20_000  # cap any one message to this

def _trim_context(messages: list, soul: str) -> list:
    soul_tokens = _estimate_tokens(soul)
    budget = MAX_CONTEXT_TOKENS - soul_tokens

    # Truncate any single message that is individually too long
    capped = []
    for m in messages:
        content = m.get("content", "")
        if _estimate_tokens(content) > MAX_SINGLE_MSG_TOKENS:
            chars = MAX_SINGLE_MSG_TOKENS * 4
            half = chars // 2
            content = content[:half] + "\n\n[... trimmed for context window ...]\n\n" + content[-half:]
        capped.append({**m, "content": content})

    # Drop oldest messages until total fits in budget (always keep at least 2)
    while len(capped) > 2:
        if sum(_estimate_tokens(m.get("content", "")) for m in capped) <= budget:
            break
        capped.pop(0)

    return capped

def log_task(db, user_id: int, model: str, input_tok: int, output_tok: int, task_type: str = "chat") -> float:
    cost = _model_cost(model, input_tok, output_tok)
    db.execute(
        "INSERT INTO task_logs (user_id, model, input_tokens, output_tokens, cost_usd, task_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, model, input_tok, output_tok, round(cost, 8), task_type, int(time.time()))
    )
    db.commit()
    return cost

def check_burn_rate(db, user_id: int) -> dict:
    now = int(time.time())
    limits = db.execute("SELECT * FROM burn_limits WHERE user_id = ?", (user_id,)).fetchone()
    daily_limit   = limits["daily_limit_usd"]   if limits else None
    monthly_limit = limits["monthly_limit_usd"] if limits else None

    def _sum(since):
        r = db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS t FROM task_logs WHERE user_id = ? AND created_at > ?",
            (user_id, since)
        ).fetchone()
        return r["t"]

    daily_spent   = _sum(now - 86400)
    monthly_spent = _sum(now - 86400 * 30)
    return {
        "daily_spent":   round(daily_spent,   6),
        "monthly_spent": round(monthly_spent, 6),
        "daily_limit":   daily_limit,
        "monthly_limit": monthly_limit,
        "over_daily":    bool(daily_limit   and daily_spent   >= daily_limit),
        "over_monthly":  bool(monthly_limit and monthly_spent >= monthly_limit),
    }

# ─── Standing orders helper ───────────────────────────────────────────────────

def get_standing_orders_block(user_id: int) -> str:
    """Return a formatted soul block for the user's enabled standing orders."""
    db = get_db()
    rows = db.execute(
        "SELECT instruction FROM standing_orders WHERE user_id = ? AND enabled = 1 ORDER BY priority DESC, id ASC",
        (user_id,)
    ).fetchall()
    db.close()
    if not rows:
        return ""
    lines = "\n".join(f"- {r['instruction']}" for r in rows)
    return f"\n\n## Standing Orders (always follow these)\n{lines}"


# ─── Apps loader ──────────────────────────────────────────────────────────────

def load_apps(include_core: bool = False) -> list[dict]:
    apps = []
    apps_path = Path(APPS_DIR)
    if not apps_path.exists():
        return []
    for f in sorted(apps_path.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text())
            if data:
                if not include_core and data.get("is_core"):
                    continue
                apps.append(data)
        except Exception:
            pass
    return apps

# ─── AI streaming ─────────────────────────────────────────────────────────────

# Models users are allowed to select, keyed by tier
ALLOWED_MODELS = {
    "cloud":      ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "cli":        ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-8"],
    "vps":        ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-8"],
    "enterprise": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-8"],
}

def resolve_model(user_row) -> str:
    """Return the model to use for this user — preferred if allowed, else default."""
    preferred = user_row["preferred_model"] if user_row and user_row["preferred_model"] else None
    tier      = user_row["tier"] if user_row else "cloud"
    allowed   = ALLOWED_MODELS.get(tier, ALLOWED_MODELS["cloud"])
    if preferred and preferred in allowed:
        return preferred
    return CLAUDE_MODEL

CLAUDE_PROXY_URL = os.getenv("CLAUDE_PROXY_URL", "")

TOOL_CAPABLE_MODELS = {
    "claude-opus-4-8", "claude-opus-4-7",
    "claude-sonnet-4-6", "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001",
}

def _estimate_cost(model: str, input_tok: int, output_tok: int) -> float:
    prices = {
        "claude-opus-4-8":          (0.000015, 0.000075),
        "claude-sonnet-4-6":        (0.000003, 0.000015),
        "claude-haiku-4-5-20251001":(0.0000008, 0.000004),
    }
    p = prices.get(model, (0.000003, 0.000015))
    return round(input_tok * p[0] + output_tok * p[1], 8)


async def stream_anthropic(messages: list, soul: str, websocket: WebSocket, model: str = None) -> tuple[str, int, int]:
    import anthropic
    proxy = CLAUDE_PROXY_URL or (
        "http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else ""
    )
    if proxy:
        client = anthropic.AsyncAnthropic(
            api_key="proxy",
            base_url=proxy,
        )
    elif ANTHROPIC_API_KEY.startswith("sk-ant-oat"):
        client = anthropic.AsyncAnthropic(auth_token=ANTHROPIC_API_KEY)
    else:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    full = ""
    input_tok = output_tok = 0
    async with client.messages.stream(
        model=model or CLAUDE_MODEL,
        max_tokens=4096,
        system=soul,
        messages=messages,
    ) as stream:
        async for chunk in stream.text_stream:
            full += chunk
            await websocket.send_json({"type": "chunk", "content": chunk})
        try:
            usage = await stream.get_final_usage()
            input_tok  = usage.input_tokens
            output_tok = usage.output_tokens
        except Exception:
            pass
    return full, input_tok, output_tok

async def stream_ollama(messages: list, soul: str, websocket: WebSocket) -> tuple[str, int, int]:
    import httpx
    full = ""
    # Use native /api/chat endpoint with think:false to suppress qwen3 extended thinking
    all_messages = [{"role": "system", "content": soul}] + messages
    payload = {
        "model": CLAUDE_MODEL,
        "messages": all_messages,
        "stream": True,
        "think": False,
        "options": {"num_ctx": 8192},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk_data = json.loads(line)
                    # Native Ollama format: {"message": {"content": "..."}, "done": bool}
                    delta = chunk_data.get("message", {}).get("content", "")
                    if delta:
                        full += delta
                        await websocket.send_json({"type": "chunk", "content": delta})
                    if chunk_data.get("done"):
                        break
                except Exception:
                    pass
    # Estimate tokens for local models (free, but track volume)
    input_tok  = sum(_estimate_tokens(m.get("content", "")) for m in messages) + _estimate_tokens(soul)
    output_tok = _estimate_tokens(full)
    return full, input_tok, output_tok

# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="AdaDo Core", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    init_db()
    # Initialise memory/notes/reminders schema
    try:
        from memory import init_memory_schema
        init_memory_schema(DB_PATH)
    except Exception as e:
        print(f"Warning: memory schema init failed: {e}")
    # Initialise API router (pool + quota schema)
    try:
        from api_router import init_router
        init_router(DB_PATH)
        print("API router initialised")
    except Exception as e:
        print(f"Warning: api_router init failed: {e}")
    # Start reminder scheduler
    _start_reminder_scheduler()

# ─── Auth ─────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    name: str

class LoginRequest(BaseModel):
    email: str
    password: str

class OnboardRequest(BaseModel):
    role: str = ""
    use_cases: List[str] = []
    experience: str = "beginner"
    timezone: str = "UTC"
    goals: str = ""
    referral: str = ""

@app.post("/api/auth/signup")
async def signup(req: SignupRequest):
    db = get_db()
    trial_ends = int(time.time()) + 86400 * TRIAL_DAYS
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, name, created_at, tier, trial_ends_at) VALUES (?, ?, ?, ?, ?, ?)",
            (req.email.lower(), hash_password(req.password), req.name, int(time.time()), "cloud", trial_ends)
        )
        db.commit()
        user = db.execute("SELECT id FROM users WHERE email = ?", (req.email.lower(),)).fetchone()
        token = make_token(user["id"], req.email)
        return {"token": token, "name": req.name, "onboarding_complete": False}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Email already registered")
    finally:
        db.close()

CLI_TIERS = {"cli", "vps", "enterprise"}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (req.email.lower(),)
    ).fetchone()
    db.close()
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = make_token(user["id"], req.email)
    return {
        "token": token,
        "name": user["name"],
        "onboarding_complete": bool(user["onboarding_complete"]),
    }

@app.post("/api/auth/cli-login")
async def cli_login(req: LoginRequest):
    """Login endpoint for the ado CLI — CLI tier and above only."""
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (req.email.lower(),)
    ).fetchone()
    db.close()
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    if user["tier"] not in CLI_TIERS:
        raise HTTPException(403, "CLI access requires a CLI, VPS, or Enterprise plan. Upgrade at adado.diginoz.com.au")
    token = make_token(user["id"], req.email)
    return {
        "token": token,
        "name": user["name"],
        "tier": user["tier"],
        "onboarding_complete": bool(user["onboarding_complete"]),
    }

@app.post("/api/auth/refresh")
async def refresh_token(request: Request):
    """
    Silently rotate a JWT before it expires.
    Client should call this when token has < 7 days left.
    Returns a fresh 30-day token without requiring password re-entry.
    """
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
    db.close()
    if not user:
        raise HTTPException(401, "User not found")
    new_token = make_token(user["id"], user["email"])
    return {
        "token": new_token,
        "expires_in": 86400 * 30,
        "message": "Token refreshed",
    }

@app.get("/api/auth/me")
async def get_me(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
    db.close()
    if not user:
        raise HTTPException(404, "User not found")
    onboarding = json.loads(user["onboarding_data"] or "{}")
    trial_ends = user["trial_ends_at"]
    trial_active = bool(trial_ends and trial_ends > time.time())
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "tier": user["tier"],
        "trial_active": trial_active,
        "trial_ends_at": trial_ends,
        "trial_days_left": max(0, int((trial_ends - time.time()) / 86400)) if trial_ends else 0,
        "onboarding_complete": bool(user["onboarding_complete"]),
        "onboarding_data": onboarding,
        "token_exp": user_data.get("exp"),
        "token_days_left": max(0, int((user_data.get("exp", 0) - time.time()) / 86400)),
    }

@app.get("/api/auth/export")
async def export_data(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
    sessions = db.execute("SELECT * FROM sessions WHERE user_id = ?", (user_data["sub"],)).fetchall()
    db.close()
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "export_version": "1",
        "user": {
            "email": user["email"],
            "name": user["name"],
            "tier": user["tier"],
            "created_at": user["created_at"],
            "onboarding_data": json.loads(user["onboarding_data"] or "{}"),
        },
        "sessions": [
            {"created_at": s["created_at"], "messages": json.loads(s["messages"])}
            for s in sessions
        ],
    }

@app.delete("/api/auth/me")
async def delete_account(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_data["sub"],))
    db.execute("DELETE FROM users WHERE id = ?", (user_data["sub"],))
    db.commit()
    db.close()
    return {"ok": True, "message": "Account and all data deleted."}

@app.post("/api/auth/onboard")
async def onboard(req: OnboardRequest, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    data = {
        "role": req.role,
        "use_cases": req.use_cases,
        "experience": req.experience,
        "timezone": req.timezone,
        "goals": req.goals,
        "referral": req.referral,
    }
    db = get_db()
    db.execute(
        "UPDATE users SET onboarding_complete = 1, onboarding_data = ? WHERE id = ?",
        (json.dumps(data), user_data["sub"])
    )
    db.commit()
    db.close()
    return {"ok": True}

# ─── Apps & Agents API ───────────────────────────────────────────────────────

@app.get("/api/apps")
async def get_apps():
    return load_apps()

@app.get("/api/apps/{app_id}")
async def get_app(app_id: str):
    for app in load_apps(include_core=True):
        if app.get("id") == app_id or app.get("slug") == app_id:
            return app
    raise HTTPException(404, "App not found")

@app.get("/api/apps/registry/all")
async def get_app_registry():
    """Returns all apps including agent metadata for the frontend registry."""
    return load_apps(include_core=True)

@app.post("/api/apps/install-interests")
async def install_interests(request: Request):
    """Save the user's selected interests and mark onboarding complete."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    interests = body.get("interests", [])
    if not isinstance(interests, list):
        raise HTTPException(400, "interests must be a list")
    import json as _json
    uid = user_data["sub"]
    db = get_db()
    db.execute(
        "UPDATE users SET interests = ?, onboarding_complete = 1 WHERE id = ?",
        (_json.dumps(interests), uid)
    )
    db.commit()
    db.close()
    return {"ok": True, "interests": interests}

@app.get("/api/agents")
async def get_agents():
    agents = load_agents()
    result = []
    for name, content in sorted(agents.items()):
        lines = [l for l in content.splitlines() if l.strip()]
        description = ""
        for line in lines:
            stripped = line.lstrip("#").strip()
            if stripped and not stripped.startswith("---") and len(stripped) > 10:
                description = stripped
                break
        result.append({"name": name, "description": description})
    return result

@app.get("/api/status")
async def status():
    agents = load_agents()
    return {
        "backend": "anthropic" if USE_ANTHROPIC else "ollama",
        "model": CLAUDE_MODEL,
        "instance": INSTANCE_NAME,
        "version": "0.5.0",
        "agents_loaded": len(agents),
    }

@app.get("/api/health")
async def health():
    return {"status": "ok"}

# ─── Reminder scheduler ──────────────────────────────────────────────────────

_reminder_task: Optional[object] = None

def _start_reminder_scheduler():
    """Start background task that fires pending reminders every 30s."""
    import asyncio

    async def _check_reminders():
        while True:
            try:
                from memory import fire_due_reminders
                fired = fire_due_reminders(DB_PATH)
                for r in fired:
                    user_id = r["user_id"]
                    payload = {
                        "type": "reminder",
                        "label": r["label"],
                        "message": r["message"]
                    }
                    # Deliver via active WebSocket (best effort)
                    if user_id in _active_connections:
                        for ws in list(_active_connections.get(user_id, set())):
                            try:
                                await ws.send_json(payload)
                            except Exception:
                                pass
                    # Deliver via Expo push notifications (mobile)
                    try:
                        await _send_expo_push(user_id, r["label"], r["message"])
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(30)

    async def _send_expo_push(user_id: int, title: str, body: str):
        """Send Expo push notification to all registered tokens for a user."""
        import httpx
        db = get_db()
        rows = db.execute("SELECT token FROM push_tokens WHERE user_id = ?", (user_id,)).fetchall()
        db.close()
        if not rows:
            return
        expo_url = os.getenv("EXPO_PUSH_TOKEN_BASE_URL", "https://exp.host/--/api/v2/push/send")
        messages = [
            {"to": r["token"], "title": title, "body": body, "sound": "default"}
            for r in rows
        ]
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(expo_url, json=messages)

    async def _run_cron_jobs():
        """Check and execute due cron jobs every 60s."""
        while True:
            await asyncio.sleep(60)
            try:
                import croniter as _cron
                db = get_db()
                rows = db.execute(
                    "SELECT id, user_id, name, schedule, prompt, timezone, last_run FROM cron_jobs WHERE enabled = 1"
                ).fetchall()
                db.close()
                now = time.time()
                for r in rows:
                    try:
                        c = _cron.croniter(r["schedule"])
                        last = r["last_run"] or (now - 120)
                        prev = c.get_prev(float, now)
                        if prev > last:
                            db = get_db()
                            db.execute("UPDATE cron_jobs SET last_run = ? WHERE id = ?", (now, r["id"]))
                            db.commit()
                            db.close()
                            asyncio.get_event_loop().create_task(
                                _run_webhook_turn(r["user_id"], r["prompt"], f"cron:{r['name']}")
                            )
                    except Exception:
                        pass
            except ImportError:
                pass  # croniter not installed — cron jobs disabled
            except Exception:
                pass

    async def _archive_idle_sessions():
        """Archive sessions with no message in 24h — runs every hour."""
        while True:
            await asyncio.sleep(3600)
            try:
                cutoff = int(time.time()) - 86400
                db = get_db()
                db.execute(
                    "UPDATE sessions SET status = 'archived' WHERE (status IS NULL OR status = 'active') AND updated_at < ?",
                    (cutoff,)
                )
                db.commit()
                db.close()
            except Exception:
                pass

    _dream_last_run: dict = {}

    async def _memory_dream_scheduler():
        """Run memory consolidation at 03:00 UTC nightly for all users."""
        while True:
            await asyncio.sleep(300)  # check every 5 min
            try:
                import datetime as _dt
                now_utc = _dt.datetime.utcnow()
                if now_utc.hour == 3 and now_utc.minute < 5:
                    today_key = now_utc.strftime("%Y-%m-%d")
                    if _dream_last_run.get("date") != today_key:
                        _dream_last_run["date"] = today_key
                        db = get_db()
                        user_ids = [r["id"] for r in db.execute("SELECT id FROM users").fetchall()]
                        db.close()
                        for uid in user_ids:
                            try:
                                await _run_memory_dream(uid)
                            except Exception:
                                pass
            except Exception:
                pass

    loop = asyncio.get_event_loop()
    loop.create_task(_check_reminders())
    loop.create_task(_run_cron_jobs())
    loop.create_task(_archive_idle_sessions())
    loop.create_task(_memory_dream_scheduler())

async def _send_expo_push(user_id: int, title: str, body: str):
    """Send Expo push notification to all registered tokens for a user."""
    import httpx as _httpx
    db = get_db()
    rows = db.execute("SELECT token FROM push_tokens WHERE user_id = ?", (user_id,)).fetchall()
    db.close()
    if not rows:
        return
    expo_url = os.getenv("EXPO_PUSH_TOKEN_BASE_URL", "https://exp.host/--/api/v2/push/send")
    messages = [{"to": r["token"], "title": title, "body": body, "sound": "default"} for r in rows]
    async with _httpx.AsyncClient(timeout=10.0) as client:
        await client.post(expo_url, json=messages)


async def _run_memory_dream(user_id: int) -> dict:
    """
    Consolidate a user's memory entries — deduplicate and distill key facts.
    Returns {"consolidated": N, "original": N, "skipped": bool}.
    """
    import anthropic as ant_lib
    db = get_db()
    rows = db.execute(
        "SELECT key, value, category FROM user_memory WHERE user_id = ?", (user_id,)
    ).fetchall()
    db.close()
    if len(rows) < 5:
        return {"skipped": True, "reason": "fewer than 5 entries", "original": len(rows)}

    entries = "\n".join(f'[{r["category"] or "general"}] {r["key"]}: {r["value"]}' for r in rows)
    prompt = (
        "You are a memory consolidation engine. The following are a user's raw memory entries. "
        "Deduplicate, merge related facts, and distil them into a clean, non-redundant list.\n\n"
        "Rules:\n"
        "- Merge entries that say the same thing differently\n"
        "- Keep distinct facts separate\n"
        "- Output ONLY valid JSON: a list of objects with keys: key, value, category\n"
        "- category must be one of: user, work, preferences, facts, misc\n"
        "- Do not invent new facts\n\n"
        f"Memory entries:\n{entries}\n\n"
        "Output JSON array only, no prose:"
    )

    proxy = CLAUDE_PROXY_URL or ("http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else "")
    client = ant_lib.AsyncAnthropic(
        api_key="proxy" if proxy else ANTHROPIC_API_KEY,
        base_url=proxy if proxy else None
    )
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system="You output only JSON arrays. No explanations, no markdown.",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip() if resp.content else "[]"

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        consolidated = json.loads(raw)
        if not isinstance(consolidated, list):
            return {"skipped": True, "reason": "bad AI output", "original": len(rows)}
    except Exception:
        return {"skipped": True, "reason": "JSON parse error", "original": len(rows)}

    now = int(time.time())
    db = get_db()
    db.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
    for entry in consolidated:
        k = str(entry.get("key", "")).strip()
        v = str(entry.get("value", "")).strip()
        cat = str(entry.get("category", "misc")).strip()
        if k and v:
            db.execute(
                "INSERT OR REPLACE INTO user_memory (user_id, key, value, category, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, k, v, cat, now, now)
            )
    db.commit()
    db.close()
    return {"consolidated": len(consolidated), "original": len(rows), "skipped": False}


# Track active WebSocket connections per user for reminder delivery
_active_connections: dict = {}


# ─── WebSocket chat ───────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    import asyncio
    import anthropic as ant_lib
    await websocket.accept()

    # Auth: URL token (compat) or first-frame auth
    token_str = websocket.query_params.get("token")
    user_data = None

    if token_str:
        user_data = verify_token(token_str)
        if not user_data:
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return
    else:
        try:
            auth_frame = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return
        if auth_frame.get("type") != "auth":
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return
        token_str = auth_frame.get("token", "")
        user_data = verify_token(token_str) if token_str else None
        if not user_data:
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return

    uid = user_data["sub"] if user_data else None

    # Register connection for reminder delivery
    if uid:
        if uid not in _active_connections:
            _active_connections[uid] = set()
        _active_connections[uid].add(websocket)

    user_name = INSTANCE_NAME
    soul = DEFAULT_SOUL
    onboarding_complete = True
    history = []
    active_model = CLAUDE_MODEL
    user_row_cache = None
    user_timezone = "UTC"

    db = get_db()
    session_id = None
    if user_data:
        user_row_cache = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if user_row_cache:
            user_name = user_row_cache["name"]
            onboarding_complete = bool(user_row_cache["onboarding_complete"])
            active_model = resolve_model(user_row_cache)
            if onboarding_complete:
                onboarding = json.loads(user_row_cache["onboarding_data"] or "{}")
                user_timezone = onboarding.get("timezone", "UTC")
                soul = build_soul(user_name, onboarding)
            else:
                onboarding = {}
            session = db.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND (status IS NULL OR status = 'active') ORDER BY updated_at DESC LIMIT 1",
                (uid,)
            ).fetchone()
            if session:
                session_id = session["id"]
                history = json.loads(session["messages"])[-40:]
    db.close()

    # Inject persistent memory into soul
    if uid:
        try:
            from memory import get_user_memory_block, get_pending_reminders
            mem_block = get_user_memory_block(uid, DB_PATH)
            reminders_block = get_pending_reminders(uid, DB_PATH)
            if mem_block:
                soul = soul + mem_block
            if reminders_block:
                soul = soul + "\n\n## Upcoming Reminders\n" + reminders_block
        except Exception:
            pass

    # Per-app mode: if ?agent= param is present, pin the soul to that agent
    requested_agent = websocket.query_params.get("agent", "").strip()
    is_pinned_agent  = bool(requested_agent)
    if is_pinned_agent and onboarding_complete and user_row_cache:
        _ob_data = json.loads(user_row_cache["onboarding_data"] or "{}")
        soul = build_pinned_agent_soul(user_name, _ob_data, requested_agent)

    await websocket.send_json({
        "type":  "ready",
        "name":  user_name,
        "model": active_model,
        "user":  user_name,
        "onboarding_complete": onboarding_complete,
        "agent": requested_agent if is_pinned_agent else None,
        "tools_enabled": USE_ANTHROPIC,
    })

    # Build Anthropic client via API router (pool + BYOK + quota)
    ant_client = None
    _router_account_id = None  # tracks which pool account is active this session
    if USE_ANTHROPIC:
        try:
            from api_router import get_client_with_retry, QuotaExceededError, NoAccountAvailableError
            _router_available = True
        except ImportError:
            _router_available = False
        if not _router_available:
            # Fallback: direct key from env
            proxy = CLAUDE_PROXY_URL or (
                "http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else ""
            )
            if proxy:
                ant_client = ant_lib.AsyncAnthropic(api_key="proxy", base_url=proxy)
            elif ANTHROPIC_API_KEY.startswith("sk-ant-oat"):
                ant_client = ant_lib.AsyncAnthropic(auth_token=ANTHROPIC_API_KEY)
            else:
                ant_client = ant_lib.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    user_context = {
        "tier": user_row_cache["tier"] if user_row_cache else "cloud",
        "timezone": user_timezone,
        "user_id": uid,
    }

    try:
        while True:
            data = await websocket.receive_json()
            user_msg = data.get("content", "").strip()
            if not user_msg:
                continue

            history.append({"role": "user", "content": user_msg})

            # Rate limiting (requests per minute)
            if not check_rate_limit(uid):
                await websocket.send_json({"type": "error", "content": "Rate limit exceeded — please wait a moment."})
                history.pop()
                continue

            # Burn rate pre-flight
            if uid:
                db = get_db()
                burn = check_burn_rate(db, uid)
                db.close()
                if burn["over_daily"]:
                    await websocket.send_json({"type": "error", "content": f"Daily spend limit reached (${burn['daily_limit']:.2f})."})
                    history.pop()
                    continue
                if burn["over_monthly"]:
                    await websocket.send_json({"type": "error", "content": f"Monthly spend limit reached (${burn['monthly_limit']:.2f})."})
                    history.pop()
                    continue

            # Resolve agent + soul for this message
            if is_pinned_agent:
                active_agent = requested_agent
                active_soul  = soul
            else:
                active_agent = route_agent(user_msg)
                onboarding_data = {}
                if uid:
                    db = get_db()
                    row = db.execute("SELECT onboarding_data FROM users WHERE id = ?", (uid,)).fetchone()
                    db.close()
                    if row:
                        onboarding_data = json.loads(row["onboarding_data"] or "{}")
                active_soul = build_soul_with_agent(user_name, onboarding_data, active_agent)
                # Re-inject memory into dynamic soul
                if uid:
                    try:
                        from memory import get_user_memory_block
                        mem_block = get_user_memory_block(uid, DB_PATH)
                        if mem_block:
                            active_soul = active_soul + mem_block
                    except Exception:
                        pass
            # Inject standing orders into every turn
            if uid:
                active_soul = active_soul + get_standing_orders_block(uid)

            # Typing indicator — client shows "Ada is typing…"
            await websocket.send_json({"type": "typing", "agent": active_agent})
            await websocket.send_json({"type": "start", "agent": active_agent})

            try:
                context = _trim_context(history, active_soul)

                async def _send(msg):
                    await websocket.send_json(msg)

                if USE_ANTHROPIC:
                    from agent_loop import run_agent_loop
                    # Route via API router (BYOK / pool / quota)
                    _effective_client = ant_client  # fallback if router unavailable
                    if _router_available and uid:
                        try:
                            from api_router import get_client_with_retry, QuotaExceededError, NoAccountAvailableError, record_usage
                            route = await get_client_with_retry(
                                user_id=uid,
                                estimated_tokens=1500,
                                max_wait=30.0,
                                send_fn=_send,
                            )
                            _effective_client = route.client
                            _router_account_id = route.account_id
                            if route.quota_warn:
                                await websocket.send_json({
                                    "type": "system",
                                    "message": f"⚠ You've used {route.quota_pct:.0f}% of today's token quota."
                                })
                        except QuotaExceededError as qe:
                            await websocket.send_json({"type": "error", "content": str(qe)})
                            history.pop()
                            continue
                        except NoAccountAvailableError:
                            await websocket.send_json({"type": "error", "content": "Ada is very busy right now. Please try again in a moment."})
                            history.pop()
                            continue

                    full_response, input_tok, output_tok = await run_agent_loop(
                        messages=context,
                        soul=active_soul,
                        model=active_model,
                        client=_effective_client,
                        user_id=uid or 0,
                        user_context=user_context,
                        send_fn=_send,
                        tools_enabled=True,
                    )
                    # Record usage against pool account + user quota
                    if _router_available and uid and input_tok:
                        try:
                            record_usage(_router_account_id, uid, input_tok, output_tok)
                        except Exception:
                            pass
                else:
                    from agent_loop import run_agent_loop_ollama
                    full_response, input_tok, output_tok = await run_agent_loop_ollama(
                        messages=context,
                        soul=active_soul,
                        model=CLAUDE_MODEL,
                        ollama_url=OLLAMA_URL,
                        send_fn=_send,
                    )
            except Exception as e:
                await websocket.send_json({"type": "error", "content": f"AI error: {e}"})
                history.pop()
                continue

            history.append({"role": "assistant", "content": full_response})
            await websocket.send_json({"type": "done", "agent": active_agent})

            # Persist session
            if uid:
                db = get_db()
                now = int(time.time())
                if session_id:
                    db.execute(
                        "UPDATE sessions SET messages = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(history[-60:]), now, session_id)
                    )
                else:
                    cur = db.execute(
                        "INSERT INTO sessions (user_id, messages, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (uid, json.dumps(history), now, now)
                    )
                    session_id = cur.lastrowid
                log_task(db, uid, active_model, input_tok, output_tok)
                db.commit()
                db.close()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
    finally:
        # Deregister connection
        if uid and uid in _active_connections:
            _active_connections[uid].discard(websocket)

# ─── REST chat endpoint (for CLI + non-WS clients) ───────────────────────────

class ChatRequest(BaseModel):
    content: str
    session_id: Optional[int] = None
    agent: Optional[str] = None
    thinking: bool = False
    thinking_budget: int = 8000

class ChatResponse(BaseModel):
    response: str
    agent: str
    session_id: int
    input_tokens: int
    output_tokens: int

@app.post("/api/chat")
async def chat_rest(req: ChatRequest, request: Request):
    """
    Non-streaming REST chat endpoint. Simpler for CLI and background tasks.
    For streaming, use the WebSocket /ws/chat endpoint.
    """
    import anthropic as ant_lib
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    uid = user_data["sub"]
    db = get_db()
    user_row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user_row:
        db.close()
        raise HTTPException(404, "User not found")

    onboarding = json.loads(user_row["onboarding_data"] or "{}")
    user_name = user_row["name"]
    user_timezone = onboarding.get("timezone", "UTC")
    active_model = resolve_model(user_row)

    # Load or create session (active sessions only)
    session_id = req.session_id
    history = []
    if session_id:
        session = db.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ? AND (status IS NULL OR status = 'active')",
            (session_id, uid)
        ).fetchone()
        if session:
            history = json.loads(session["messages"])[-40:]
    else:
        session = db.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND (status IS NULL OR status = 'active') ORDER BY updated_at DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if session:
            session_id = session["id"]
            history = json.loads(session["messages"])[-40:]
    db.close()

    # Build soul + memory
    soul = build_soul(user_name, onboarding)
    try:
        from memory import get_user_memory_block
        mem = get_user_memory_block(uid, DB_PATH)
        if mem:
            soul += mem
    except Exception:
        pass

    # Route agent
    agent_name = req.agent or route_agent(req.content)
    soul = build_soul_with_agent(user_name, onboarding, agent_name)
    soul += get_standing_orders_block(uid)

    history.append({"role": "user", "content": req.content})
    context = _trim_context(history, soul)

    collected_chunks = []
    async def _collect(msg):
        if msg.get("type") == "chunk":
            collected_chunks.append(msg.get("content", ""))

    user_context = {"tier": user_row["tier"] or "cloud", "timezone": user_timezone, "user_id": uid}

    thinking_blocks = []
    try:
        if USE_ANTHROPIC and req.thinking:
            # Extended thinking mode — call Anthropic directly, no tool loop
            proxy = CLAUDE_PROXY_URL or ("http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else "")
            client = ant_lib.AsyncAnthropic(
                api_key="proxy" if proxy else ANTHROPIC_API_KEY,
                base_url=proxy if proxy else None
            )
            think_model = active_model if active_model in TOOL_CAPABLE_MODELS else "claude-sonnet-4-6"
            budget = max(1024, min(req.thinking_budget, 32000))
            resp = await client.messages.create(
                model=think_model,
                max_tokens=budget + 4096,
                system=soul,
                messages=context,
                thinking={"type": "enabled", "budget_tokens": budget},
            )
            full = ""
            for block in resp.content:
                if block.type == "thinking":
                    thinking_blocks.append({"type": "thinking", "thinking": block.thinking})
                elif block.type == "text":
                    full += block.text
            input_tok  = resp.usage.input_tokens
            output_tok = resp.usage.output_tokens
        elif USE_ANTHROPIC:
            proxy = CLAUDE_PROXY_URL or ("http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else "")
            if proxy:
                client = ant_lib.AsyncAnthropic(api_key="proxy", base_url=proxy)
            else:
                client = ant_lib.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            from agent_loop import run_agent_loop
            full, input_tok, output_tok = await run_agent_loop(
                messages=context, soul=soul, model=active_model,
                client=client, user_id=uid, user_context=user_context,
                send_fn=_collect, tools_enabled=True
            )
        else:
            from agent_loop import run_agent_loop_ollama
            full, input_tok, output_tok = await run_agent_loop_ollama(
                messages=context, soul=soul, model=CLAUDE_MODEL,
                ollama_url=OLLAMA_URL, send_fn=_collect
            )
    except Exception as e:
        raise HTTPException(500, f"AI error: {e}")

    history.append({"role": "assistant", "content": full})

    db = get_db()
    now = int(time.time())
    if session_id:
        db.execute("UPDATE sessions SET messages = ?, updated_at = ? WHERE id = ?",
                   (json.dumps(history[-60:]), now, session_id))
    else:
        cur = db.execute("INSERT INTO sessions (user_id, messages, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (uid, json.dumps(history), now, now))
        session_id = cur.lastrowid
    log_task(db, uid, active_model, input_tok, output_tok)
    db.commit()
    db.close()

    result = {"response": full, "agent": agent_name, "session_id": session_id,
              "input_tokens": input_tok, "output_tokens": output_tok}
    if thinking_blocks:
        result["thinking"] = thinking_blocks
    return result


# ─── Session management API ───────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    rows = db.execute(
        "SELECT id, created_at, updated_at, COALESCE(status, 'active') as status, substr(messages, 1, 200) as preview FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 20",
        (uid,)
    ).fetchall()
    db.close()
    sessions = []
    for r in rows:
        try:
            msgs = json.loads(r["preview"] + '"]' if not r["preview"].endswith("]") else r["preview"])
            first_msg = next((m.get("content", "")[:80] for m in msgs if m.get("role") == "user"), "")
        except Exception:
            first_msg = ""
        sessions.append({"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"], "status": r["status"], "preview": first_msg})
    return sessions

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    row = db.execute("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_data["sub"])).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Session not found")
    return {"id": row["id"], "messages": json.loads(row["messages"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}

@app.post("/api/sessions/new")
async def new_session(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    now = int(time.time())
    cur = db.execute("INSERT INTO sessions (user_id, messages, created_at, updated_at) VALUES (?, '[]', ?, ?)",
                     (user_data["sub"], now, now))
    db.commit()
    session_id = cur.lastrowid
    db.close()
    return {"session_id": session_id}


# ─── Memory API ───────────────────────────────────────────────────────────────

@app.get("/api/memory")
async def get_memory(request: Request, q: str = ""):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT key, value, category, created_at, updated_at FROM user_memory WHERE user_id = ? AND (key LIKE ? OR value LIKE ?) ORDER BY updated_at DESC",
            (uid, f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT key, value, category, created_at, updated_at FROM user_memory WHERE user_id = ? ORDER BY category, updated_at DESC",
            (uid,)
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.delete("/api/memory/{key}")
async def delete_memory(key: str, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM user_memory WHERE user_id = ? AND key = ?", (user_data["sub"], key))
    db.commit()
    db.close()
    return {"ok": True}


# ─── Reminders API ────────────────────────────────────────────────────────────

@app.get("/api/reminders")
async def get_reminders(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    now = int(time.time())
    rows = db.execute(
        "SELECT id, label, message, fire_at, fired, fired_at FROM reminders WHERE user_id = ? AND fire_at > ? AND fired = 0 ORDER BY fire_at ASC",
        (uid, now)
    ).fetchall()
    db.close()
    from datetime import datetime, timezone
    return [{"id": r["id"], "label": r["label"], "message": r["message"],
             "fire_at": datetime.fromtimestamp(r["fire_at"], tz=timezone.utc).isoformat(),
             "fired": bool(r["fired"])} for r in rows]

@app.delete("/api/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}


# ─── Notes API ────────────────────────────────────────────────────────────────

@app.get("/api/notes")
async def get_notes(request: Request, q: str = ""):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT id, title, tags, created_at, updated_at, substr(content,1,100) as preview FROM notes WHERE user_id = ? AND (title LIKE ? OR content LIKE ?) ORDER BY updated_at DESC",
            (uid, f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, title, tags, created_at, updated_at, substr(content,1,100) as preview FROM notes WHERE user_id = ? ORDER BY updated_at DESC LIMIT 50",
            (uid,)
        ).fetchall()
    db.close()
    return [{"id": r["id"], "title": r["title"], "tags": json.loads(r["tags"] or "[]"),
             "preview": r["preview"], "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]

@app.get("/api/notes/{note_id}")
async def get_note(note_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, user_data["sub"])).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Note not found")
    return {"id": row["id"], "title": row["title"], "content": row["content"],
            "tags": json.loads(row["tags"] or "[]"), "created_at": row["created_at"], "updated_at": row["updated_at"]}

@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}


# ─── Push notification registration ──────────────────────────────────────────

class PushTokenRequest(BaseModel):
    token: str
    platform: str  # ios | android | web

@app.post("/api/push/register")
async def register_push(req: PushTokenRequest, request: Request):
    """Register a push notification token for mobile app."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    now = int(time.time())
    db.execute(
        "INSERT INTO push_tokens (user_id, token, platform, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, token) DO UPDATE SET platform = excluded.platform, created_at = excluded.created_at",
        (uid, req.token, req.platform, now)
    )
    db.commit()
    db.close()
    return {"ok": True}


# ─── Voice endpoints (local Piper TTS / faster-whisper STT) ──────────────────

TTS_SERVICE_URL = os.getenv("TTS_URL", "http://host.docker.internal:8085")
STT_SERVICE_URL = os.getenv("STT_URL", "http://host.docker.internal:8086")

# ─── ElevenLabs Conversational AI — live voice chat ──────────────────────────
# Architecture:
#   Client → POST /api/voice/session → AdaDo creates signed EL conversation URL
#   Client connects to that URL directly (ElevenLabs WebSocket)
#   ElevenLabs calls POST /api/voice/llm  (OpenAI-compat) for each AI turn
#   ElevenLabs handles: VAD, STT, turn detection, TTS streaming
#   AdaDo handles: auth, Claude inference, credit tracking

ELEVENLABS_API_KEY   = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID  = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_API_BASE  = "https://api.elevenlabs.io/v1"

# ─── Stripe ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID        = os.getenv("STRIPE_PRICE_ID", "")  # monthly subscription price

# ─── Fastmail ─────────────────────────────────────────────────────────────────
FASTMAIL_API_TOKEN  = os.getenv("FASTMAIL_API_TOKEN", "")
FASTMAIL_ACCOUNT_ID = os.getenv("FASTMAIL_ACCOUNT_ID", "")
FASTMAIL_DOMAIN     = os.getenv("FASTMAIL_DOMAIN", "adadoai.com")

def _el_headers():
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


@app.post("/api/voice/session")
async def voice_session(request: Request):
    """
    Issue a signed ElevenLabs conversation URL for the authenticated user.
    The client uses this URL to open a direct WebSocket to ElevenLabs.
    Credits come from the shared AdaDo ElevenLabs API key.
    """
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID:
        raise HTTPException(503, "Voice not configured — ELEVENLABS_API_KEY or ELEVENLABS_AGENT_ID missing")

    uid = user_data["sub"]
    db = get_db()
    row = db.execute("SELECT name, tier, onboarding_data FROM users WHERE id = ?", (uid,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "User not found")

    # Build per-user overrides passed to EL agent
    onboarding = json.loads(row["onboarding_data"] or "{}")
    tz = onboarding.get("timezone", "UTC")
    user_name = row["name"]

    # Dynamic variables injected into the EL agent's system prompt
    # These let EL's custom LLM call carry user context we'd otherwise lose
    dynamic_vars = {
        "user_id":   str(uid),
        "user_name": user_name,
        "timezone":  tz,
    }

    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{ELEVENLABS_API_BASE}/convai/conversation/get_signed_url",
            headers=_el_headers(),
            json={"agent_id": ELEVENLABS_AGENT_ID, "conversation_config_override": {
                "agent": {
                    "prompt": {
                        "prompt": _build_voice_soul(user_name, onboarding)
                    }
                }
            }},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"ElevenLabs error: {resp.text[:200]}")

    data = resp.json()
    signed_url = data.get("signed_url", "")

    # Log that a voice session was started (for credit tracking)
    db = get_db()
    db.execute(
        "INSERT INTO task_logs (user_id, model, input_tokens, output_tokens, cost_usd, task_type, created_at) "
        "VALUES (?, 'elevenlabs/convai', 0, 0, 0.0, 'voice_session_start', ?)",
        (uid, int(time.time()))
    )
    db.commit()
    db.close()

    return {
        "signed_url": signed_url,
        "agent_id": ELEVENLABS_AGENT_ID,
        "user_name": user_name,
        "dynamic_vars": dynamic_vars,
    }


def _build_voice_soul(name: str, onboarding: dict) -> str:
    """Condensed soul for voice — shorter than text chat, optimised for speech."""
    base = (
        f"You are Ada, an AI assistant for {name}. "
        "You are direct, warm, and capable. Keep answers concise — this is a voice conversation. "
        "No bullet points, no markdown, no code blocks. Speak naturally. "
        "Get to the point in one or two sentences unless depth is genuinely needed."
    )
    tz = onboarding.get("timezone", "")
    if tz:
        base += f" The user's timezone is {tz}."
    goals = onboarding.get("goals", "")
    if goals:
        base += f" Context: {goals[:200]}"
    return base


@app.post("/api/voice/llm")
async def voice_llm(request: Request):
    """
    OpenAI-compatible chat completion endpoint called by ElevenLabs custom LLM.
    ElevenLabs sends an OpenAI-format request; we respond with Claude, streaming.
    Auth: ElevenLabs signs with a shared secret header we verify.
    """
    # Verify this is from ElevenLabs (shared secret or IP — use secret header)
    el_secret = os.getenv("ELEVENLABS_LLM_SECRET", "")
    if el_secret:
        incoming = request.headers.get("x-eleven-secret", "")
        if incoming != el_secret:
            raise HTTPException(401, "Unauthorized")

    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", True)
    model_hint = body.get("model", "claude-sonnet-4-6")

    # Extract user_id from the system message dynamic vars EL injects
    uid = 0
    user_name = "User"
    soul = DEFAULT_SOUL
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            # EL injects dynamic vars as JSON in system — parse if present
            soul = content or DEFAULT_SOUL
            break

    # Filter to user/assistant turns only (Claude doesn't want system in messages list)
    chat_messages = [m for m in messages if m.get("role") in ("user", "assistant")]

    import anthropic as ant_lib
    from fastapi.responses import StreamingResponse

    proxy = CLAUDE_PROXY_URL or (
        "http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else ""
    )
    client = ant_lib.AsyncAnthropic(
        api_key="proxy" if proxy else ANTHROPIC_API_KEY,
        base_url=proxy if proxy else None,
    )

    async def _openai_compat_stream():
        """Yield OpenAI-format SSE chunks wrapping Claude streaming."""
        import json as _json, time as _time
        chunk_id = f"chatcmpl-voice-{int(_time.time())}"
        model_name = "claude-sonnet-4-6"

        # Opening chunk
        yield f"data: {_json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

        full_text = ""
        input_tok = 0
        output_tok = 0
        try:
            async with client.messages.stream(
                model=CLAUDE_MODEL if CLAUDE_MODEL in TOOL_CAPABLE_MODELS else "claude-sonnet-4-6",
                max_tokens=512,   # voice responses must be short
                system=soul,
                messages=chat_messages,
            ) as stream_:
                async for event in stream_:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        delta = event.delta.text
                        full_text += delta
                        yield f"data: {_json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': delta}, 'finish_reason': None}]})}\n\n"
                final = await stream_.get_final_message()
                input_tok = final.usage.input_tokens
                output_tok = final.usage.output_tokens
        except Exception as e:
            err = f"Sorry, I had a technical issue: {str(e)[:60]}"
            yield f"data: {_json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': err}, 'finish_reason': None}]})}\n\n"
            full_text = err

        # Done chunk
        yield f"data: {_json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

        # Track usage (best-effort, uid may be 0 for unauthenticated EL calls)
        if uid:
            try:
                _cost = _estimate_cost(model_name, input_tok, output_tok)
                db = get_db()
                log_task(db, uid, model_name, input_tok, output_tok)
                db.commit()
                db.close()
            except Exception:
                pass

    if stream:
        return StreamingResponse(_openai_compat_stream(), media_type="text/event-stream")

    # Non-streaming fallback (EL shouldn't need this but just in case)
    resp = await client.messages.create(
        model=CLAUDE_MODEL if CLAUDE_MODEL in TOOL_CAPABLE_MODELS else "claude-sonnet-4-6",
        max_tokens=512,
        system=soul,
        messages=chat_messages,
    )
    text = resp.content[0].text if resp.content else ""
    return {
        "id": f"chatcmpl-voice-{int(time.time())}",
        "object": "chat.completion",
        "model": "claude-sonnet-4-6",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens},
    }


@app.post("/api/voice/end")
async def voice_end(request: Request):
    """
    Log end of a voice conversation with duration/cost for credit tracking.
    Client calls this when the ElevenLabs WebSocket closes.
    """
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    conversation_id = body.get("conversation_id", "")
    duration_secs = body.get("duration_secs", 0)

    # Rough EL cost: ~$0.11/min for Starter tier
    el_cost_per_min = float(os.getenv("ELEVENLABS_COST_PER_MIN", "0.11"))
    cost = round((duration_secs / 60) * el_cost_per_min, 6)

    db = get_db()
    db.execute(
        "INSERT INTO task_logs (user_id, model, input_tokens, output_tokens, cost_usd, task_type, created_at) "
        "VALUES (?, 'elevenlabs/convai', ?, 0, ?, 'voice_session_end', ?)",
        (user_data["sub"], int(duration_secs * 10), cost, int(time.time()))
    )
    db.commit()
    db.close()
    return {"ok": True, "cost_usd": cost, "duration_secs": duration_secs}


@app.get("/api/voice/usage")
async def voice_usage(request: Request):
    """Voice credit usage for this user."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    since = int(time.time()) - 86400 * 30
    db = get_db()
    rows = db.execute(
        "SELECT task_type, SUM(cost_usd) as cost, COUNT(*) as n FROM task_logs "
        "WHERE user_id = ? AND task_type LIKE 'voice%' AND created_at > ? GROUP BY task_type",
        (uid, since)
    ).fetchall()
    db.close()
    return {"monthly_voice": [dict(r) for r in rows]}

@app.post("/api/voice/tts")
async def voice_tts(request: Request):
    """Proxy TTS synthesis — clients call this instead of TTS service directly."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "No text provided")
    import httpx
    from fastapi.responses import Response
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{TTS_SERVICE_URL}/synthesize", params={"text": text})
        if resp.status_code == 200:
            return Response(content=resp.content, media_type="audio/wav")
        raise HTTPException(502, "TTS service error")

@app.post("/api/voice/stt")
async def voice_stt(request: Request):
    """Proxy STT transcription — clients upload audio here."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    import httpx
    form = await request.form()
    audio = form.get("file")
    if not audio:
        raise HTTPException(400, "No audio file provided")
    content = await audio.read()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{STT_SERVICE_URL}/transcribe",
            files={"file": (audio.filename, content, audio.content_type or "audio/wav")},
        )
        if resp.status_code == 200:
            return resp.json()
        raise HTTPException(502, "STT service error")


# ─── Usage & burn rate API ────────────────────────────────────────────────────

@app.get("/api/usage/summary")
async def usage_summary(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    burn = check_burn_rate(db, user_data["sub"])

    now = int(time.time())
    today_start = now - 86400
    month_start = now - 86400 * 30

    daily_rows = db.execute(
        "SELECT model, SUM(input_tokens) as i, SUM(output_tokens) as o, SUM(cost_usd) as c, COUNT(*) as n "
        "FROM task_logs WHERE user_id = ? AND created_at > ? GROUP BY model",
        (user_data["sub"], today_start)
    ).fetchall()
    monthly_total = db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) as c, COALESCE(SUM(input_tokens+output_tokens), 0) as t, COUNT(*) as n "
        "FROM task_logs WHERE user_id = ? AND created_at > ?",
        (user_data["sub"], month_start)
    ).fetchone()
    db.close()

    return {
        **burn,
        "today": [dict(r) for r in daily_rows],
        "month": {
            "cost_usd": round(monthly_total["c"], 6),
            "total_tokens": monthly_total["t"],
            "requests": monthly_total["n"],
        }
    }

@app.get("/api/usage/history")
async def usage_history(request: Request, days: int = 7):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    since = int(time.time()) - 86400 * min(days, 90)
    db = get_db()
    rows = db.execute(
        "SELECT created_at, model, input_tokens, output_tokens, cost_usd, task_type "
        "FROM task_logs WHERE user_id = ? AND created_at > ? ORDER BY created_at DESC LIMIT 500",
        (user_data["sub"], since)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

class BurnLimitRequest(BaseModel):
    daily_limit_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None

@app.put("/api/usage/limits")
async def set_burn_limits(req: BurnLimitRequest, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute(
        "INSERT INTO burn_limits (user_id, daily_limit_usd, monthly_limit_usd, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
        "daily_limit_usd = excluded.daily_limit_usd, "
        "monthly_limit_usd = excluded.monthly_limit_usd, "
        "updated_at = excluded.updated_at",
        (user_data["sub"], req.daily_limit_usd, req.monthly_limit_usd, int(time.time()))
    )
    db.commit()
    db.close()
    return {"ok": True, "daily_limit_usd": req.daily_limit_usd, "monthly_limit_usd": req.monthly_limit_usd}

# ─── API Router: BYOK + Quota endpoints ──────────────────────────────────────

@app.get("/api/usage/quota")
async def get_quota(request: Request):
    """Return current user's token quota and usage."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    try:
        from api_router import get_user_quota_status, get_user_byok
        quota = get_user_quota_status(user_data["sub"])
        has_byok = get_user_byok(user_data["sub"]) is not None
        return {**quota, "has_byok": has_byok}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/settings/byok")
async def set_byok(request: Request):
    """Store user's own Anthropic API key (BYOK)."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(400, "api_key required")
    try:
        from api_router import set_user_byok
        set_user_byok(user_data["sub"], api_key)
        return {"ok": True, "message": "API key saved — your calls now use your own account."}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/settings/byok")
async def delete_byok(request: Request):
    """Remove user's BYOK — fall back to shared pool."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    from api_router import delete_user_byok
    deleted = delete_user_byok(user_data["sub"])
    return {"ok": True, "removed": deleted}


@app.get("/api/admin/pool")
async def pool_status(request: Request):
    """Admin: pool health and account usage (requires admin tier)."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    row = db.execute("SELECT tier FROM users WHERE id=?", (user_data["sub"],)).fetchone()
    db.close()
    if not row or row["tier"] not in ("admin", "vps"):
        raise HTTPException(403, "Admin only")
    from api_router import pool_health
    return pool_health()


@app.post("/api/admin/pool/account")
async def add_pool_account(request: Request):
    """Admin: add a new API account to the pool."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    row = db.execute("SELECT tier FROM users WHERE id=?", (user_data["sub"],)).fetchone()
    db.close()
    if not row or row["tier"] != "admin":
        raise HTTPException(403, "Admin only")
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    name = body.get("name", "").strip()
    tier = int(body.get("tier", 1))
    if not api_key or not name:
        raise HTTPException(400, "api_key and name required")
    from api_router import add_account
    account_id = add_account(name, api_key, tier, body.get("notes", ""))
    return {"ok": True, "account_id": account_id}


@app.put("/api/admin/pool/account/{account_id}/tier")
async def update_account_tier_endpoint(account_id: int, request: Request):
    """Admin: update an account's Anthropic tier (adjusts rate limits)."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    row = db.execute("SELECT tier FROM users WHERE id=?", (user_data["sub"],)).fetchone()
    db.close()
    if not row or row["tier"] != "admin":
        raise HTTPException(403, "Admin only")
    body = await request.json()
    tier = int(body.get("tier", 1))
    from api_router import update_account_tier
    update_account_tier(account_id, tier)
    return {"ok": True}


@app.get("/api/models")
async def list_models(request: Request):
    """Return models available to this user."""
    user_data = get_auth_user(request)
    tier = "cloud"
    if user_data:
        db = get_db()
        row = db.execute("SELECT tier, preferred_model FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
        db.close()
        if row:
            tier = row["tier"] or "cloud"
            current = row["preferred_model"] or CLAUDE_MODEL
        else:
            current = CLAUDE_MODEL
    else:
        current = CLAUDE_MODEL
    return {"models": ALLOWED_MODELS.get(tier, ALLOWED_MODELS["cloud"]), "current": current, "default": CLAUDE_MODEL}

@app.put("/api/me/model")
async def set_preferred_model(request: Request):
    """Set the user's preferred model."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    model = body.get("model", "").strip()
    db = get_db()
    row = db.execute("SELECT tier FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
    tier = row["tier"] if row else "cloud"
    allowed = ALLOWED_MODELS.get(tier, ALLOWED_MODELS["cloud"])
    if model not in allowed:
        db.close()
        raise HTTPException(400, f"Model not available on your tier. Allowed: {allowed}")
    db.execute("UPDATE users SET preferred_model = ? WHERE id = ?", (model, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True, "model": model}

# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process per-user request rate limiter (requests/minute)

import collections
_rate_buckets: dict = collections.defaultdict(list)
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "30"))

def check_rate_limit(user_id) -> bool:
    """Returns True if under limit, False if exceeded."""
    if not user_id:
        return True
    now = time.time()
    window = [t for t in _rate_buckets[user_id] if now - t < 60]
    _rate_buckets[user_id] = window
    if len(window) >= RATE_LIMIT_RPM:
        return False
    _rate_buckets[user_id].append(now)
    return True


# ─── Inbound webhooks ─────────────────────────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True

@app.post("/api/webhooks")
async def create_webhook(req: WebhookCreateRequest, request: Request):
    """Create a personal inbound webhook that triggers an agent turn."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    token = secrets.token_urlsafe(24)
    db = get_db()
    now = int(time.time())
    db.execute(
        "INSERT INTO webhooks (user_id, token, name, description, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, token, req.name, req.description, 1 if req.enabled else 0, now)
    )
    db.commit()
    db.close()
    return {"token": token, "url": f"/api/webhooks/trigger/{token}"}

@app.get("/api/webhooks")
async def list_webhooks(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    rows = db.execute(
        "SELECT id, token, name, description, enabled, created_at FROM webhooks WHERE user_id = ?",
        (user_data["sub"],)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM webhooks WHERE id = ? AND user_id = ?", (webhook_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}

@app.post("/api/webhooks/trigger/{token}")
async def trigger_webhook(token: str, request: Request):
    """External POST triggers an agent turn — no auth required (token IS the secret)."""
    db = get_db()
    row = db.execute(
        "SELECT user_id, name, description, enabled FROM webhooks WHERE token = ?", (token,)
    ).fetchone()
    db.close()
    if not row or not row["enabled"]:
        raise HTTPException(404, "Webhook not found")

    uid = row["user_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    prompt = body.get("message") or body.get("text") or body.get("content") or f"Webhook '{row['name']}' triggered."

    # Queue as a task (non-blocking) — runs agent loop and delivers via push
    import asyncio
    asyncio.get_event_loop().create_task(_run_webhook_turn(uid, prompt, row["name"]))
    return {"ok": True, "queued": True}

async def _run_webhook_turn(user_id: int, prompt: str, source: str):
    """Run a headless agent turn for a webhook trigger and push-deliver the response."""
    import anthropic as ant_lib
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    if not row:
        return

    onboarding = json.loads(row["onboarding_data"] or "{}")
    soul = build_soul(row["name"], onboarding) + f"\n\n[Triggered by webhook: {source}]"
    active_model = resolve_model(row)

    collected = []
    async def _collect(msg):
        if msg.get("type") == "chunk":
            collected.append(msg.get("content", ""))

    try:
        proxy = CLAUDE_PROXY_URL or (
            "http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else ""
        )
        client = ant_lib.AsyncAnthropic(
            api_key="proxy" if proxy else ANTHROPIC_API_KEY,
            base_url=proxy if proxy else None
        )
        from agent_loop import run_agent_loop
        full, _, _ = await run_agent_loop(
            messages=[{"role": "user", "content": prompt}],
            soul=soul, model=active_model, client=client,
            user_id=user_id, user_context={"tier": row["tier"] or "cloud", "timezone": "UTC", "user_id": user_id},
            send_fn=_collect, tools_enabled=True,
        )
    except Exception:
        full = "".join(collected) or "[webhook agent error]"

    # Deliver via push
    try:
        await _send_expo_push(user_id, f"Ada (webhook: {source})", full[:200])
    except Exception:
        pass

    # Deliver to any active WS connections
    if user_id in _active_connections:
        for ws in list(_active_connections.get(user_id, set())):
            try:
                await ws.send_json({"type": "webhook_response", "source": source, "content": full})
            except Exception:
                pass


# ─── Skills API ───────────────────────────────────────────────────────────────

@app.get("/api/skills")
async def list_skills(request: Request):
    """List available skills from the skills/ directory."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    skills_dir = Path(AGENTS_DIR).parent / "skills"
    skills = []
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                content = skill_file.read_text()
                # Parse YAML frontmatter
                meta = {}
                if content.startswith("---"):
                    lines = content.splitlines()
                    try:
                        end = lines.index("---", 1)
                        import yaml as _yaml
                        meta = _yaml.safe_load("\n".join(lines[1:end])) or {}
                    except Exception:
                        pass
                skills.append({
                    "id": skill_file.parent.name,
                    "name": meta.get("name", skill_file.parent.name),
                    "description": meta.get("description", ""),
                    "version": meta.get("version", "1.0.0"),
                    "tags": meta.get("tags", []),
                })
            except Exception:
                pass
    return skills

@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request):
    """Get a skill's content."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    skills_dir = Path(AGENTS_DIR).parent / "skills"
    skill_file = skills_dir / skill_id / "SKILL.md"
    if not skill_file.exists():
        raise HTTPException(404, "Skill not found")

    return {"id": skill_id, "content": skill_file.read_text()}

@app.post("/api/skills/{skill_id}/run")
async def run_skill(skill_id: str, request: Request):
    """Inject a skill's content into an agent turn as a prefilled prompt."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    skills_dir = Path(AGENTS_DIR).parent / "skills"
    skill_file = skills_dir / skill_id / "SKILL.md"
    if not skill_file.exists():
        raise HTTPException(404, "Skill not found")

    body = await request.json()
    args = body.get("args", "")
    content = skill_file.read_text()
    prompt = f"[Running skill: {skill_id}]\n\n{content}\n\nArgs: {args}" if args else f"[Running skill: {skill_id}]\n\n{content}"

    # Delegate to /api/chat
    from fastapi.testclient import TestClient
    return {"skill_id": skill_id, "prompt_preview": prompt[:200], "tip": "POST to /api/chat with this as content"}


# ─── Memory search API ────────────────────────────────────────────────────────

@app.get("/api/memory/search")
async def search_memory(request: Request, q: str = "", limit: int = 10):
    """Full-text search across user memory."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    rows = db.execute(
        "SELECT key, value, category, updated_at FROM user_memory "
        "WHERE user_id = ? AND (key LIKE ? OR value LIKE ?) ORDER BY updated_at DESC LIMIT ?",
        (uid, f"%{q}%", f"%{q}%", limit)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# ─── Memory dream endpoint ────────────────────────────────────────────────────

@app.post("/api/memory/dream")
async def memory_dream(request: Request):
    """Manually trigger memory consolidation (dreaming) for the authenticated user."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    if not USE_ANTHROPIC:
        raise HTTPException(503, "Memory dreaming requires Anthropic API")
    uid = user_data["sub"]
    result = await _run_memory_dream(uid)
    return result


# ─── Standing orders endpoints ────────────────────────────────────────────────

class StandingOrderRequest(BaseModel):
    instruction: str
    priority: int = 0
    enabled: bool = True

@app.post("/api/standing-orders")
async def create_standing_order(req: StandingOrderRequest, request: Request):
    """Create a persistent standing order that is injected into every agent turn."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO standing_orders (user_id, instruction, priority, enabled, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, req.instruction.strip(), req.priority, 1 if req.enabled else 0, now)
    )
    db.commit()
    order_id = cur.lastrowid
    db.close()
    return {"id": order_id, "instruction": req.instruction, "priority": req.priority, "enabled": req.enabled, "created_at": now}

@app.get("/api/standing-orders")
async def list_standing_orders(request: Request):
    """List all standing orders for the authenticated user."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    rows = db.execute(
        "SELECT id, instruction, priority, enabled, created_at FROM standing_orders WHERE user_id = ? ORDER BY priority DESC, id ASC",
        (user_data["sub"],)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.delete("/api/standing-orders/{order_id}")
async def delete_standing_order(order_id: int, request: Request):
    """Delete a standing order."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM standing_orders WHERE id = ? AND user_id = ?", (order_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}

@app.patch("/api/standing-orders/{order_id}")
async def update_standing_order(order_id: int, request: Request):
    """Toggle or update a standing order."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    db = get_db()
    updates, params = [], []
    for field in ["instruction", "priority", "enabled"]:
        if field in body:
            updates.append(f"{field} = ?")
            params.append(1 if (field == "enabled" and body[field]) else (0 if field == "enabled" else body[field]))
    if not updates:
        db.close()
        raise HTTPException(400, "Nothing to update")
    params += [order_id, user_data["sub"]]
    db.execute(f"UPDATE standing_orders SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
    db.commit()
    db.close()
    return {"ok": True}


# ─── Usage dashboard endpoint ─────────────────────────────────────────────────

@app.get("/api/usage/dashboard")
async def usage_dashboard(request: Request):
    """
    Rich usage dashboard: today/week/month costs, token counts,
    top models, burn rate (7-day avg), estimated monthly cost.
    """
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    now = int(time.time())

    day_start   = now - 86400
    week_start  = now - 86400 * 7
    month_start = now - 86400 * 30

    db = get_db()

    def _sum(since):
        r = db.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c, COALESCE(SUM(input_tokens),0) AS i, "
            "COALESCE(SUM(output_tokens),0) AS o, COUNT(*) AS n "
            "FROM task_logs WHERE user_id = ? AND created_at > ?",
            (uid, since)
        ).fetchone()
        return dict(r)

    today  = _sum(day_start)
    week   = _sum(week_start)
    month  = _sum(month_start)

    # Top models this month
    model_rows = db.execute(
        "SELECT model, SUM(cost_usd) AS cost, SUM(input_tokens+output_tokens) AS tokens, COUNT(*) AS requests "
        "FROM task_logs WHERE user_id = ? AND created_at > ? GROUP BY model ORDER BY cost DESC LIMIT 5",
        (uid, month_start)
    ).fetchall()

    # Burn rate: daily average over last 7 days
    daily_costs = db.execute(
        "SELECT CAST((created_at - ?) / 86400 AS INTEGER) AS day_bucket, SUM(cost_usd) AS daily_cost "
        "FROM task_logs WHERE user_id = ? AND created_at > ? GROUP BY day_bucket",
        (week_start, uid, week_start)
    ).fetchall()
    db.close()

    costs_list = [r["daily_cost"] for r in daily_costs]
    burn_rate_per_day = round(sum(costs_list) / max(len(costs_list), 1), 6)
    estimated_monthly = round(burn_rate_per_day * 30, 4)

    return {
        "today": {
            "cost_usd":      round(today["c"], 6),
            "input_tokens":  today["i"],
            "output_tokens": today["o"],
            "requests":      today["n"],
        },
        "week": {
            "cost_usd":      round(week["c"], 6),
            "input_tokens":  week["i"],
            "output_tokens": week["o"],
            "requests":      week["n"],
        },
        "month": {
            "cost_usd":      round(month["c"], 6),
            "input_tokens":  month["i"],
            "output_tokens": month["o"],
            "requests":      month["n"],
        },
        "burn_rate_usd_per_day":   burn_rate_per_day,
        "estimated_monthly_usd":   estimated_monthly,
        "top_models": [
            {
                "model":    r["model"],
                "cost_usd": round(r["cost"], 6),
                "tokens":   r["tokens"],
                "requests": r["requests"],
            }
            for r in model_rows
        ],
    }


# ─── Cron jobs API ────────────────────────────────────────────────────────────

class CronJobRequest(BaseModel):
    name: str
    schedule: str        # cron expression e.g. "0 9 * * 1"
    prompt: str          # agent prompt to run on schedule
    enabled: bool = True
    timezone: str = "UTC"

@app.post("/api/cron")
async def create_cron_job(req: CronJobRequest, request: Request):
    """Create a scheduled agent cron job."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO cron_jobs (user_id, name, schedule, prompt, enabled, timezone, created_at, last_run) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (uid, req.name, req.schedule, req.prompt, 1 if req.enabled else 0, req.timezone, now)
    )
    db.commit()
    job_id = cur.lastrowid
    db.close()
    return {"id": job_id, "name": req.name, "schedule": req.schedule}

@app.get("/api/cron")
async def list_cron_jobs(request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    rows = db.execute(
        "SELECT id, name, schedule, prompt, enabled, timezone, created_at, last_run FROM cron_jobs WHERE user_id = ?",
        (user_data["sub"],)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.delete("/api/cron/{job_id}")
async def delete_cron_job(job_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    db = get_db()
    db.execute("DELETE FROM cron_jobs WHERE id = ? AND user_id = ?", (job_id, user_data["sub"]))
    db.commit()
    db.close()
    return {"ok": True}

@app.patch("/api/cron/{job_id}")
async def update_cron_job(job_id: int, request: Request):
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    db = get_db()
    updates = []
    params = []
    for field in ["name", "schedule", "prompt", "enabled", "timezone"]:
        if field in body:
            updates.append(f"{field} = ?")
            params.append(1 if (field == "enabled" and body[field]) else body[field])
    if not updates:
        db.close()
        raise HTTPException(400, "Nothing to update")
    params += [job_id, user_data["sub"]]
    db.execute(f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
    db.commit()
    db.close()
    return {"ok": True}


# ─── Stripe payment endpoints ─────────────────────────────────────────────────

@app.post("/api/payment/setup-intent")
async def payment_setup_intent(request: Request):
    """Create a Stripe SetupIntent so the frontend can capture card details."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    import stripe as _stripe
    _stripe.api_key = STRIPE_SECRET_KEY
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "User not found")

    # Create or retrieve Stripe customer
    customer_id = user["stripe_customer_id"]
    if not customer_id:
        customer = _stripe.Customer.create(
            email=user["email"],
            name=user["name"],
            metadata={"adado_user_id": str(uid)},
        )
        customer_id = customer["id"]
        db.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ?", (customer_id, uid))
        db.commit()
    db.close()

    intent = _stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
    )
    return {"client_secret": intent["client_secret"], "publishable_key": STRIPE_PUBLISHABLE_KEY}


@app.post("/api/payment/confirm")
async def payment_confirm(request: Request):
    """Attach card and create subscription after frontend captures card via Stripe Elements."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(503, "Stripe not configured")
    import stripe as _stripe
    _stripe.api_key = STRIPE_SECRET_KEY
    body = await request.json()
    setup_intent_id   = body.get("setup_intent_id", "")
    payment_method_id = body.get("payment_method_id", "")
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user or not user["stripe_customer_id"]:
        db.close()
        raise HTTPException(400, "Setup payment intent first")
    customer_id = user["stripe_customer_id"]

    # Attach payment method as default
    _stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
    _stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": payment_method_id},
    )

    # Create subscription
    sub = _stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": STRIPE_PRICE_ID}],
        default_payment_method=payment_method_id,
        expand=["latest_invoice.payment_intent"],
    )
    sub_id     = sub["id"]
    sub_status = sub["status"]
    db.execute(
        "UPDATE users SET stripe_subscription_id = ?, subscription_status = ? WHERE id = ?",
        (sub_id, sub_status, uid)
    )
    db.commit()
    db.close()
    return {"success": True, "subscription_id": sub_id, "status": sub_status}


@app.post("/api/payment/webhook")
async def payment_webhook(request: Request):
    """Stripe webhook — verify signature and update subscription status."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    import stripe as _stripe
    _stripe.api_key = STRIPE_SECRET_KEY
    payload   = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = _stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "Invalid webhook signature")

    obj = event["data"]["object"]
    event_type = event["type"]

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = obj["id"]
        status = obj["status"]
        db = get_db()
        db.execute(
            "UPDATE users SET subscription_status = ? WHERE stripe_subscription_id = ?",
            (status, sub_id)
        )
        db.commit()
        db.close()
    elif event_type == "invoice.payment_failed":
        customer_id = obj.get("customer")
        if customer_id:
            db = get_db()
            db.execute(
                "UPDATE users SET subscription_status = 'past_due' WHERE stripe_customer_id = ?",
                (customer_id,)
            )
            db.commit()
            db.close()

    return JSONResponse({"received": True})


@app.get("/api/payment/status")
async def payment_status(request: Request):
    """Return current subscription status for the authenticated user."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    db.close()
    if not user:
        raise HTTPException(404, "User not found")

    # If Stripe is not configured, treat everyone as active (MVP/trial mode)
    if not STRIPE_SECRET_KEY:
        return {"status": "trialing", "plan": "trial", "next_billing_date": None, "card_last4": None}

    status     = user["subscription_status"] or "free"
    sub_id     = user["stripe_subscription_id"]
    next_bill  = None
    card_last4 = None

    if sub_id and STRIPE_SECRET_KEY:
        try:
            import stripe as _stripe
            _stripe.api_key = STRIPE_SECRET_KEY
            sub = _stripe.Subscription.retrieve(sub_id, expand=["default_payment_method"])
            next_bill  = sub.get("current_period_end")
            pm = sub.get("default_payment_method")
            if isinstance(pm, dict):
                card_last4 = pm.get("card", {}).get("last4")
        except Exception:
            pass

    return {
        "status": status,
        "plan": "pro" if status in ("active", "trialing") else "free",
        "next_billing_date": next_bill,
        "card_last4": card_last4,
    }


# ─── Fastmail email endpoints ─────────────────────────────────────────────────

import re as _re

def _sanitize_username(name: str) -> str:
    """Lowercase and strip non-alphanumeric chars from a display name."""
    return _re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "user"


@app.post("/api/email/provision")
async def email_provision(request: Request):
    """Create a Fastmail mailbox for the authenticated user via JMAP."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    if not FASTMAIL_API_TOKEN or not FASTMAIL_ACCOUNT_ID:
        raise HTTPException(503, "Fastmail not configured")
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "User not found")
    if user["email_provisioned"]:
        db.close()
        return {"email_address": user["email_address"]}

    # Derive username from display name, de-dupe with numeric suffix if taken
    base = _sanitize_username(user["name"])
    username = base
    import httpx as _httpx
    for suffix in range(1, 50):
        # JMAP identity set — check by attempting creation
        break_outer = False
        jmap_req = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [[
                "Identity/set",
                {
                    "accountId": FASTMAIL_ACCOUNT_ID,
                    "create": {
                        "new1": {
                            "name": user["name"],
                            "email": f"{username}@{FASTMAIL_DOMAIN}",
                        }
                    }
                },
                "0"
            ]]
        }
        async with _httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.fastmail.com/jmap/api/",
                headers={
                    "Authorization": f"Bearer {FASTMAIL_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=jmap_req,
            )
        if resp.status_code == 200:
            data = resp.json()
            created = (data.get("methodResponses", [[]])[0][1] or {}).get("created", {})
            errors  = (data.get("methodResponses", [[]])[0][1] or {}).get("notCreated", {})
            if "new1" in created:
                break_outer = True
            elif "new1" in errors and "alreadyExists" in str(errors["new1"]):
                username = f"{base}{suffix}"
        if break_outer:
            break

    email_address = f"{username}@{FASTMAIL_DOMAIN}"
    db.execute(
        "UPDATE users SET email_address = ?, email_provisioned = 1 WHERE id = ?",
        (email_address, uid)
    )
    db.commit()
    db.close()
    return {"email_address": email_address}


class EmailForwardRequest(BaseModel):
    from_address: str


@app.post("/api/email/setup-forward")
async def email_setup_forward(req: EmailForwardRequest, request: Request):
    """Store forwarding source address and return step-by-step instructions."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT email_address FROM users WHERE id = ?", (uid,)).fetchone()
    if not user or not user["email_address"]:
        db.close()
        raise HTTPException(400, "Provision your AdaDo email first")
    target = user["email_address"]
    db.execute(
        "INSERT INTO email_forwards (user_id, from_address) VALUES (?, ?)",
        (uid, req.from_address.strip())
    )
    db.commit()
    db.close()

    from_domain = req.from_address.split("@")[-1].lower() if "@" in req.from_address else ""
    if "gmail" in from_domain:
        instructions = (
            f"To forward Gmail to {target}:\n"
            "1. Open Gmail → Settings (⚙️) → See all settings\n"
            "2. Go to Forwarding and POP/IMAP tab\n"
            "3. Click 'Add a forwarding address' and enter: " + target + "\n"
            "4. Google will send a verification code — check your AdaDo inbox\n"
            "5. Once verified, select 'Forward a copy of incoming mail to'\n"
            "6. Choose what to do with the Gmail copy (keep/archive/delete)\n"
            "7. Click Save Changes\n\n"
            "Gmail SMTP settings page: https://mail.google.com/mail/u/0/#settings/fwdandpop"
        )
    elif "outlook" in from_domain or "hotmail" in from_domain or "live" in from_domain:
        instructions = (
            f"To forward Outlook/Hotmail to {target}:\n"
            "1. Go to outlook.com → Settings → Mail → Forwarding\n"
            "2. Enable forwarding and enter: " + target + "\n"
            "3. Optionally check 'Keep a copy of forwarded messages'\n"
            "4. Click Save\n"
        )
    else:
        instructions = (
            f"To forward {req.from_address} to {target}:\n"
            "1. Log in to your email provider's settings\n"
            "2. Find 'Forwarding', 'Mail Forwarding', or 'Auto-forward'\n"
            f"3. Add forwarding address: {target}\n"
            "4. Confirm any verification email sent to your AdaDo inbox\n"
            "5. Save and test by sending yourself a message\n"
        )

    return {"instructions": instructions, "target": target, "from_address": req.from_address}


@app.get("/api/email/status")
async def email_status(request: Request):
    """Return email provisioning status for the authenticated user."""
    user_data = get_auth_user(request)
    if not user_data:
        raise HTTPException(401, "Unauthorized")
    uid = user_data["sub"]
    db = get_db()
    user = db.execute("SELECT email_address, email_provisioned FROM users WHERE id = ?", (uid,)).fetchone()
    forwards = db.execute(
        "SELECT from_address, created_at FROM email_forwards WHERE user_id = ? ORDER BY created_at DESC",
        (uid,)
    ).fetchall()
    db.close()
    # If Fastmail not configured, treat email as provisioned so the overlay never fires
    if not FASTMAIL_API_TOKEN or not FASTMAIL_ACCOUNT_ID:
        return {"provisioned": True, "email_address": None, "forwards": []}
    return {
        "provisioned": bool(user and user["email_provisioned"]),
        "email_address": user["email_address"] if user else None,
        "forwards": [dict(r) for r in forwards],
    }


# ─── Support chat (pre-auth, public) ─────────────────────────────────────────

import re as _re

# In-memory rate limiter: {ip: [timestamp, ...]}
_support_rate: dict[str, list] = {}
_SUPPORT_LIMIT = 20       # requests
_SUPPORT_WINDOW = 3600    # 1 hour in seconds

# In-memory session history: {session_id: [{"role": ..., "content": ...}, ...]}
_support_sessions: dict[str, list] = {}
_SUPPORT_SESSION_MAX = 20  # max messages to keep per session

# Plane config (for ticket creation)
_PLANE_API_KEY  = os.getenv("PLANE_API_KEY", "plane_api_716deab5290c427eb03cd54b87a3b6e5")
_PLANE_BASE_URL = os.getenv("PLANE_API_HOST_URL", "http://100.127.152.116:9210")
_PLANE_WORKSPACE = os.getenv("PLANE_WORKSPACE_SLUG", "diginoz")
_PLANE_PROJECT_ID = "644b9571-3eba-414e-bf6b-ed16012d86d0"

# Escalation log path (Ada monitors this)
_ESCALATION_LOG = Path("/data/support-escalations.log")

_SUPPORT_SYSTEM = """You are Ada, AdaDo's support assistant. You help new and existing users with:
- Signup and onboarding issues
- Payment and subscription problems
- App installation and configuration
- Account access issues
- General questions about what AdaDo does

AdaDo is a $29/mo all-in-one AI assistant — one login, one subscription, every app included (email, tasks, calendar, files, and more).

CRITICAL PRIVACY RULE: You are a public-facing support agent. You have absolutely NO access to any user's personal data, email address, name, or account information. You do NOT know who this person is. Never mention, guess, or reveal any email address or name. Never say "you're on [email]" or "I can see your account". Treat every person as a completely anonymous visitor. If you find yourself about to write an email address, stop and remove it.

You are warm, fast, and competent. You fix things — you don't just explain them.

For any action that would require infrastructure changes, data deletion, or system modifications, say: "I'll escalate this to our support team — you'll hear back at ada@diginoz.com.au." and include this exact tag at the end of your reply:
<ESCALATE>brief description of what needs admin action</ESCALATE>"""

# Problem signal words for Python-side ticket classification
_PROBLEM_KEYWORDS = [
    "error", "500", "404", "fail", "failed", "failing", "can't", "cannot",
    "can not", "couldn't", "issue", "problem", "broken", "not working",
    "doesn't work", "won't", "won't load", "didn't receive", "never arrived",
    "never got", "bug", "crash", "crashed", "stuck", "locked out", "blocked",
    "denied", "rejected", "wrong", "missing", "lost", "disappeared",
    "charged", "billed", "double charged", "refund", "payment", "declined",
    "login", "sign in", "signin", "signup", "sign up", "password", "reset",
    "account", "access", "unauthorized", "forbidden", "timeout", "slow",
    "not loading", "blank", "white screen", "keeps", "every time",
]

_ESCALATE_KEYWORDS = [
    "delete my account", "delete account", "delete all my data", "remove my data",
    "ban", "legal", "gdpr", "privacy request", "data export", "server",
    "infrastructure", "database", "admin", "backdoor", "manual override",
]


def _classify_support_message(user_msg: str, assistant_reply: str) -> tuple[bool, bool, str, str]:
    """
    Classify whether to create a ticket and/or escalate.
    Returns: (should_ticket, should_escalate, severity, title)
    """
    msg_lower = user_msg.lower()
    reply_lower = assistant_reply.lower()

    is_problem = any(kw in msg_lower for kw in _PROBLEM_KEYWORDS)
    is_escalate = any(kw in msg_lower for kw in _ESCALATE_KEYWORDS)

    # Don't ticket if the reply suggests it's not a real problem
    if not is_problem:
        return False, is_escalate, "low", ""

    # Determine severity
    if any(kw in msg_lower for kw in ["can't access", "locked out", "can't log in", "can't login", "blocked", "urgent", "outage", "down"]):
        severity = "high"
    elif any(kw in msg_lower for kw in ["error", "500", "crash", "failed", "not working", "broken"]):
        severity = "medium"
    elif any(kw in msg_lower for kw in ["payment", "charged", "billed", "refund", "double"]):
        severity = "high"
    else:
        severity = "low"

    # Build a brief title from first 80 chars of problem description
    title = user_msg.strip()[:80].replace("\n", " ")
    if len(user_msg) > 80:
        title = title.rstrip() + "..."

    return True, is_escalate, severity, title


async def _create_plane_ticket(title: str, severity: str, resolved: bool, summary: str, session_id: str, history: list) -> str | None:
    """Create a Plane issue and return the readable identifier (e.g. ADADO-42)."""
    try:
        import httpx
        # Build description with conversation history
        convo_lines = []
        for m in history:
            role = "User" if m["role"] == "user" else "Ada"
            convo_lines.append(f"**{role}:** {m['content'][:500]}")
        convo_text = "\n\n".join(convo_lines)

        description = (
            f"**Severity:** {severity}\n"
            f"**Status:** {'Resolved' if resolved else 'Unresolved'}\n"
            f"**Session ID:** {session_id}\n\n"
            f"**Summary:** {summary}\n\n"
            f"---\n\n**Conversation:**\n\n{convo_text}"
        )

        headers = {
            "X-API-Key": _PLANE_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "name": title,
            "description_html": f"<p>{description.replace(chr(10), '<br>')}</p>",
            "priority": {"low": "low", "medium": "medium", "high": "high", "critical": "urgent"}.get(severity, "medium"),
        }
        url = f"{_PLANE_BASE_URL}/api/v1/workspaces/{_PLANE_WORKSPACE}/projects/{_PLANE_PROJECT_ID}/issues/"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code in (200, 201):
                data = r.json()
                seq = data.get("sequence_id", "?")
                return f"ADADO-{seq}"
    except Exception:
        pass
    return None


def _log_escalation(issue: str, user: str, session_id: str):
    """Append an escalation entry to the escalation log."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        line = f"[{ts}] Support escalation — Session: {session_id} | User: {user or 'unknown'} | Issue: {issue}\n"
        _ESCALATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_ESCALATION_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


class SupportChatRequest(BaseModel):
    message: str
    session_id: str = ""

@app.post("/api/support/chat")
async def support_chat(req: SupportChatRequest, request: Request):
    # Rate limit by IP
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = _support_rate.get(ip, [])
    hits = [t for t in hits if now - t < _SUPPORT_WINDOW]
    if len(hits) >= _SUPPORT_LIMIT:
        raise HTTPException(429, "Too many requests — please try again later.")
    hits.append(now)
    _support_rate[ip] = hits

    msg = req.message.strip()[:2000]
    if not msg:
        raise HTTPException(400, "Empty message")

    session_id = req.session_id.strip()[:64] or secrets.token_hex(8)

    # Load or create session history
    history = _support_sessions.get(session_id, [])
    history.append({"role": "user", "content": msg})

    # Trim to max window
    if len(history) > _SUPPORT_SESSION_MAX:
        history = history[-_SUPPORT_SESSION_MAX:]

    raw_reply = ""

    # Support chat inference — uses Anthropic via proxy (Ollama too slow on CPU-only host)
    try:
        import anthropic as _anthro
        _proxy = CLAUDE_PROXY_URL or (
            "http://192.168.80.1:8211/" if ANTHROPIC_API_KEY.startswith("sk-ant-oat") else ""
        )
        if _proxy:
            _aclient = _anthro.AsyncAnthropic(api_key="proxy", base_url=_proxy)
        elif ANTHROPIC_API_KEY.startswith("sk-ant-oat"):
            _aclient = _anthro.AsyncAnthropic(auth_token=ANTHROPIC_API_KEY)
        else:
            _aclient = _anthro.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        _resp = await _aclient.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=_SUPPORT_SYSTEM,
            messages=history,
        )
        raw_reply = _resp.content[0].text if _resp.content else "Sorry, I couldn't respond right now."
    except Exception as e:
        raw_reply = f"Hi! I'm having a brief technical hiccup. Please try again in a moment, or email ada@diginoz.com.au if this keeps happening."

    # Post-process: strip any email addresses that leaked through (proxy may inject context)
    raw_reply = _re.sub(r'\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b', '[email protected]', raw_reply)

    # Parse ESCALATE tag from reply (model-driven for admin-action cases)
    escalated = False
    esc_match = _re.search(r'<ESCALATE[^>]*>(.*?)</ESCALATE>', raw_reply, _re.DOTALL)
    if esc_match:
        esc_issue = esc_match.group(1).strip()
        _log_escalation(issue=esc_issue, user="anonymous", session_id=session_id)
        escalated = True
        raw_reply = _re.sub(r"\s*<ESCALATE[^>]*>.*?</ESCALATE>", "", raw_reply, flags=_re.DOTALL).strip()

    # Save assistant turn to history
    history.append({"role": "assistant", "content": raw_reply})
    _support_sessions[session_id] = history

    # Python-side ticket classification (no extra API call needed)
    ticket_id = None
    should_ticket, should_escalate_py, severity, title = _classify_support_message(msg, raw_reply)
    if should_ticket:
        summary = f"User reported: {msg[:300]}"
        ticket_id = await _create_plane_ticket(
            title=title,
            severity=severity,
            resolved=False,
            summary=summary,
            session_id=session_id,
            history=history,
        )
    if should_escalate_py and not escalated:
        _log_escalation(issue=msg[:200], user="", session_id=session_id)
        escalated = True

    result: dict = {"reply": raw_reply, "session_id": session_id}
    if ticket_id:
        result["ticket_id"] = ticket_id
        result["reply"] = raw_reply + f"\n\n*Issue {ticket_id} created for tracking.*"
    if escalated:
        result["escalated"] = True

    return result


# ─── Static / UI ──────────────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ─── Maintenance banner ───────────────────────────────────────────────────────

_MAINTENANCE_FILE = Path("/data/maintenance.json")

def _read_maintenance() -> dict:
    try:
        if _MAINTENANCE_FILE.exists():
            import json as _json
            return _json.loads(_MAINTENANCE_FILE.read_text())
    except Exception:
        pass
    return {"active": False, "message": "", "level": "info"}

def _write_maintenance(data: dict):
    try:
        import json as _json
        _MAINTENANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MAINTENANCE_FILE.write_text(_json.dumps(data))
    except Exception:
        pass

@app.get("/api/maintenance")
async def get_maintenance():
    return _read_maintenance()

class MaintenanceRequest(BaseModel):
    active: bool = True
    message: str = ""
    level: str = "info"  # info | warning | error

@app.post("/api/maintenance")
async def set_maintenance(req: MaintenanceRequest, request: Request):
    user = get_auth_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    data = {"active": req.active, "message": req.message, "level": req.level}
    _write_maintenance(data)
    return data

@app.delete("/api/maintenance")
async def clear_maintenance(request: Request):
    user = get_auth_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    _write_maintenance({"active": False, "message": "", "level": "info"})
    return {"active": False}

# ─── robots / sitemap ─────────────────────────────────────────────────────────

@app.get("/robots.txt", include_in_schema=False)
async def robots():
    f = static_dir / "robots.txt"
    if f.exists():
        return FileResponse(str(f), media_type="text/plain")
    return PlainTextResponse("User-agent: *\nAllow: /\n")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    f = static_dir / "sitemap.xml"
    if f.exists():
        return FileResponse(str(f), media_type="application/xml")
    return PlainTextResponse("", status_code=404)

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>AdaDo Core — UI not found</h1>", status_code=200)
