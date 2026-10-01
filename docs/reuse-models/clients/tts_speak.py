#!/usr/bin/env python3
"""Call the same streaming TTS this agent uses.

Writes a 48 kHz 16-bit mono WAV. Server body is raw PCM (no header).

    python clients/tts_speak.py "يا هلا، معك سلمى من لقمة." --out hello.wav
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import api_key, get, require, strip_v1  # noqa: E402

SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2


def synthesize(text: str) -> bytes:
    if not text.strip():
        raise SystemExit("TTS input is empty — the gateway returns 400")

    base = strip_v1(require("TTS_SERVER_URL", "https://coda.eracoda.ai/api/tts"))
    voice = require("TTS_VOICE", "nouf")
    payload: dict = {
        "model": require("TTS_MODEL", "Coda-V2"),
        "input": text,
        "voice": voice,
        "language": "Arabic",
        "response_format": "pcm",
        "stream": True,
        "stream_format": "audio",
        "seed": int(require("TTS_SEED", "1234")),
    }
    ref_audio = get("TTS_REF_AUDIO")
    ref_text = get("TTS_REF_TEXT")
    if ref_audio:
        payload["voice"] = "default"
        payload["ref_audio"] = ref_audio
        if ref_text:
            payload["ref_text"] = ref_text

    with httpx.Client(timeout=httpx.Timeout(None, connect=30.0)) as client:
        with client.stream(
            "POST",
            f"{base}/v1/audio/speech",
            headers={"Authorization": f"Bearer {api_key('TTS_API_KEY')}"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                raise SystemExit(
                    f"TTS {resp.status_code}: {resp.read().decode('utf-8', 'replace')[:600]}"
                )
            return b"".join(resp.iter_bytes())


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


def main() -> None:
    parser = argparse.ArgumentParser(description="Coda TTS (same host as سلمى)")
    parser.add_argument("text")
    parser.add_argument("--out", default="speech.wav")
    args = parser.parse_args()
    pcm = synthesize(args.text)
    out = Path(args.out)
    write_wav(out, pcm)
    print(f"wrote {out} ({len(pcm)} pcm bytes, {SAMPLE_RATE} Hz mono s16le)")


if __name__ == "__main__":
    main()
