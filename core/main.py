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
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
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
INSTANCE_NAME     = os.getenv("INSTANCE_NAME", "Ada")

USE_ANTHROPIC = bool(ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-"))

TRIAL_DAYS = 7

# ─── Persona builder ──────────────────────────────────────────────────────────

DEFAULT_SOUL = f"""You are Ada, an AI assistant built on AdaDo.
You are direct, capable, and proactive. You get things done.
When asked to do something, do it — don't ask for permission or explain what you're about to do.
Verify from live sources, not from memory. Be concise. Have opinions and hold them.
You help the user manage their life through their apps: projects, finance, passwords, inbox, and more.
Talk to them like a capable colleague, not a customer service bot."""

def build_soul(name: str, onboarding: dict) -> str:
    role = onboarding.get("role", "")
    uses = onboarding.get("use_cases", [])
    experience = onboarding.get("experience", "")
    goals = onboarding.get("goals", "")

    role_line = f"Your user's name is {name}"
    if role:
        role_line += f", a {role}"
    role_line += "."

    use_line = ""
    if uses:
        use_line = f"They primarily use Ada for: {', '.join(uses)}."

    goal_line = ""
    if goals:
        goal_line = f"Context they shared: {goals}"

    exp_map = {
        "beginner": "They're new to AI — explain things simply, avoid jargon.",
        "intermediate": "They're comfortable with AI tools — be efficient.",
        "advanced": "They're an experienced AI user — be terse and technical when appropriate.",
    }
    exp_line = exp_map.get(experience, "")

    parts = [
        "You are Ada, an AI assistant built on AdaDo.",
        "You are direct, capable, and proactive. You get things done.",
        role_line,
        use_line,
        goal_line,
        exp_line,
        "When asked to do something, do it — don't ask for permission or explain first.",
        "Verify from live sources, not from memory. Be concise. Have opinions and hold them.",
        "You help them manage their life through their apps: projects, finance, passwords, inbox, and more.",
        "Talk like a capable colleague, not a customer service bot.",
    ]
    return " ".join(p for p in parts if p)

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
    """)
    # Migrate existing tables — add columns if missing (SQLite doesn't support IF NOT EXISTS on ALTER)
    for col, defn in [
        ("tier",                "TEXT DEFAULT 'cloud'"),
        ("trial_ends_at",       "INTEGER"),
        ("onboarding_complete", "INTEGER DEFAULT 0"),
        ("onboarding_data",     "TEXT DEFAULT '{}'"),
    ]:
        try:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
        except Exception:
            pass
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

# ─── Apps loader ──────────────────────────────────────────────────────────────

def load_apps() -> list[dict]:
    apps = []
    apps_path = Path(APPS_DIR)
    if not apps_path.exists():
        return []
    for f in sorted(apps_path.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text())
            if data:
                apps.append(data)
        except Exception:
            pass
    return apps

# ─── AI streaming ─────────────────────────────────────────────────────────────

async def stream_anthropic(messages: list, soul: str, websocket: WebSocket) -> tuple[str, int, int]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    full = ""
    input_tok = output_tok = 0
    async with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=soul,
        messages=messages,
    ) as stream:
        async for chunk in stream.text_stream:
            full += chunk
            await websocket.send_json({"type": "chunk", "content": chunk})
        usage = await stream.get_final_usage()
        input_tok  = usage.input_tokens
        output_tok = usage.output_tokens
    return full, input_tok, output_tok

async def stream_ollama(messages: list, soul: str, websocket: WebSocket) -> tuple[str, int, int]:
    import httpx
    full = ""
    payload = {
        "model": CLAUDE_MODEL,
        "messages": [{"role": "system", "content": soul}] + messages,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/v1/chat/completions", json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    try:
                        chunk_data = json.loads(line[6:])
                        delta = chunk_data["choices"][0]["delta"].get("content", "")
                        if delta:
                            full += delta
                            await websocket.send_json({"type": "chunk", "content": delta})
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

# ─── Apps API ─────────────────────────────────────────────────────────────────

@app.get("/api/apps")
async def get_apps():
    return load_apps()

@app.get("/api/status")
async def status():
    return {
        "backend": "anthropic" if USE_ANTHROPIC else "ollama",
        "model": CLAUDE_MODEL,
        "instance": INSTANCE_NAME,
        "version": "0.4.0",
    }

@app.get("/api/health")
async def health():
    return {"status": "ok"}

# ─── WebSocket chat ───────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()

    token_str = websocket.query_params.get("token")
    user_data = verify_token(token_str) if token_str else None

    user_name = INSTANCE_NAME
    soul = DEFAULT_SOUL
    onboarding_complete = True
    history = []

    db = get_db()
    session_id = None
    if user_data:
        user_row = db.execute("SELECT * FROM users WHERE id = ?", (user_data["sub"],)).fetchone()
        if user_row:
            user_name = user_row["name"]
            onboarding_complete = bool(user_row["onboarding_complete"])
            if onboarding_complete:
                onboarding = json.loads(user_row["onboarding_data"] or "{}")
                soul = build_soul(user_name, onboarding)
            # Load most recent session
            session = db.execute(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_data["sub"],)
            ).fetchone()
            if session:
                session_id = session["id"]
                history = json.loads(session["messages"])[-40:]
    db.close()

    await websocket.send_json({
        "type": "ready",
        "name": user_name,
        "onboarding_complete": onboarding_complete,
    })

    try:
        while True:
            data = await websocket.receive_json()
            user_msg = data.get("content", "").strip()
            if not user_msg:
                continue

            history.append({"role": "user", "content": user_msg})

            # Burn rate pre-flight check
            if user_data:
                db = get_db()
                burn = check_burn_rate(db, user_data["sub"])
                db.close()
                if burn["over_daily"]:
                    await websocket.send_json({
                        "type": "error",
                        "content": f"Daily AI spend limit reached (${burn['daily_limit']:.2f}). Reset tomorrow or adjust your limit in Settings."
                    })
                    history.pop()
                    continue
                if burn["over_monthly"]:
                    await websocket.send_json({
                        "type": "error",
                        "content": f"Monthly AI spend limit reached (${burn['monthly_limit']:.2f}). Adjust your limit in Settings."
                    })
                    history.pop()
                    continue

            await websocket.send_json({"type": "start"})

            try:
                context = history[-30:]
                if USE_ANTHROPIC:
                    full_response, input_tok, output_tok = await stream_anthropic(context, soul, websocket)
                else:
                    full_response, input_tok, output_tok = await stream_ollama(context, soul, websocket)
            except Exception as e:
                await websocket.send_json({"type": "error", "content": f"AI error: {e}"})
                history.pop()
                continue

            history.append({"role": "assistant", "content": full_response})
            await websocket.send_json({"type": "done"})

            if user_data:
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
                        (user_data["sub"], json.dumps(history), now, now)
                    )
                    session_id = cur.lastrowid
                log_task(db, user_data["sub"], CLAUDE_MODEL, input_tok, output_tok)
                db.close()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass

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

# ─── Static / UI ──────────────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>AdaDo Core — UI not found</h1>", status_code=200)
