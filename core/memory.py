"""
AdaDo Memory System
Per-user persistent memory that survives across sessions.
Ada reads this at the start of conversations and writes to it during conversations.

Backed by SQLite (same DB as users/sessions).
"""

import json
import time
import sqlite3
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

DB_PATH_DEFAULT = "/data/adado.db"


@dataclass
class MemoryEntry:
    key: str
    value: str
    category: str  # preference, fact, project, decision, note
    created_at: int
    updated_at: int


def _get_db(db_path: str = None):
    path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_schema(db_path: str = None):
    """Add memory-related tables to the DB."""
    db = _get_db(db_path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS user_memory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            category    TEXT    NOT NULL DEFAULT 'note',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL,
            UNIQUE(user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_user ON user_memory(user_id, updated_at);

        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            label       TEXT    NOT NULL,
            message     TEXT    NOT NULL,
            fire_at     INTEGER NOT NULL,
            created_at  INTEGER NOT NULL,
            fired       INTEGER NOT NULL DEFAULT 0,
            fired_at    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(fire_at, fired);

        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            title       TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            tags        TEXT    NOT NULL DEFAULT '[]',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, updated_at);

        CREATE TABLE IF NOT EXISTS context_summaries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            session_id  INTEGER NOT NULL,
            summary     TEXT    NOT NULL,
            message_count INTEGER NOT NULL,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ctx_user ON context_summaries(user_id, session_id);
    """)
    db.commit()
    db.close()


def get_user_memory_block(user_id: int, db_path: str = None) -> str:
    """
    Build the memory context block to inject into Ada's system prompt.
    Returns a formatted string of the user's persistent memory.
    """
    db = _get_db(db_path)
    rows = db.execute(
        "SELECT key, value, category FROM user_memory WHERE user_id = ? ORDER BY category, updated_at DESC",
        (user_id,)
    ).fetchall()
    db.close()

    if not rows:
        return ""

    by_category = {}
    for row in rows:
        cat = row["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(f"- **{row['key']}:** {row['value']}")

    parts = ["\n\n## Memory about this user"]
    category_labels = {
        "preference": "Preferences",
        "fact": "Facts",
        "project": "Ongoing Projects",
        "decision": "Decisions Made",
        "note": "Notes"
    }
    for cat in ["preference", "fact", "project", "decision", "note"]:
        if cat in by_category:
            parts.append(f"\n**{category_labels.get(cat, cat)}:**")
            parts.extend(by_category[cat])

    return "\n".join(parts)


def get_pending_reminders(user_id: int, db_path: str = None) -> str:
    """
    Return a string of upcoming reminders to inject into context.
    """
    db = _get_db(db_path)
    now = int(time.time())
    rows = db.execute(
        "SELECT id, label, fire_at FROM reminders WHERE user_id = ? AND fire_at > ? AND fired = 0 ORDER BY fire_at ASC LIMIT 5",
        (user_id, now)
    ).fetchall()
    db.close()

    if not rows:
        return ""

    from datetime import datetime, timezone
    lines = ["**Pending reminders:**"]
    for r in rows:
        dt = datetime.fromtimestamp(r["fire_at"], tz=timezone.utc)
        lines.append(f"- {r['label']} — {dt.strftime('%Y-%m-%d %H:%M UTC')} (id: {r['id']})")
    return "\n".join(lines)


def summarize_context(messages: list, model_client, soul: str) -> str:
    """
    Summarize a conversation context block for long-session compaction.
    Returns a summary string.
    """
    # Format messages for summarization
    formatted = "\n".join(
        f"[{m['role'].upper()}]: {m.get('content', '')[:500]}"
        for m in messages
        if isinstance(m.get("content"), str)
    )

    prompt = (
        "Summarize the following conversation in 3-5 bullet points. "
        "Focus on: facts learned about the user, tasks completed, decisions made, and open questions. "
        "Be concise. Output plain bullet points only.\n\n"
        f"{formatted}"
    )

    try:
        import anthropic as ant_sync
        response = model_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


def fire_due_reminders(db_path: str = None) -> list[dict]:
    """
    Find and mark reminders that are due. Returns list of fired reminders.
    Call this from a background scheduler.
    """
    db = _get_db(db_path)
    now = int(time.time())
    rows = db.execute(
        "SELECT id, user_id, label, message FROM reminders WHERE fire_at <= ? AND fired = 0",
        (now,)
    ).fetchall()

    fired = []
    for row in rows:
        db.execute(
            "UPDATE reminders SET fired = 1, fired_at = ? WHERE id = ?",
            (now, row["id"])
        )
        fired.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "label": row["label"],
            "message": row["message"]
        })

    db.commit()
    db.close()
    return fired
