"""Resolve the female/male TTS voice and normalize voice selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from loguru import logger

# Both speakers are registered server-side on the VoxCPM2 server:
# GET /v1/audio/voices lists "nouf" (female, نوف) and "nawaf" (male, نواف).
#
# The male speaker used to be off because it was NOT a server-side voice — it
# was produced by sending a 4.5MB ref-audio/male.wav clone with every request,
# which measured ~45s to first audio versus ~0.16s for a precomputed voice, and
# that is unusable in a live conversation. Registering `nawaf` upstream removed
# the clone entirely: measured 0.33s to first byte, on par with nouf's 0.30s.
# So the male speaker is back on, and neither voice sends a reference any more.
MALE_VOICE_ENABLED = True

# Server-side voice names. Overridable from the environment via agent.py
# (TTS_VOICE / TTS_VOICE_MALE) — an unregistered name is a hard 400 upstream.
DEFAULT_FEMALE_VOICE = "nouf"
DEFAULT_MALE_VOICE = "nawaf"

# Diagnostic F0 bands (logged only; not a hard gate). Measured medians on the
# registered voices: nouf 215 Hz, nawaf 114 Hz.
FEMALE_F0 = (135.0, 340.0)
MALE_F0 = (70.0, 180.0)


@dataclass(frozen=True)
class VoiceProfile:
    """Resolved TTS + persona settings for one live session / TTS request."""

    key: str  # "female" | "male"
    display_name_ar: str
    tts_voice: str
    expected_voice: str
    voice_min_f0: float
    voice_max_f0: float


def normalize_voice(raw: Optional[str]) -> str:
    """Map UI / metadata aliases onto the canonical female|male keys."""
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


def voice_profile(
    raw: Optional[str] = None,
    *,
    female_tts_voice: str = DEFAULT_FEMALE_VOICE,
    male_tts_voice: str = DEFAULT_MALE_VOICE,
) -> VoiceProfile:
    key = normalize_voice(raw)
    if key == "male":
        return VoiceProfile(
            key="male",
            display_name_ar="نواف",
            tts_voice=male_tts_voice,
            expected_voice=male_tts_voice,
            voice_min_f0=MALE_F0[0],
            voice_max_f0=MALE_F0[1],
        )
    return VoiceProfile(
        key="female",
        display_name_ar="نوف",
        tts_voice=female_tts_voice,
        expected_voice=female_tts_voice,
        voice_min_f0=FEMALE_F0[0],
        voice_max_f0=FEMALE_F0[1],
    )
