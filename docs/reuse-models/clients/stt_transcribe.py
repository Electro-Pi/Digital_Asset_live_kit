#!/usr/bin/env python3
"""Call the same STT this agent uses.

Audio should be 16 kHz, 16-bit, mono WAV. Other rates often still work;
this is what the agent sends.

    python clients/stt_transcribe.py clip.wav
    python clients/stt_transcribe.py clip.wav --stream
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import api_key, require, strip_v1  # noqa: E402


def transcribe(wav_path: Path, *, stream: bool = False) -> str:
    base = strip_v1(require("STT_BASE_URL", "https://coda.eracoda.ai/api/tts"))
    files = {"file": (wav_path.name, wav_path.read_bytes(), "audio/wav")}
    data = {
        "model": require("STT_MODEL", "Coda-V2"),
        "language": require("STT_LANGUAGE", "ar"),
        "response_format": "json",
        "stream": "true" if stream else "false",
    }
    headers = {"Authorization": f"Bearer {api_key('STT_API_KEY')}"}

    if not stream:
        resp = httpx.post(
            f"{base}/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files=files,
            timeout=120.0,
        )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()

    text = ""
    with httpx.Client(timeout=120.0) as client:
        with client.stream(
            "POST",
            f"{base}/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files=files,
        ) as resp:
            if resp.status_code != 200:
                raise SystemExit(
                    f"STT {resp.status_code}: {resp.read().decode('utf-8', 'replace')[:600]}"
                )
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or ""
                if delta:
                    text += delta
                    print(delta, end="", flush=True)
    if stream:
        print()
    return text.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Coda STT (same host as سلمى)")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    if not args.wav.is_file():
        raise SystemExit(f"file not found: {args.wav}")
    text = transcribe(args.wav, stream=args.stream)
    if not args.stream:
        print(text)


if __name__ == "__main__":
    main()
