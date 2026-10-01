#!/usr/bin/env python3
"""Call the same OpenAI-compatible LLM this agent uses.

    python clients/llm_chat.py "قل مرحبا بجملة واحدة"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import api_key, require  # noqa: E402


def chat(
    prompt: str,
    *,
    system: str | None = None,
    tools: list[dict] | None = None,
) -> dict:
    base = require("LLM_BASE_URL", "https://coda.eracoda.ai/api/tts/v1").rstrip("/")
    model = require("LLM_MODEL", "Coda-V2")
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": float(require("LLM_TEMPERATURE", "0.6")),
        "max_tokens": int(require("LLM_MAX_TOKENS", "400")),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key('LLM_API_KEY')}"},
        json=body,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Coda LLM chat (same host as سلمى)")
    parser.add_argument("prompt", nargs="?", default="قل مرحبا بجملة واحدة")
    parser.add_argument("--system", default="")
    args = parser.parse_args()
    message = chat(args.prompt, system=args.system or None)
    print(json.dumps(message, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
