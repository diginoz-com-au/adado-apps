"""
AdaDo Tool Executor
Receives tool_use blocks from Claude and executes them.
Returns tool_result content for the next API call.
"""

import os
import json
import math
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import httpx
import zoneinfo

# ── Constants ─────────────────────────────────────────────────────────────────

TTS_URL = os.getenv("TTS_URL", "http://host.docker.internal:8085")
STT_URL = os.getenv("STT_URL", "http://host.docker.internal:8086")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave")  # brave | ddg | serp
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", "/data/adado.db")

# ── Utility ───────────────────────────────────────────────────────────────────

def _ok(data: Any) -> str:
    """Return a JSON success response."""
    return json.dumps(data, ensure_ascii=False)

def _err(msg: str) -> str:
    """Return a JSON error response."""
    return json.dumps({"error": msg})

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Tool Handlers ─────────────────────────────────────────────────────────────

async def handle_web_search(params: dict, user_id: int) -> str:
    query = params.get("query", "").strip()
    num_results = min(int(params.get("num_results", 5)), 10)

    if not query:
        return _err("No search query provided")

    try:
        if BRAVE_API_KEY:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": num_results, "text_decorations": False},
                    headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                for item in data.get("web", {}).get("results", [])[:num_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", "")
                    })
                return _ok({"query": query, "results": results})
        else:
            # Fallback: DuckDuckGo instant answer API
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
                )
                data = resp.json()
                abstract = data.get("AbstractText", "")
                results = [{"title": data.get("Heading", query), "url": data.get("AbstractURL", ""), "snippet": abstract}] if abstract else []
                for r in data.get("RelatedTopics", [])[:num_results - 1]:
                    if isinstance(r, dict) and r.get("Text"):
                        results.append({"title": r.get("Text", "")[:80], "url": r.get("FirstURL", ""), "snippet": r.get("Text", "")})
                return _ok({"query": query, "results": results[:num_results], "note": "Using DuckDuckGo (limited). Set BRAVE_API_KEY for better results."})
    except Exception as e:
        return _err(f"Search failed: {e}")


async def handle_web_fetch(params: dict, user_id: int) -> str:
    url = params.get("url", "").strip()
    max_chars = int(params.get("max_chars", 4000))

    if not url:
        return _err("No URL provided")

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 AdaDo/1.0 (+https://adadoai.com)"}
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

            if "text/html" in content_type:
                # Strip HTML tags
                import re
                text = resp.text
                text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'[\s\n]+', ' ', text).strip()
                text = text[:max_chars]
            elif "text/" in content_type or "json" in content_type:
                text = resp.text[:max_chars]
            else:
                return _err(f"Content type {content_type} is not readable text")

            return _ok({"url": url, "content": text, "chars": len(text)})
    except Exception as e:
        return _err(f"Fetch failed: {e}")


def handle_memory_read(params: dict, user_id: int) -> str:
    query = params.get("query", "").strip()
    limit = min(int(params.get("limit", 5)), 20)

    try:
        db = _get_db()
        # Simple keyword search across key + value
        rows = db.execute(
            "SELECT key, value, category, created_at, updated_at FROM user_memory "
            "WHERE user_id = ? AND (key LIKE ? OR value LIKE ?) "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, f"%{query}%", f"%{query}%", limit)
        ).fetchall()

        if not rows:
            # Return all recent memories if no match
            rows = db.execute(
                "SELECT key, value, category, created_at, updated_at FROM user_memory "
                "WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()

        db.close()
        memories = [
            {"key": r["key"], "value": r["value"], "category": r["category"],
             "updated_at": r["updated_at"]}
            for r in rows
        ]
        return _ok({"memories": memories, "count": len(memories)})
    except Exception as e:
        return _err(f"Memory read failed: {e}")


def handle_memory_write(params: dict, user_id: int) -> str:
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()
    category = params.get("category", "note")

    if not key or not value:
        return _err("Key and value are required")

    try:
        db = _get_db()
        now = int(time.time())
        db.execute(
            "INSERT INTO user_memory (user_id, key, value, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET "
            "value = excluded.value, category = excluded.category, updated_at = excluded.updated_at",
            (user_id, key, value, category, now, now)
        )
        db.commit()
        db.close()
        return _ok({"saved": True, "key": key, "category": category})
    except Exception as e:
        return _err(f"Memory write failed: {e}")


def handle_memory_delete(params: dict, user_id: int) -> str:
    key = params.get("key", "").strip()

    try:
        db = _get_db()
        db.execute("DELETE FROM user_memory WHERE user_id = ? AND key = ?", (user_id, key))
        db.commit()
        db.close()
        return _ok({"deleted": True, "key": key})
    except Exception as e:
        return _err(f"Memory delete failed: {e}")


def handle_reminder_set(params: dict, user_id: int) -> str:
    message = params.get("message", "").strip()
    when_iso = params.get("when_iso", "").strip()
    label = params.get("label", "Reminder").strip()

    if not message or not when_iso:
        return _err("message and when_iso are required")

    try:
        # Parse ISO datetime
        dt = datetime.fromisoformat(when_iso.replace("Z", "+00:00"))
        when_ts = int(dt.timestamp())

        db = _get_db()
        now = int(time.time())
        cur = db.execute(
            "INSERT INTO reminders (user_id, label, message, fire_at, created_at, fired) VALUES (?, ?, ?, ?, ?, 0)",
            (user_id, label, message, when_ts, now)
        )
        db.commit()
        reminder_id = cur.lastrowid
        db.close()

        # Format time for confirmation
        fire_time = datetime.fromtimestamp(when_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return _ok({"set": True, "reminder_id": reminder_id, "label": label, "fire_at": fire_time})
    except Exception as e:
        return _err(f"Reminder set failed: {e}")


def handle_reminder_list(params: dict, user_id: int) -> str:
    include_past = params.get("include_past", False)
    try:
        db = _get_db()
        now = int(time.time())
        if include_past:
            rows = db.execute(
                "SELECT id, label, message, fire_at, fired FROM reminders WHERE user_id = ? ORDER BY fire_at DESC LIMIT 20",
                (user_id,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, label, message, fire_at, fired FROM reminders WHERE user_id = ? AND fire_at > ? AND fired = 0 ORDER BY fire_at ASC LIMIT 20",
                (user_id, now)
            ).fetchall()
        db.close()
        reminders = [
            {"id": r["id"], "label": r["label"], "message": r["message"],
             "fire_at": datetime.fromtimestamp(r["fire_at"], tz=timezone.utc).isoformat(),
             "fired": bool(r["fired"])}
            for r in rows
        ]
        return _ok({"reminders": reminders, "count": len(reminders)})
    except Exception as e:
        return _err(f"Reminder list failed: {e}")


def handle_reminder_delete(params: dict, user_id: int) -> str:
    reminder_id = int(params.get("reminder_id", 0))
    try:
        db = _get_db()
        db.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
        db.commit()
        db.close()
        return _ok({"deleted": True, "reminder_id": reminder_id})
    except Exception as e:
        return _err(f"Reminder delete failed: {e}")


def handle_calculator(params: dict, user_id: int) -> str:
    expr = params.get("expression", "").strip()
    if not expr:
        return _err("No expression provided")

    # Safe evaluation — only allow math operations
    allowed_names = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    allowed_names["abs"] = abs
    allowed_names["round"] = round
    allowed_names["min"] = min
    allowed_names["max"] = max
    allowed_names["sum"] = sum

    try:
        # Compile and check AST before evaluating
        import ast
        tree = ast.parse(expr, mode="eval")
        # Reject any non-math nodes
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
                return _err("Only mathematical expressions are allowed")
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, allowed_names)
        return _ok({"expression": expr, "result": result})
    except ZeroDivisionError:
        return _err("Division by zero")
    except Exception as e:
        return _err(f"Calculation failed: {e}")


def handle_datetime_now(params: dict, user_id: int, user_timezone: str = "UTC") -> str:
    tz_str = params.get("timezone") or user_timezone or "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
        now = datetime.now(tz)
        return _ok({
            "datetime": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "timezone": tz_str,
            "unix_timestamp": int(now.timestamp())
        })
    except Exception as e:
        return _err(f"Timezone error: {e}")


def handle_note_create(params: dict, user_id: int) -> str:
    title = params.get("title", "").strip()
    content = params.get("content", "").strip()
    tags = params.get("tags", [])

    if not title or not content:
        return _err("Title and content are required")

    try:
        db = _get_db()
        now = int(time.time())
        cur = db.execute(
            "INSERT INTO notes (user_id, title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, title, content, json.dumps(tags), now, now)
        )
        db.commit()
        note_id = cur.lastrowid
        db.close()
        return _ok({"created": True, "note_id": note_id, "title": title})
    except Exception as e:
        return _err(f"Note create failed: {e}")


def handle_note_list(params: dict, user_id: int) -> str:
    query = params.get("query", "").strip()
    limit = min(int(params.get("limit", 10)), 50)

    try:
        db = _get_db()
        if query:
            rows = db.execute(
                "SELECT id, title, tags, created_at, updated_at, substr(content, 1, 100) as preview "
                "FROM notes WHERE user_id = ? AND (title LIKE ? OR content LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, f"%{query}%", f"%{query}%", limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, title, tags, created_at, updated_at, substr(content, 1, 100) as preview "
                "FROM notes WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        db.close()
        notes = [
            {"id": r["id"], "title": r["title"], "tags": json.loads(r["tags"] or "[]"),
             "preview": r["preview"], "updated_at": r["updated_at"]}
            for r in rows
        ]
        return _ok({"notes": notes, "count": len(notes)})
    except Exception as e:
        return _err(f"Note list failed: {e}")


def handle_note_read(params: dict, user_id: int) -> str:
    note_id = int(params.get("note_id", 0))
    try:
        db = _get_db()
        row = db.execute(
            "SELECT id, title, content, tags, created_at, updated_at FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id)
        ).fetchone()
        db.close()
        if not row:
            return _err("Note not found")
        return _ok({"id": row["id"], "title": row["title"], "content": row["content"],
                    "tags": json.loads(row["tags"] or "[]"), "created_at": row["created_at"],
                    "updated_at": row["updated_at"]})
    except Exception as e:
        return _err(f"Note read failed: {e}")


def handle_note_delete(params: dict, user_id: int) -> str:
    note_id = int(params.get("note_id", 0))
    try:
        db = _get_db()
        db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        db.commit()
        db.close()
        return _ok({"deleted": True, "note_id": note_id})
    except Exception as e:
        return _err(f"Note delete failed: {e}")


async def handle_tts_speak(params: dict, user_id: int) -> str:
    text = params.get("text", "").strip()
    if not text:
        return _err("No text provided")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TTS_URL}/synthesize",
                params={"text": text, "cache": True}
            )
            if resp.status_code == 200:
                # Return audio URL for client to play
                return _ok({"spoken": True, "text": text, "audio_bytes": len(resp.content)})
            return _err(f"TTS failed: {resp.status_code}")
    except Exception as e:
        return _err(f"TTS error: {e}")


def handle_app_action(params: dict, user_id: int) -> str:
    app_id = params.get("app_id", "").strip()
    action = params.get("action", "").strip()
    # Placeholder — real implementation routes to app-specific handlers
    return _ok({
        "app_id": app_id,
        "action": action,
        "status": "not_connected",
        "message": f"The {app_id} app is not connected yet. "
                   f"Ask the user to connect it in the App Store."
    })


# ── Dispatch Table ─────────────────────────────────────────────────────────────

_HANDLERS = {
    "web_search":     ("async", handle_web_search),
    "web_fetch":      ("async", handle_web_fetch),
    "memory_read":    ("sync",  handle_memory_read),
    "memory_write":   ("sync",  handle_memory_write),
    "memory_delete":  ("sync",  handle_memory_delete),
    "reminder_set":   ("sync",  handle_reminder_set),
    "reminder_list":  ("sync",  handle_reminder_list),
    "reminder_delete":("sync",  handle_reminder_delete),
    "calculator":     ("sync",  handle_calculator),
    "datetime_now":   ("sync",  handle_datetime_now),
    "note_create":    ("sync",  handle_note_create),
    "note_list":      ("sync",  handle_note_list),
    "note_read":      ("sync",  handle_note_read),
    "note_delete":    ("sync",  handle_note_delete),
    "tts_speak":      ("async", handle_tts_speak),
    "app_action":     ("sync",  handle_app_action),
}


async def execute_tool(tool_name: str, tool_input: dict, user_id: int, user_context: dict = None) -> str:
    """
    Execute a tool and return the result string.
    user_context: optional dict with timezone, tier, etc.
    """
    ctx = user_context or {}

    if tool_name not in _HANDLERS:
        return _err(f"Unknown tool: {tool_name}")

    kind, handler = _HANDLERS[tool_name]

    try:
        if tool_name == "datetime_now":
            result = handler(tool_input, user_id, ctx.get("timezone", "UTC"))
        elif kind == "async":
            result = await handler(tool_input, user_id)
        else:
            result = handler(tool_input, user_id)
        return result
    except Exception as e:
        tb = traceback.format_exc()
        return _err(f"Tool {tool_name} crashed: {e}")
