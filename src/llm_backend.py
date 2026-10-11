"""Which LLM answers the call: the Coda gateway, the self-hosted instance, or Gemini.

One env var picks the backend — ``LLM_PROVIDER=coda`` (the default, so an
existing deployment keeps talking to the same vLLM gateway),
``LLM_PROVIDER=instance`` or ``LLM_PROVIDER=gemini``. All three speak the OpenAI chat-completions dialect, so
they reach the agent through the same ``openai.LLM`` client and nothing
downstream — ``llm_node``, the tool guard, the text-tool-call parser — knows
which one is running.

Gemini thinks before it answers unless told not to, and a thinking turn lands
directly in front of سلمى's reply. ``GEMINI_REASONING_EFFORT`` defaults to
``none`` for that reason; raise it only if a turn needs the deliberation more
than the caller needs the second back. The models served on the instance have
all thought by default too, and ``INSTANCE_REASONING_EFFORT`` defaults to
``none`` for the same reason.

A bad value here fails at boot rather than mid-call: a typo in the provider
name must not quietly route the whole deployment to the wrong model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

CODA = "coda"
INSTANCE = "instance"
GEMINI = "gemini"

# What people actually type for each backend.
_PROVIDER_ALIASES = {
    CODA: CODA,
    "vllm": CODA,
    "gemma": CODA,
    "local": CODA,
    INSTANCE: INSTANCE,
    "selfhosted": INSTANCE,
    "self-hosted": INSTANCE,
    GEMINI: GEMINI,
    "google": GEMINI,
}

# Coda's OpenAI-compatible vLLM gateway. Takes /v1 — the client appends
# /chat/completions.
CODA_BASE_URL = "http://8.213.50.155:8001/v1"
CODA_MODEL = "gemma4"
# openai.LLM rejects an empty api_key, so an unauthenticated server gets a
# placeholder instead of nothing.
CODA_PLACEHOLDER_KEY = "no-key-needed"

# The self-hosted vLLM instance. Needs its bearer token (INSTANCE_API_KEY).
# The model behind it changes, so the name is only a default: set
# INSTANCE_MODEL. It served gemma-4-12b (looped on empty thinking blocks) and
# qwen3.5-9b before this one.
INSTANCE_BASE_URL = "http://162.249.226.242:26679/v1"
INSTANCE_MODEL = "mimo-v2.6-9b"

# Gemini's OpenAI-compatibility layer. The trailing slash is part of the
# documented base URL.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = "gemini-3.8-flash"

# "minimal" is an OpenAI level; Gemini 3.x rejects it with a 400. "none" is how
# thinking is switched off.
REASONING_EFFORTS = ("none", "low", "medium", "high")


@dataclass(frozen=True)
class LLMBackend:
    """Everything needed to build the chat client, and nothing else."""

    provider: str
    base_url: str
    model: str
    api_key: str
    # None leaves the server's own default alone.
    reasoning_effort: Optional[str] = None
    temperature: Optional[float] = None
    # OpenAI's strict function schemas mark every property required, which
    # makes Gemini fill the optional ones with nulls and zeros it never meant.
    # Off for anything that is not OpenAI, as livekit's own provider helpers do.
    strict_tool_schema: bool = True
    # Gemini folds every system message into `system_instruction` and then
    # rejects the request because `contents` came out empty. See
    # ensure_non_system_turn.
    needs_non_system_turn: bool = False

    @property
    def api_key_set(self) -> bool:
        """Whether a real key is in play, for logging without leaking it."""
        return bool(self.api_key) and self.api_key != CODA_PLACEHOLDER_KEY

    def openai_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``livekit.plugins.openai.LLM``."""
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "_strict_tool_schema": self.strict_tool_schema,
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return kwargs


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get(name) or default).strip()


def _optional_float(env: Mapping[str, str], name: str) -> Optional[float]:
    raw = _get(env, name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _reasoning_effort(env: Mapping[str, str], name: str, default: str) -> Optional[str]:
    raw = _get(env, name, default).lower()
    # An explicit "off"/"" means: send nothing, let the model decide.
    if raw in ("", "off", "default"):
        return None
    if raw not in REASONING_EFFORTS:
        raise ValueError(
            f"{name}={raw!r} is not a thinking level; use one of "
            f"{', '.join(REASONING_EFFORTS)}, or 'off' to leave it to the model"
        )
    return raw


def resolve_provider(env: Optional[Mapping[str, str]] = None) -> str:
    """The backend name, normalised. Raises on anything unrecognised."""
    env = os.environ if env is None else env
    raw = _get(env, "LLM_PROVIDER", CODA).lower()
    if raw not in _PROVIDER_ALIASES:
        raise ValueError(
            f"LLM_PROVIDER={raw!r} is not a backend; use "
            f"{CODA!r}, {INSTANCE!r} or {GEMINI!r}"
        )
    return _PROVIDER_ALIASES[raw]


def resolve_backend(env: Optional[Mapping[str, str]] = None) -> LLMBackend:
    """Read the environment and settle which LLM this worker will use."""
    env = os.environ if env is None else env
    provider = resolve_provider(env)

    if provider == GEMINI:
        api_key = _get(env, "GEMINI_API_KEY") or _get(env, "GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "LLM_PROVIDER=gemini needs GEMINI_API_KEY (or GOOGLE_API_KEY) set"
            )
        return LLMBackend(
            provider=GEMINI,
            base_url=_get(env, "GEMINI_BASE_URL", GEMINI_BASE_URL),
            model=_get(env, "GEMINI_MODEL", GEMINI_MODEL),
            api_key=api_key,
            reasoning_effort=_reasoning_effort(env, "GEMINI_REASONING_EFFORT", "none"),
            temperature=_optional_float(env, "GEMINI_TEMPERATURE"),
            strict_tool_schema=False,
            needs_non_system_turn=True,
        )

    if provider == INSTANCE:
        api_key = _get(env, "INSTANCE_API_KEY")
        if not api_key:
            raise ValueError("LLM_PROVIDER=instance needs INSTANCE_API_KEY set")
        return LLMBackend(
            provider=INSTANCE,
            base_url=_get(env, "INSTANCE_BASE_URL", INSTANCE_BASE_URL),
            model=_get(env, "INSTANCE_MODEL", INSTANCE_MODEL),
            api_key=api_key,
            reasoning_effort=_reasoning_effort(env, "INSTANCE_REASONING_EFFORT", "none"),
            temperature=_optional_float(env, "INSTANCE_TEMPERATURE"),
        )

    return LLMBackend(
        provider=CODA,
        base_url=_get(env, "LLM_BASE_URL", CODA_BASE_URL),
        model=_get(env, "LLM_MODEL", CODA_MODEL),
        api_key=_get(env, "LLM_API_KEY") or CODA_PLACEHOLDER_KEY,
        reasoning_effort=_reasoning_effort(env, "LLM_REASONING_EFFORT", ""),
        temperature=_optional_float(env, "LLM_TEMPERATURE"),
    )


def _yields_content(item: Any) -> bool:
    """Whether this item becomes an entry in Gemini's ``contents``.

    A system message is folded into ``system_instruction``, and the bookkeeping
    items livekit keeps in the context — ``agent_handoff``,
    ``agent_config_update`` — are dropped during serialisation. Neither leaves
    anything for the model to answer.
    """
    kind = getattr(item, "type", "message")
    if kind in ("function_call", "function_call_output"):
        return True
    if kind != "message":
        return False
    return getattr(item, "role", None) in ("user", "assistant")


def _is_system_message(item: Any) -> bool:
    if getattr(item, "type", "message") != "message":
        return False
    return getattr(item, "role", None) in ("system", "developer")


def ensure_non_system_turn(chat_ctx: Any) -> Any:
    """Give a context with nothing to answer something Gemini can answer.

    The opening turn is ``session.generate_reply(instructions=...)``: livekit
    appends the intro as a second system message and there is no user turn yet,
    so nothing in the context carries content. Gemini's OpenAI layer moves the
    system messages into ``system_instruction``, finds ``contents`` empty and
    answers 400 INVALID_ARGUMENT — which the retry loop turned into the cutout
    fallback, so سلمى opened every call with «معذرة، ما فهمت عليك زين».

    The trailing system message is relabelled as the user turn rather than a
    greeting being invented for the caller: the text that reaches the model is
    exactly the text livekit put there, only in an envelope Gemini accepts.

    Emptiness is judged by what actually serialises. The live context also
    holds an ``agent_handoff`` item from ``session.start``, which carries no
    content but is not a system message either — an earlier version of this
    checked that every item was a system message, so it never fired and the
    400 stood.

    Returns the context and whether it was changed. Call it *after* the turn's
    guards have read the context — they key off the newest user message, and
    the relabelled item would look like one.
    """
    if chat_ctx is None:
        return chat_ctx, False
    items = list(getattr(chat_ctx, "items", None) or [])
    if any(_yields_content(item) for item in items):
        return chat_ctx, False

    index = next(
        (i for i in range(len(items) - 1, -1, -1) if _is_system_message(items[i])),
        None,
    )
    if index is None:
        return chat_ctx, False

    item = items[index]
    copier = getattr(item, "model_copy", None)
    if callable(copier):
        items[index] = copier(update={"role": "user"})
    else:
        try:
            item.role = "user"
        except Exception:  # noqa: BLE001
            return chat_ctx, False
    try:
        chat_ctx.items = items
    except Exception:  # noqa: BLE001
        return chat_ctx, False
    return chat_ctx, True
