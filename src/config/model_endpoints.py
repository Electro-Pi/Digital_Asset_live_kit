"""Upstream OpenAI-compatible model server URLs (env overrides)."""

from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().strip('"').strip("'")


def _openai_base(url: str) -> str:
    """Ensure OpenAI LLM client base_url ends with /v1."""
    base = url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _http_origin(url: str) -> str:
    """Host origin without a trailing /v1 (STT and TTS servers)."""
    base = url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


STT_BASE_URL = _http_origin(_env("STT_BASE_URL", "http://5.38.206.138:48321"))
STT_MODEL = _env("STT_MODEL", "cohere-transcribe-arabic")
STT_API_KEY = _env("STT_API_KEY") or _env("LLM_API_KEY") or "no-key-needed"

LLM_BASE_URL = _openai_base(_env("LLM_BASE_URL", "http://5.38.206.138:48313"))
LLM_MODEL = _env("LLM_MODEL", "gemma4")
LLM_API_KEY = _env("LLM_API_KEY") or "no-key-needed"

TTS_SERVER_URL = _http_origin(
    _env("TTS_SERVER_URL") or _env("TTS_BASE_URL") or "http://80.81.2.193:41595"
)
TTS_BASE_URL = TTS_SERVER_URL
TTS_MODEL = _env("TTS_MODEL") or "VoxCPM2"
TTS_VOICE = _env("TTS_VOICE") or "hoda"
TTS_SEED = int(_env("TTS_SEED", "1234") or "1234")
TTS_API_KEY = _env("TTS_API_KEY") or "no-key-needed"
TTS_RESPONSE_FORMAT = _env("TTS_RESPONSE_FORMAT", "pcm")
TTS_STRIP_TASHKEEL = _env("TTS_STRIP_TASHKEEL", "0").lower() in {"1", "true", "yes", "on"}

TASHKEEL_ENABLED = _env("TASHKEEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
TASHKEEL_BASE_URL = _openai_base(_env("TASHKEEL_BASE_URL") or LLM_BASE_URL)
TASHKEEL_MODEL = _env("TASHKEEL_MODEL") or LLM_MODEL
TASHKEEL_API_KEY = _env("TASHKEEL_API_KEY") or LLM_API_KEY
TASHKEEL_TIMEOUT_S = float(_env("TASHKEEL_TIMEOUT_S", "8") or "8")
