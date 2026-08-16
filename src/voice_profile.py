"""Resolve female/male TTS clone assets and normalize voice selection."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_AUDIO_DIR = REPO_ROOT / "ref-audio"

# The male speaker is off: this deployment is female-only.
#
# Male is not a server-side voice — it is produced by sending a 4.5MB
# ref-audio/male.wav clone with every request. On the VoxCPM2 server that path
# measures ~45s to first audio (reproduced across requests) versus ~0.16s for
# the precomputed `hoda` voice, which is unusable for a live conversation.
#
# Nothing about the male path is deleted, so re-enabling is this one flag plus a
# fix for the clone latency (a server-side registered voice would be the fix).
MALE_VOICE_ENABLED = False

# Female uses the TTS server's precomputed `hoda` voice (no per-request ref).
# Male clones from local ref-audio/male.wav + male-ref-text each request.
MALE_WAV_PATH = REF_AUDIO_DIR / "male.wav"
MALE_REF_TEXT_PATH = REF_AUDIO_DIR / "male-ref-text"

# Diagnostic F0 bands (logged only; not a hard gate).
FEMALE_F0 = (135.0, 340.0)
MALE_F0 = (70.0, 180.0)


@dataclass(frozen=True)
class VoiceProfile:
    """Resolved TTS + persona settings for one live session / TTS request."""

    key: str  # "female" | "male"
    display_name_ar: str
    tts_voice: str
    ref_audio: Optional[str]
    ref_text: Optional[str]
    expected_voice: str
    voice_min_f0: float
    voice_max_f0: float


def normalize_voice(raw: Optional[str]) -> str:
    """Map UI / metadata aliases onto the canonical female|male keys.

    With MALE_VOICE_ENABLED off this always answers "female", so a male request
    from the frontend is served by the female voice rather than failing.
    """
    v = (raw or "female").strip().lower()
    if v in {"male", "nawaf", "نواف", "m"}:
        if not MALE_VOICE_ENABLED:
            logger.info(
                "Voice selection {!r} requested but the male speaker is disabled "
                "on this deployment; using the female voice",
                raw,
            )
            return "female"
        return "male"
    # coda / nouf / female / anything else → female production voice
    return "female"


def parse_voice_from_metadata(metadata: Optional[str]) -> str:
    if not metadata:
        return "female"
    try:
        meta = json.loads(metadata)
    except (ValueError, TypeError):
        return "female"
    if not isinstance(meta, dict):
        return "female"
    return normalize_voice(meta.get("voice"))


def _male_ref_audio_data_uri() -> str:
    raw = MALE_WAV_PATH.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:audio/wav;base64,{b64}"


def _male_ref_text() -> str:
    return MALE_REF_TEXT_PATH.read_text(encoding="utf-8").strip()


def voice_profile(
    raw: Optional[str] = None,
    *,
    female_tts_voice: str = "hoda",
) -> VoiceProfile:
    key = normalize_voice(raw)
    if key == "male":
        if not MALE_WAV_PATH.is_file():
            raise FileNotFoundError(f"Male reference audio missing: {MALE_WAV_PATH}")
        if not MALE_REF_TEXT_PATH.is_file():
            raise FileNotFoundError(f"Male reference text missing: {MALE_REF_TEXT_PATH}")
        return VoiceProfile(
            key="male",
            display_name_ar="نواف",
            tts_voice="default",
            ref_audio=_male_ref_audio_data_uri(),
            ref_text=_male_ref_text(),
            expected_voice="default",
            voice_min_f0=MALE_F0[0],
            voice_max_f0=MALE_F0[1],
        )
    return VoiceProfile(
        key="female",
        display_name_ar="سلمى",
        tts_voice=female_tts_voice,
        ref_audio=None,
        ref_text=None,
        expected_voice=female_tts_voice,
        voice_min_f0=FEMALE_F0[0],
        voice_max_f0=FEMALE_F0[1],
    )
