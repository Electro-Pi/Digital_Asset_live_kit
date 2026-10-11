"""Tool calls the model wrote as text, and the calls a turn asked for.

Some backends print a call into the reply instead of using the function-calling
channel (Gemma: ``call:show_meal{meal_id:…}<tool_call|>``). Turning that leak
into a real call makes the action happen, and removing it from the text keeps
the caller from hearing ``call:show_meal``. This is a parser, not flow
control: it never adds a call the model did not write.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterable, Optional

# Gemma / OpenAI-compat wrappers leave these control crumbs inside the args.
_CTRL_TOKEN_RE = re.compile(r"<\|?[^<>|]{0,40}\|?>")
# call:name{…} — the body is non-greedy so a trailing <tool_call|> does not
# get swallowed into the args. Nested braces are not used by our tools.
_CALL_RE = re.compile(
    r"\bcall\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}\s*(?:<\s*tool_call\|?>)?",
    re.IGNORECASE | re.DOTALL,
)
# key:value pairs. Value runs until the next ,key: or end.
_KV_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)(?=\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*:|$)",
    re.DOTALL,
)
# Tashkeel marks for Arabic text folding and cleaning.
_MARKS = re.compile(r"[ً-ْٰـ]")


def _clean_arg_value(raw: str) -> Any:
    text = _CTRL_TOKEN_RE.sub("", str(raw or "")).strip()
    text = text.strip().strip("\"'`").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"none", "null", "nil"}:
        return ""
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return text


def _parse_args_body(body: str) -> dict[str, Any]:
    cleaned = _CTRL_TOKEN_RE.sub("", body or "")
    cleaned = cleaned.strip().strip(",")
    if not cleaned:
        return {}
    # Prefer real JSON when the model managed to emit it.
    if cleaned.startswith("{"):
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    args: dict[str, Any] = {}
    for match in _KV_RE.finditer(cleaned):
        key = match.group(1).strip()
        args[key] = _clean_arg_value(match.group(2))
    return args


def extract_text_tool_calls(
    text: str,
    *,
    allowed_names: Optional[Iterable[str]] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Split leaked ``call:name{…}`` invocations out of assistant text.

    Returns ``(speech_text, calls)`` where each call is
    ``{"name": str, "arguments": dict, "call_id": str}``.
    Unknown tool names are dropped (left out of speech too, so the user never
    hears the raw call).
    """
    raw = str(text or "")
    if not raw.strip():
        return "", []

    allow = {n for n in (allowed_names or []) if n} or None
    calls: list[dict[str, Any]] = []
    pieces: list[str] = []
    cursor = 0
    for match in _CALL_RE.finditer(raw):
        pieces.append(raw[cursor:match.start()])
        cursor = match.end()
        name = match.group(1).strip()
        if allow is not None and name not in allow:
            continue
        args = _parse_args_body(match.group(2))
        calls.append(
            {
                "name": name,
                "arguments": args,
                "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
            }
        )
    pieces.append(raw[cursor:])
    speech = " ".join(" ".join(pieces).split()).strip()
    # A turn that was *only* a leaked tool call must stay silent.
    if re.search(r"\bcall\s*:", speech, re.IGNORECASE) or re.search(
        r"<\s*tool_call\b", speech, re.IGNORECASE
    ):
        speech = ""
    return speech, calls


def contains_text_tool_call(text: str) -> bool:
    """True when the text looks like a leaked tool invocation."""
    if not text:
        return False
    if _CALL_RE.search(text):
        return True
    if re.search(r"\bcall\s*:", text, re.IGNORECASE):
        return True
    if re.search(r"<\s*tool_call\b", text, re.IGNORECASE):
        return True
    return False


def collect_requested_calls(
    pending: Iterable[Any], leaked: Iterable[Any] = ()
) -> tuple[list[tuple[str, Any, str]], list[Any], dict[str, Any]]:
    """Every call a turn wants to make, in the order the model produced them.

    Returns the ``(name, arguments, call_id)`` triples the tool guard
    validates, the chunks that carried no call (to pass through untouched),
    and the provider data riding on each call, keyed by call_id.

    That last one is the reason this is a function. Gemini 3 returns a
    ``thought_signature`` with every function call and rejects the *next*
    request if it comes back without one — "Function call is missing a
    thought_signature", 400, four retries, then the cutout fallback, heard as
    سلمى apologising immediately after the tool ran. livekit carries the
    signature from the stream onto the chat item and back out again; the guard
    rebuilds each approved call from name and arguments alone, so it drops on
    the floor unless it is carried across here. A backend that sends none
    (Coda) leaves the map empty and nothing changes.

    Calls recovered from assistant text carry no provider data — they were
    never a real function call — and are appended after the real ones.
    """
    requested: list[tuple[str, Any, str]] = []
    passthrough: list[Any] = []
    extras: dict[str, Any] = {}

    for chunk in pending:
        delta = getattr(chunk, "delta", None)
        calls = getattr(delta, "tool_calls", None) if delta is not None else None
        if not calls:
            passthrough.append(chunk)
            continue
        for call in calls:
            call_id = (
                str(getattr(call, "call_id", "") or "")
                or f"call_{uuid.uuid4().hex[:10]}"
            )
            extra = getattr(call, "extra", None)
            if extra:
                extras[call_id] = extra
            requested.append(
                (
                    str(getattr(call, "name", "") or ""),
                    getattr(call, "arguments", None),
                    call_id,
                )
            )

    for call in leaked:
        requested.append(
            (
                str(call.get("name") or ""),
                call.get("arguments"),
                str(call.get("call_id") or "") or f"call_{uuid.uuid4().hex[:10]}",
            )
        )

    return requested, passthrough, extras
