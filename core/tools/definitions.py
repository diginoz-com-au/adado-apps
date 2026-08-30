"""
AdaDo Tool Definitions
Defines every tool Ada can call. Tools are declared here; executors live in executor.py.
Adding a new tool: add its definition here and its handler in executor.py.
"""

# ── Web / Research ────────────────────────────────────────────────────────────

WEB_SEARCH = {
    "name": "web_search",
    "description": (
        "Search the web for current information. Use for facts, news, prices, "
        "anything that may have changed recently or that you are not confident about."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 10)",
                "default": 5
            }
        },
        "required": ["query"]
    }
}

WEB_FETCH = {
    "name": "web_fetch",
    "description": "Fetch and read the text content of a URL. Use when the user shares a link or you need to read a specific page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max characters to return (default 4000)", "default": 4000}
        },
        "required": ["url"]
    }
}

# ── Memory ────────────────────────────────────────────────────────────────────

MEMORY_READ = {
    "name": "memory_read",
    "description": (
        "Read from your persistent memory about this user. "
        "Use at the start of conversations to recall important facts, preferences, "
        "ongoing projects, and context about the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in memory (semantic search)"
            },
            "limit": {
                "type": "integer",
                "description": "Max memory entries to return (default 5)",
                "default": 5
            }
        },
        "required": ["query"]
    }
}

MEMORY_WRITE = {
    "name": "memory_write",
    "description": (
        "Save something important to your persistent memory about this user. "
        "Use to remember: preferences, important facts, ongoing projects, "
        "decisions made, anything worth keeping across sessions. "
        "Be selective — only save genuinely useful long-term information."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Short identifier for this memory (e.g. 'preferred_name', 'current_project')"
            },
            "value": {
                "type": "string",
                "description": "The content to remember"
            },
            "category": {
                "type": "string",
                "description": "Category: preference, fact, project, decision, note",
                "enum": ["preference", "fact", "project", "decision", "note"],
                "default": "note"
            }
        },
        "required": ["key", "value"]
    }
}

MEMORY_DELETE = {
    "name": "memory_delete",
    "description": "Delete a memory entry. Use when the user asks you to forget something or when information is no longer relevant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The key of the memory to delete"}
        },
        "required": ["key"]
    }
}

# ── Scheduling / Reminders ────────────────────────────────────────────────────

REMINDER_SET = {
    "name": "reminder_set",
    "description": (
        "Set a reminder for the user. The user will be notified at the specified time. "
        "Use for any 'remind me', 'don't forget', 'tell me when' type requests."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The reminder message to send to the user"
            },
            "when_iso": {
                "type": "string",
                "description": "When to send the reminder — ISO 8601 datetime string (e.g. 2026-09-01T09:00:00+09:30)"
            },
            "label": {
                "type": "string",
                "description": "Short label for the reminder (e.g. 'Doctor appointment')"
            }
        },
        "required": ["message", "when_iso", "label"]
    }
}

REMINDER_LIST = {
    "name": "reminder_list",
    "description": "List all pending reminders for the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "include_past": {
                "type": "boolean",
                "description": "Include past/fired reminders",
                "default": False
            }
        }
    }
}

REMINDER_DELETE = {
    "name": "reminder_delete",
    "description": "Cancel/delete a reminder.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reminder_id": {"type": "integer", "description": "ID of the reminder to delete"}
        },
        "required": ["reminder_id"]
    }
}

# ── Calculator / Utilities ────────────────────────────────────────────────────

CALCULATOR = {
    "name": "calculator",
    "description": "Perform calculations. Use for any maths that needs to be precise.",
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Python math expression to evaluate (e.g. '245 * 1.1 + 50')"
            }
        },
        "required": ["expression"]
    }
}

DATETIME_NOW = {
    "name": "datetime_now",
    "description": "Get the current date and time in the user's timezone. Use whenever you need to know what time it is now.",
    "input_schema": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone (e.g. 'Australia/Adelaide'). Uses user's timezone if not specified."
            }
        }
    }
}

# ── Notes ─────────────────────────────────────────────────────────────────────

NOTE_CREATE = {
    "name": "note_create",
    "description": "Create a note for the user. Use when the user wants to save text, ideas, or information.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title of the note"},
            "content": {"type": "string", "description": "Content of the note"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for the note"
            }
        },
        "required": ["title", "content"]
    }
}

NOTE_LIST = {
    "name": "note_list",
    "description": "List or search the user's notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (optional)"},
            "limit": {"type": "integer", "default": 10}
        }
    }
}

NOTE_READ = {
    "name": "note_read",
    "description": "Read the full content of a specific note.",
    "input_schema": {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "Note ID"}
        },
        "required": ["note_id"]
    }
}

NOTE_DELETE = {
    "name": "note_delete",
    "description": "Delete a note.",
    "input_schema": {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "Note ID to delete"}
        },
        "required": ["note_id"]
    }
}

# ── TTS / Voice ───────────────────────────────────────────────────────────────

TTS_SPEAK = {
    "name": "tts_speak",
    "description": (
        "Convert text to speech audio. Use when the user asks for spoken output "
        "or when a voice response would be more appropriate than text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to speak aloud"}
        },
        "required": ["text"]
    }
}

# ── App Actions ───────────────────────────────────────────────────────────────

APP_ACTION = {
    "name": "app_action",
    "description": (
        "Perform an action in a connected app (email, calendar, tasks, etc.). "
        "Use when the user wants to do something in one of their connected apps."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "app_id": {
                "type": "string",
                "description": "The app to act on (e.g. 'email', 'calendar', 'tasks', 'notes')"
            },
            "action": {
                "type": "string",
                "description": "The action to perform (e.g. 'send', 'create', 'list', 'delete', 'update')"
            },
            "params": {
                "type": "object",
                "description": "Action-specific parameters"
            }
        },
        "required": ["app_id", "action"]
    }
}

# ── All tools by tier ─────────────────────────────────────────────────────────

# Tools available to all users
CORE_TOOLS = [
    DATETIME_NOW,
    CALCULATOR,
    MEMORY_READ,
    MEMORY_WRITE,
    MEMORY_DELETE,
    REMINDER_SET,
    REMINDER_LIST,
    REMINDER_DELETE,
    NOTE_CREATE,
    NOTE_LIST,
    NOTE_READ,
    NOTE_DELETE,
]

# Tools requiring internet access (all tiers with connectivity)
NETWORK_TOOLS = [
    WEB_SEARCH,
    WEB_FETCH,
]

# Tools requiring TTS service
VOICE_TOOLS = [
    TTS_SPEAK,
]

# App integration tools
APP_TOOLS = [
    APP_ACTION,
]


def get_tools_for_user(tier: str = "cloud", has_voice: bool = False, has_apps: bool = False) -> list:
    """Return the tool list for a given user tier."""
    tools = CORE_TOOLS + NETWORK_TOOLS

    if has_voice:
        tools = tools + VOICE_TOOLS

    if has_apps:
        tools = tools + APP_TOOLS

    return tools
