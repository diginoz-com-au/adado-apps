"""
AdaDo Agent Loop
The heart of AdaDo: runs an agentic loop where Claude can call tools,
receive results, and continue thinking — just like OpenClaw's agent runtime.

Features:
- Multi-round tool use (up to MAX_TOOL_ROUNDS)
- Auto-compaction: when context overflows, summarize old messages with Haiku
- Model failover: try fallback models on API errors
- Streaming text chunks to client via send_fn
- Full tool execution and result injection
"""

import json
import asyncio
import time
from typing import Optional
import anthropic

from tools.definitions import get_tools_for_user
from tools.executor import execute_tool

MAX_TOOL_ROUNDS = 10

# Models that support tool use (Claude tool_use API)
TOOL_CAPABLE_MODELS = {
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-3-5-sonnet-20241022",
    "claude-3-haiku-20240307",
}

# Fallback model chain — try in order when primary fails
MODEL_FALLBACK_CHAIN = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
]

# Token estimates: rough chars-per-token ratio
CHARS_PER_TOKEN = 4
# Auto-compact when estimated context exceeds this (tokens)
AUTO_COMPACT_THRESHOLD = 90_000
# Keep this many tokens from the tail (recent messages)
KEEP_RECENT_TOKENS = 20_000


def _estimate_tokens(text: str) -> int:
    if not isinstance(text, str):
        try:
            text = json.dumps(text)
        except Exception:
            text = str(text)
    return max(1, len(text) // CHARS_PER_TOKEN)


def _estimate_messages_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += _estimate_tokens(block.get("text", "") or block.get("content", ""))
    return total


async def _compact_messages(
    messages: list,
    soul: str,
    client: anthropic.AsyncAnthropic,
    send_fn,
) -> list:
    """
    Auto-compaction: summarize the oldest portion of messages with Haiku,
    keep the recent tail intact. Like OpenClaw's /compact.
    """
    if not messages:
        return messages

    # Estimate total context size
    total_tokens = _estimate_tokens(soul) + _estimate_messages_tokens(messages)
    if total_tokens < AUTO_COMPACT_THRESHOLD:
        return messages

    # Find split point: keep the recent KEEP_RECENT_TOKENS tokens
    tail_tokens = 0
    split_idx = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        content = m.get("content", "")
        if isinstance(content, list):
            tok = sum(_estimate_tokens(b.get("text", "") or b.get("content", "")) for b in content if isinstance(b, dict))
        else:
            tok = _estimate_tokens(str(content))
        tail_tokens += tok
        if tail_tokens >= KEEP_RECENT_TOKENS:
            split_idx = i
            break

    old_messages = messages[:split_idx]
    recent_messages = messages[split_idx:]

    if not old_messages:
        return messages

    # Notify client compaction is happening
    await send_fn({"type": "system", "message": "Compacting context…"})

    # Format old messages for summarization
    formatted = "\n".join(
        f"[{m['role'].upper()}]: {m['content'][:800] if isinstance(m['content'], str) else '[complex content]'}"
        for m in old_messages
    )
    prompt = (
        "Summarize the following conversation. Focus on: facts, decisions, "
        "completed tasks, and open questions. Be concise (bullet points).\n\n"
        f"{formatted}"
    )

    summary = ""
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        summary = resp.content[0].text.strip() if resp.content else ""
    except Exception:
        summary = f"[{len(old_messages)} earlier messages summarized]"

    # Inject summary as a system-style user message at the start of tail
    summary_msg = {
        "role": "user",
        "content": f"[CONTEXT SUMMARY — earlier conversation]\n{summary}"
    }
    # Immediately follow with a brief assistant ack so message alternation holds
    ack_msg = {
        "role": "assistant",
        "content": "Understood. Continuing with that context."
    }

    compacted = [summary_msg, ack_msg] + recent_messages
    await send_fn({"type": "system", "message": "Context compacted."})
    return compacted


async def _make_api_call(client, model, soul, messages, tools, max_tokens=4096):
    """Streaming API call with model fallback on failure."""
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": soul,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return client.messages.stream(**kwargs)


async def run_agent_loop(
    messages: list,
    soul: str,
    model: str,
    client: anthropic.AsyncAnthropic,
    user_id: int,
    user_context: dict,
    send_fn,
    tools_enabled: bool = True,
) -> tuple[str, int, int]:
    """
    Run the full agentic loop. Streams text chunks via send_fn.
    Handles: tool use, auto-compaction, model failover.
    Returns (full_response_text, input_tokens, output_tokens).
    """
    use_tools = tools_enabled and model in TOOL_CAPABLE_MODELS
    tools = get_tools_for_user(
        tier=user_context.get("tier", "cloud"),
        has_voice=True,
        has_apps=True,
    ) if use_tools else []

    total_input_tokens = 0
    total_output_tokens = 0
    full_text = ""
    loop_messages = list(messages)

    # Auto-compact if context is already large
    loop_messages = await _compact_messages(loop_messages, soul, client, send_fn)

    for round_num in range(MAX_TOOL_ROUNDS):
        tool_uses = []
        current_text = ""
        stop_reason = "end_turn"
        succeeded = False

        # Try primary model, fall back on failure
        models_to_try = [model] + [m for m in MODEL_FALLBACK_CHAIN if m != model]

        for attempt_model in models_to_try:
            try:
                kwargs = {
                    "model": attempt_model,
                    "max_tokens": 4096,
                    "system": soul,
                    "messages": loop_messages,
                }
                if tools:
                    kwargs["tools"] = tools

                async with client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            if hasattr(event.content_block, "type"):
                                if event.content_block.type == "tool_use":
                                    tool_uses.append({
                                        "id": event.content_block.id,
                                        "name": event.content_block.name,
                                        "input": {}
                                    })

                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                current_text += delta.text
                                full_text += delta.text
                                await send_fn({"type": "chunk", "content": delta.text})
                            elif delta.type == "input_json_delta":
                                if tool_uses:
                                    tool_uses[-1]["_raw"] = tool_uses[-1].get("_raw", "") + delta.partial_json

                    final_msg = await stream.get_final_message()
                    total_input_tokens += final_msg.usage.input_tokens
                    total_output_tokens += final_msg.usage.output_tokens

                    for tu in tool_uses:
                        raw = tu.pop("_raw", "{}")
                        try:
                            tu["input"] = json.loads(raw)
                        except Exception:
                            tu["input"] = {}

                    stop_reason = final_msg.stop_reason
                    succeeded = True
                    break  # primary model succeeded

            except anthropic.BadRequestError as e:
                # Context overflow — compact and retry with same model
                if "context" in str(e).lower() or "token" in str(e).lower():
                    loop_messages = await _compact_messages(loop_messages, soul, client, send_fn)
                    # Force compact by pretending we're near threshold
                    break
                raise

            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                if attempt_model == models_to_try[-1]:
                    raise
                # Try next model in chain
                await send_fn({"type": "system", "message": f"Switching to fallback model…"})
                continue

        if not succeeded:
            break

        if not tool_uses or stop_reason != "tool_use":
            break

        # ── Execute tools ──────────────────────────────────────────────────────
        await send_fn({
            "type": "tool_calls",
            "tools": [{"name": tu["name"], "id": tu["id"]} for tu in tool_uses]
        })

        tool_results = []
        for tu in tool_uses:
            await send_fn({"type": "tool_running", "name": tu["name"]})
            result = await execute_tool(
                tool_name=tu["name"],
                tool_input=tu["input"],
                user_id=user_id,
                user_context=user_context
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": result
            })
            await send_fn({"type": "tool_done", "name": tu["name"]})

        # Rebuild assistant content + add tool results
        assistant_content = []
        if current_text:
            assistant_content.append({"type": "text", "text": current_text})
        for tu in tool_uses:
            assistant_content.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu["input"]
            })

        loop_messages.append({"role": "assistant", "content": assistant_content})
        loop_messages.append({"role": "user", "content": tool_results})

        # Check if we need to compact again after tool results bloat the context
        loop_messages = await _compact_messages(loop_messages, soul, client, send_fn)

    return full_text, total_input_tokens, total_output_tokens


async def run_agent_loop_ollama(
    messages: list,
    soul: str,
    model: str,
    ollama_url: str,
    send_fn,
) -> tuple[str, int, int]:
    """
    Simplified Ollama loop (no tool support — Ollama models vary).
    Streams text directly.
    """
    import httpx

    full = ""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": soul}] + messages,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{ollama_url}/v1/chat/completions", json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    try:
                        chunk_data = json.loads(line[6:])
                        delta = chunk_data["choices"][0]["delta"].get("content", "")
                        if delta:
                            full += delta
                            await send_fn({"type": "chunk", "content": delta})
                    except Exception:
                        pass

    input_tok = _estimate_tokens(soul) + _estimate_messages_tokens(messages)
    output_tok = _estimate_tokens(full)
    return full, input_tok, output_tok
