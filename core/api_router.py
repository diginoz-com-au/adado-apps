"""
AdaDo API Router
Manages a pool of Anthropic API accounts, per-user quotas, BYOK, and request queuing.
Prevents rate limits from ever dropping user messages.
"""

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional
import anthropic
from cryptography.fernet import Fernet

# ─── Encryption ──────────────────────────────────────────────────────────────

_KEY_PATH = os.path.expanduser("~/.adado-router.key")


def _fernet() -> Fernet:
    if not os.path.exists(_KEY_PATH):
        key = Fernet.generate_key()
        with open(_KEY_PATH, "wb") as f:
            f.write(key)
        os.chmod(_KEY_PATH, 0o600)
    with open(_KEY_PATH, "rb") as f:
        return Fernet(f.read())


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


# ─── DB helpers ──────────────────────────────────────────────────────────────

_DB_PATH: Optional[str] = None  # set by init_router()


def _conn() -> sqlite3.Connection:
    assert _DB_PATH, "api_router.init_router() not called"
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_router(db_path: str) -> None:
    """Create tables and seed from env if no accounts exist yet."""
    global _DB_PATH
    _DB_PATH = db_path
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS api_accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            enc_api_key TEXT NOT NULL,
            tier        INTEGER DEFAULT 1,
            rpm_limit   INTEGER DEFAULT 50,
            tpm_limit   INTEGER DEFAULT 50000,
            tpd_limit   INTEGER DEFAULT 2500000,
            active      INTEGER DEFAULT 1,
            notes       TEXT DEFAULT '',
            created_at  REAL,
            updated_at  REAL
        );

        CREATE TABLE IF NOT EXISTS account_usage_windows (
            account_id  INTEGER NOT NULL,
            bucket      INTEGER NOT NULL,
            requests    INTEGER DEFAULT 0,
            tokens_in   INTEGER DEFAULT 0,
            tokens_out  INTEGER DEFAULT 0,
            PRIMARY KEY (account_id, bucket)
        );

        CREATE TABLE IF NOT EXISTS account_usage_daily (
            account_id      INTEGER NOT NULL,
            date            TEXT NOT NULL,
            tokens_total    INTEGER DEFAULT 0,
            requests_total  INTEGER DEFAULT 0,
            PRIMARY KEY (account_id, date)
        );

        CREATE TABLE IF NOT EXISTS user_api_keys (
            user_id         INTEGER PRIMARY KEY,
            provider        TEXT DEFAULT 'anthropic',
            enc_api_key     TEXT NOT NULL,
            model_override  TEXT,
            created_at      REAL,
            updated_at      REAL
        );

        CREATE TABLE IF NOT EXISTS user_quotas (
            user_id             INTEGER PRIMARY KEY,
            plan                TEXT DEFAULT 'trial',
            daily_token_limit   INTEGER DEFAULT 50000,
            tokens_used_today   INTEGER DEFAULT 0,
            tokens_used_month   INTEGER DEFAULT 0,
            quota_reset_date    TEXT,
            account_id_affinity INTEGER
        );
        """)
        c.commit()
        _seed_from_env(c)


def _seed_from_env(c: sqlite3.Connection) -> None:
    """Add the main API key from environment if no accounts exist yet."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key or not key.startswith("sk-"):
        return
    existing = c.execute("SELECT COUNT(*) FROM api_accounts").fetchone()[0]
    if existing == 0:
        now = time.time()
        c.execute(
            "INSERT OR IGNORE INTO api_accounts "
            "(name, enc_api_key, tier, rpm_limit, tpm_limit, tpd_limit, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("main", _encrypt(key), 1, 50, 50_000, 2_500_000, now, now),
        )
        c.commit()


# ─── Plan definitions ─────────────────────────────────────────────────────────

PLAN_LIMITS = {
    "trial":     {"daily": 50_000,      "models": {"haiku"}},
    "cloud":     {"daily": 500_000,     "models": {"haiku", "sonnet"}},
    "cli":       {"daily": 2_000_000,   "models": {"haiku", "sonnet", "opus"}},
    "vps":       {"daily": 10_000_000,  "models": {"haiku", "sonnet", "opus"}},
    "unlimited": {"daily": 999_999_999, "models": {"haiku", "sonnet", "opus"}},
}

# Tier limits for auto-detect / manual config
TIER_LIMITS = {
    1: {"rpm": 50,    "tpm": 50_000,  "tpd": 2_500_000},
    2: {"rpm": 2_000, "tpm": 400_000, "tpd": 25_000_000},
    3: {"rpm": 4_000, "tpm": 1_000_000, "tpd": 250_000_000},
    4: {"rpm": 8_000, "tpm": 2_000_000, "tpd": 1_000_000_000},
}


# ─── Account pool ─────────────────────────────────────────────────────────────

@dataclass
class AccountStatus:
    id: int
    name: str
    api_key: str
    tier: int
    rpm_limit: int
    tpm_limit: int
    tpd_limit: int
    # live tracking (filled from DB)
    rpm_used: int = 0
    tpm_used: int = 0
    tpd_used: int = 0

    @property
    def rpm_headroom(self) -> float:
        return 1.0 - (self.rpm_used / max(1, self.rpm_limit))

    @property
    def tpm_headroom(self) -> float:
        return 1.0 - (self.tpm_used / max(1, self.tpm_limit))

    @property
    def tpd_headroom(self) -> float:
        return 1.0 - (self.tpd_used / max(1, self.tpd_limit))

    def has_headroom(self, estimated_tokens: int = 1000, buffer: float = 0.1) -> bool:
        threshold = 1.0 - buffer
        return (
            self.rpm_headroom > buffer
            and self.tpm_headroom > buffer
            and self.tpd_headroom > buffer
        )


def _get_pool_status() -> list[AccountStatus]:
    now = time.time()
    bucket = int(now) // 60
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    with _conn() as c:
        accounts = c.execute(
            "SELECT * FROM api_accounts WHERE active=1 ORDER BY id"
        ).fetchall()

        result = []
        for acc in accounts:
            # Sliding window: sum last 60 seconds (current + prior bucket)
            window = c.execute(
                "SELECT COALESCE(SUM(requests),0), COALESCE(SUM(tokens_in+tokens_out),0) "
                "FROM account_usage_windows "
                "WHERE account_id=? AND bucket>=?",
                (acc["id"], bucket - 1),
            ).fetchone()
            rpm_used = window[0]
            tpm_used = window[1]

            # Daily total
            daily = c.execute(
                "SELECT COALESCE(tokens_total,0) FROM account_usage_daily "
                "WHERE account_id=? AND date=?",
                (acc["id"], today),
            ).fetchone()
            tpd_used = daily[0] if daily else 0

            result.append(AccountStatus(
                id=acc["id"],
                name=acc["name"],
                api_key=_decrypt(acc["enc_api_key"]),
                tier=acc["tier"],
                rpm_limit=acc["rpm_limit"],
                tpm_limit=acc["tpm_limit"],
                tpd_limit=acc["tpd_limit"],
                rpm_used=rpm_used,
                tpm_used=tpm_used,
                tpd_used=tpd_used,
            ))

        # Clean up old window buckets
        c.execute(
            "DELETE FROM account_usage_windows WHERE bucket < ?",
            (bucket - 5,),
        )
        c.commit()
    return result


def _record_usage(account_id: int, tokens_in: int, tokens_out: int) -> None:
    bucket = int(time.time()) // 60
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with _conn() as c:
        c.execute(
            """INSERT INTO account_usage_windows (account_id, bucket, requests, tokens_in, tokens_out)
               VALUES (?,?,1,?,?)
               ON CONFLICT(account_id,bucket) DO UPDATE SET
               requests=requests+1, tokens_in=tokens_in+excluded.tokens_in,
               tokens_out=tokens_out+excluded.tokens_out""",
            (account_id, bucket, tokens_in, tokens_out),
        )
        c.execute(
            """INSERT INTO account_usage_daily (account_id, date, tokens_total, requests_total)
               VALUES (?,?,?,1)
               ON CONFLICT(account_id,date) DO UPDATE SET
               tokens_total=tokens_total+excluded.tokens_total,
               requests_total=requests_total+1""",
            (account_id, today, tokens_in + tokens_out),
        )
        c.commit()


# ─── User quotas ──────────────────────────────────────────────────────────────

def _ensure_quota(user_id: int, c: sqlite3.Connection) -> sqlite3.Row:
    row = c.execute("SELECT * FROM user_quotas WHERE user_id=?", (user_id,)).fetchone()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if not row:
        c.execute(
            "INSERT INTO user_quotas (user_id, plan, daily_token_limit, "
            "tokens_used_today, tokens_used_month, quota_reset_date) VALUES (?,?,?,0,0,?)",
            (user_id, "trial", PLAN_LIMITS["trial"]["daily"], today),
        )
        c.commit()
        row = c.execute("SELECT * FROM user_quotas WHERE user_id=?", (user_id,)).fetchone()
    # Reset daily counter if needed
    if row["quota_reset_date"] != today:
        c.execute(
            "UPDATE user_quotas SET tokens_used_today=0, quota_reset_date=? WHERE user_id=?",
            (today, user_id),
        )
        c.commit()
        row = c.execute("SELECT * FROM user_quotas WHERE user_id=?", (user_id,)).fetchone()
    return row


def get_user_quota_status(user_id: int) -> dict:
    with _conn() as c:
        row = _ensure_quota(user_id, c)
        plan = row["plan"]
        limit = row["daily_token_limit"]
        used = row["tokens_used_today"]
        used_month = row["tokens_used_month"]
        pct = round(used / max(1, limit) * 100, 1)
        return {
            "plan": plan,
            "daily_limit": limit,
            "used_today": used,
            "used_month": used_month,
            "pct_today": pct,
            "remaining_today": max(0, limit - used),
            "quota_exceeded": used >= limit,
            "warn": pct >= 80,
        }


def set_user_plan(user_id: int, plan: str) -> None:
    if plan not in PLAN_LIMITS:
        raise ValueError(f"Unknown plan: {plan}")
    limits = PLAN_LIMITS[plan]
    with _conn() as c:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        c.execute(
            """INSERT INTO user_quotas (user_id, plan, daily_token_limit, quota_reset_date)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
               plan=excluded.plan, daily_token_limit=excluded.daily_token_limit""",
            (user_id, plan, limits["daily"], today),
        )
        c.commit()


def _record_user_usage(user_id: int, tokens: int) -> None:
    with _conn() as c:
        _ensure_quota(user_id, c)
        c.execute(
            "UPDATE user_quotas SET tokens_used_today=tokens_used_today+?, "
            "tokens_used_month=tokens_used_month+? WHERE user_id=?",
            (tokens, tokens, user_id),
        )
        c.commit()


def _get_user_affinity(user_id: int) -> Optional[int]:
    with _conn() as c:
        row = c.execute(
            "SELECT account_id_affinity FROM user_quotas WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return row["account_id_affinity"] if row else None


def _set_user_affinity(user_id: int, account_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE user_quotas SET account_id_affinity=? WHERE user_id=?",
            (account_id, user_id),
        )
        c.commit()


# ─── BYOK ────────────────────────────────────────────────────────────────────

def get_user_byok(user_id: int) -> Optional[str]:
    with _conn() as c:
        row = c.execute(
            "SELECT enc_api_key FROM user_api_keys WHERE user_id=? AND provider='anthropic'",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return _decrypt(row["enc_api_key"])
    except Exception:
        return None


def set_user_byok(user_id: int, api_key: str) -> None:
    if not api_key.startswith("sk-ant-api"):
        raise ValueError("Invalid Anthropic API key (must start with sk-ant-api)")
    enc = _encrypt(api_key)
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO user_api_keys (user_id, provider, enc_api_key, created_at, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
               enc_api_key=excluded.enc_api_key, updated_at=excluded.updated_at""",
            (user_id, "anthropic", enc, now, now),
        )
        c.commit()


def delete_user_byok(user_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM user_api_keys WHERE user_id=?", (user_id,))
        c.commit()
    return cur.rowcount > 0


# ─── Account management ───────────────────────────────────────────────────────

def add_account(name: str, api_key: str, tier: int = 1, notes: str = "") -> int:
    limits = TIER_LIMITS.get(tier, TIER_LIMITS[1])
    enc = _encrypt(api_key)
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO api_accounts "
            "(name, enc_api_key, tier, rpm_limit, tpm_limit, tpd_limit, active, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,1,?,?,?)",
            (name, enc, tier, limits["rpm"], limits["tpm"], limits["tpd"], notes, now, now),
        )
        c.commit()
        return cur.lastrowid


def update_account_tier(account_id: int, tier: int) -> None:
    limits = TIER_LIMITS.get(tier, TIER_LIMITS[1])
    with _conn() as c:
        c.execute(
            "UPDATE api_accounts SET tier=?, rpm_limit=?, tpm_limit=?, tpd_limit=?, updated_at=? WHERE id=?",
            (tier, limits["rpm"], limits["tpm"], limits["tpd"], time.time(), account_id),
        )
        c.commit()


def list_accounts() -> list[dict]:
    statuses = _get_pool_status()
    return [
        {
            "id": a.id,
            "name": a.name,
            "tier": a.tier,
            "rpm": f"{a.rpm_used}/{a.rpm_limit}",
            "tpm": f"{a.tpm_used}/{a.tpm_limit}",
            "tpd": f"{a.tpd_used}/{a.tpd_limit}",
            "headroom_pct": round(min(a.rpm_headroom, a.tpm_headroom, a.tpd_headroom) * 100, 1),
        }
        for a in statuses
    ]


# ─── Main routing entry point ─────────────────────────────────────────────────

@dataclass
class RouteResult:
    client: anthropic.AsyncAnthropic
    account_id: Optional[int]   # None = BYOK
    source: str                 # 'byok' | 'pool' | 'affinity'
    quota_warn: bool = False
    quota_pct: float = 0.0


class QuotaExceededError(Exception):
    pass


class NoAccountAvailableError(Exception):
    pass


async def get_client(user_id: int, estimated_tokens: int = 1000) -> RouteResult:
    """
    Main entry point. Returns a configured AsyncAnthropic client + metadata.
    Raises QuotaExceededError if user's daily limit is hit.
    Raises NoAccountAvailableError if all pool accounts are rate-limited (caller should queue).
    """
    # 1. Check BYOK
    byok_key = get_user_byok(user_id)
    if byok_key:
        return RouteResult(
            client=anthropic.AsyncAnthropic(api_key=byok_key),
            account_id=None,
            source="byok",
        )

    # 2. Check quota
    quota = get_user_quota_status(user_id)
    if quota["quota_exceeded"]:
        raise QuotaExceededError(
            f"Daily token limit reached ({quota['daily_limit']:,} tokens). "
            f"Resets at midnight UTC."
        )

    # 3. Pick pool account
    pool = _get_pool_status()
    if not pool:
        raise NoAccountAvailableError("No API accounts configured")

    # Try affinity account first
    affinity_id = _get_user_affinity(user_id)
    if affinity_id:
        for acc in pool:
            if acc.id == affinity_id and acc.has_headroom(estimated_tokens):
                return RouteResult(
                    client=anthropic.AsyncAnthropic(api_key=acc.api_key),
                    account_id=acc.id,
                    source="affinity",
                    quota_warn=quota["warn"],
                    quota_pct=quota["pct_today"],
                )

    # Find least-loaded account with headroom
    candidates = [a for a in pool if a.has_headroom(estimated_tokens)]
    if not candidates:
        raise NoAccountAvailableError("All API accounts are currently rate-limited")

    best = min(candidates, key=lambda a: a.tpm_used / max(1, a.tpm_limit))
    _set_user_affinity(user_id, best.id)

    return RouteResult(
        client=anthropic.AsyncAnthropic(api_key=best.api_key),
        account_id=best.id,
        source="pool",
        quota_warn=quota["warn"],
        quota_pct=quota["pct_today"],
    )


async def get_client_with_retry(
    user_id: int,
    estimated_tokens: int = 1000,
    max_wait: float = 30.0,
    send_fn=None,
) -> RouteResult:
    """
    Like get_client() but queues when rate-limited, retrying until max_wait seconds.
    send_fn: optional async callable to send status messages to user.
    """
    start = time.time()
    notified = False

    while True:
        try:
            return await get_client(user_id, estimated_tokens)
        except QuotaExceededError:
            raise
        except NoAccountAvailableError:
            elapsed = time.time() - start
            if elapsed >= max_wait:
                raise
            if not notified and send_fn:
                await send_fn({
                    "type": "system",
                    "message": "Ada is handling a lot of requests right now — your message is queued and will be answered in a moment…"
                })
                notified = True
            await asyncio.sleep(2.0)


def record_usage(account_id: Optional[int], user_id: int, tokens_in: int, tokens_out: int) -> None:
    """Call this after every API response to update rate limit tracking and user quotas."""
    if account_id is not None:
        _record_usage(account_id, tokens_in, tokens_out)
    _record_user_usage(user_id, tokens_in + tokens_out)


# ─── Pool health summary (for admin/monitoring) ───────────────────────────────

def pool_health() -> dict:
    accounts = list_accounts()
    total_tpm = sum(
        int(a["tpm"].split("/")[1]) for a in accounts if "/" in a["tpm"]
    )
    any_saturated = any(a["headroom_pct"] < 10 for a in accounts)
    return {
        "accounts": len(accounts),
        "total_tpm_capacity": total_tpm,
        "any_saturated": any_saturated,
        "accounts_detail": accounts,
    }
