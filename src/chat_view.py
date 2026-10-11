"""Read-only views of a LiveKit chat context.

Tools and the LLM node read the turn in progress through these helpers. None of
them decide anything; they only find items (the last user message, this
turn's tool calls and outputs) in a context that may be a LiveKit
``ChatContext``, a list, or the dict-shaped stand-ins the tests use.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterable, Optional

# Tool outputs longer than this are clipped in the copy sent to the LLM (the
# session history keeps them whole), and only the newest items are sent.
LLM_TOOL_OUTPUT_MAX_CHARS = 4000
LLM_CHAT_MAX_ITEMS = 40


def _get(item: Any, key: str) -> Any:
    value = getattr(item, key, None)
    if value is None and isinstance(item, dict):
        value = item.get(key)
    return value


def chat_items(chat_ctx: Any) -> list[Any]:
    items = getattr(chat_ctx, "items", None)
    if items is None:
        items = getattr(chat_ctx, "messages", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    return list(items or [])


def message_text(item: Any) -> str:
    """Plain text of a chat item (LiveKit stores text_content or content[])."""
    text = getattr(item, "text_content", None) if item is not None else None
    if not text and isinstance(item, dict):
        text = item.get("text_content")
    if not text:
        content = _get(item, "content")
        if content is None and isinstance(item, dict):
            content = item.get("text")
        if isinstance(content, list):
            text = " ".join(p if isinstance(p, str) else str(getattr(p, "text", "") or p or "") for p in content)
        elif content is not None:
            text = str(content)
    return str(text or "").strip()


def last_user_text(chat_ctx: Any) -> str:
    """Newest user utterance."""
    for item in reversed(chat_items(chat_ctx)):
        if _get(item, "role") == "user":
            text = message_text(item)
            if text:
                return text
    return ""


def items_since_last_user(chat_ctx: Any) -> list[Any]:
    items = chat_items(chat_ctx)
    last_user = -1
    for i, item in enumerate(items):
        if _get(item, "role") == "user":
            last_user = i
    return items[last_user + 1:]


def _tool_names(item: Any) -> list[str]:
    names = [str(_get(item, "name"))] if _get(item, "name") else []
    for call in _get(item, "tool_calls") or []:
        name = _get(call, "name")
        if name:
            names.append(str(name))
    return names


def _arguments(item: Any) -> Optional[dict[str, Any]]:
    args = _get(item, "arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            return None
    return args if isinstance(args, dict) else None


def tool_calls_since_last_user(chat_ctx: Any, names: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    """``(name, arguments)`` for each call of these tools already made this turn."""
    wanted = set(names or ())
    found: list[tuple[str, dict[str, Any]]] = []
    for item in items_since_last_user(chat_ctx):
        if _get(item, "type") == "function_call_output":
            continue
        for name in _tool_names(item):
            if name in wanted:
                found.append((name, _arguments(item) or {}))
    return found


def tool_called_since_last_user(chat_ctx: Any, tool_name: str) -> bool:
    return any(tool_name in _tool_names(item) for item in items_since_last_user(chat_ctx))


def parse_tool_output(raw: Any) -> dict[str, Any]:
    """A function_call_output payload as a dict ({} when it is not one).

    LiveKit stores ``str(dict)`` (a Python repr), not JSON, so both are tried.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    for parse in (json.loads, ast.literal_eval):
        try:
            data = parse(raw.strip())
        except (ValueError, TypeError, SyntaxError, MemoryError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def tool_outputs_since_last_user(chat_ctx: Any) -> list[dict[str, Any]]:
    return [
        parse_tool_output(_get(item, "output"))
        for item in items_since_last_user(chat_ctx)
        if _get(item, "type") == "function_call_output" or _get(item, "output") is not None
    ]


def has_tool_activity_since_last_user(chat_ctx: Any) -> bool:
    for item in items_since_last_user(chat_ctx):
        if _get(item, "type") in {"function_call", "function_call_output"} or _get(item, "name"):
            return True
    return False


def prepare_chat_ctx_for_llm(chat_ctx: Any) -> Any:
    """A copy of the history for the LLM: long tool outputs clipped, old items dropped."""
    if chat_ctx is None:
        return chat_ctx
    copy_fn = getattr(chat_ctx, "copy", None)
    ctx = copy_fn() if callable(copy_fn) else chat_ctx
    items = getattr(ctx, "items", None)
    if items is None:
        return ctx
    compacted: list[Any] = []
    for item in list(items):
        if getattr(item, "type", None) == "function_call_output":
            out = getattr(item, "output", "") or ""
            if len(out) > LLM_TOOL_OUTPUT_MAX_CHARS:
                # Tools are meant to stay well under this; clipping cuts a
                # result mid-structure, so it is logged as a defect.
                from loguru import logger

                logger.warning("Tool output clipped for the LLM chars={} limit={}", len(out), LLM_TOOL_OUTPUT_MAX_CHARS)
                clipped = out[:LLM_TOOL_OUTPUT_MAX_CHARS] + "…[truncated]"
                copier = getattr(item, "model_copy", None)
                item = copier(update={"output": clipped}) if callable(copier) else item
        compacted.append(item)
    try:
        ctx.items = compacted
    except Exception:  # noqa: BLE001 - a read-only stand-in keeps its items
        pass
    truncate = getattr(ctx, "truncate", None)
    if callable(truncate):
        truncate(max_items=LLM_CHAT_MAX_ITEMS)
    return ctx


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def known_amounts(chat_ctx: Any) -> set[int]:
    """Every amount the model has been given: in tool outputs and instructions.

    A price she says must be one of these. Her own earlier replies and the
    caller's words are not sources, so an invented price cannot launder itself
    by being repeated.
    """
    from src.speech_claims import spoken_amounts

    amounts: set[int] = set()
    for item in chat_items(chat_ctx):
        if _get(item, "type") == "function_call_output" or _get(item, "output") is not None:
            text = str(_get(item, "output") or "")
        elif _get(item, "role") in {"system", "developer"}:
            text = message_text(item)
        else:
            continue
        amounts.update(int(round(float(n))) for n in _NUMBER.findall(text))
        amounts.update(spoken_amounts(text))
    return amounts
