"""Tuned ``extra_body`` for Najdi Arabic (OmniVoice language id ``ars``).

Based on k2-fsa/OmniVoice ``model.generate`` / ``OmniVoiceGenerationConfig``:
https://github.com/k2-fsa/OmniVoice/blob/master/docs/generation-parameters.md

Language id ``ars`` = Najdi Arabic:
https://github.com/k2-fsa/OmniVoice/blob/master/docs/languages.md

These keys are merged into ``POST /v1/audio/speech`` via the OpenAI SDK ``extra_body``.
Your gateway must forward them into the synthesizer; unknown keys can cause HTTP 400 —
trim ``najdi_omnivoice_speech_extra_body()`` or add server-side filtering if needed.

Environment overrides (optional): ``OMNIVOICE_LANGUAGE``, ``OMNIVOICE_INSTRUCTIONS``,
``OMNIVOICE_NUM_STEP``, ``OMNIVOICE_GUIDANCE_SCALE``, ``OMNIVOICE_DENOISE``,
``OMNIVOICE_T_SHIFT``, ``OMNIVOICE_POSITION_TEMPERATURE``, ``OMNIVOICE_CLASS_TEMPERATURE``,
``OMNIVOICE_LAYER_PENALTY_FACTOR``, ``OMNIVOICE_AUDIO_CHUNK_DURATION``,
``OMNIVOICE_AUDIO_CHUNK_THRESHOLD``, ``OMNIVOICE_PREPROCESS_PROMPT``,
``OMNIVOICE_POSTPROCESS_OUTPUT``, ``OMNIVOICE_DURATION``, ``OMNIVOICE_GEN_SPEED``.
"""

from __future__ import annotations

import os
from typing import Any

_DEFAULT_INSTRUCTIONS = (
    "Speak in natural Najdi Arabic: Saudi Gulf colloquial, clear consonants, "
    "warm professional restaurant tone, moderate pace; avoid switching to "
    "Modern Standard Arabic unless the text is already formal."
)


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    return default if v is None else v


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        return default
    return int(v)


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        return default
    return float(v)


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def najdi_omnivoice_speech_extra_body() -> dict[str, Any]:
    """Return JSON fields merged into each TTS request (``extra_body``)."""

    body: dict[str, Any] = {
        "language": _env_str("OMNIVOICE_LANGUAGE", "ars"),
        # decoding — more steps than default 32 for cleaner masked diffusion
        "num_step": _env_int("OMNIVOICE_NUM_STEP", 20),
        "guidance_scale": _env_float("OMNIVOICE_GUIDANCE_SCALE", 2.25),
        "denoise": _env_bool("OMNIVOICE_DENOISE", True),
        "t_shift": _env_float("OMNIVOICE_T_SHIFT", 0.1),
        # sampling — lower position_temperature than default 5.0 for steadier timbre
        "position_temperature": _env_float("OMNIVOICE_POSITION_TEMPERATURE", 3.5),
        "class_temperature": _env_float("OMNIVOICE_CLASS_TEMPERATURE", 0.0),
        "layer_penalty_factor": _env_float("OMNIVOICE_LAYER_PENALTY_FACTOR", 5.0),
        # long-form chunking — slightly tighter chunks for more stable prosody per segment
        "audio_chunk_duration": _env_float("OMNIVOICE_AUDIO_CHUNK_DURATION", 12.0),
        "audio_chunk_threshold": _env_float("OMNIVOICE_AUDIO_CHUNK_THRESHOLD", 28.0),
        "preprocess_prompt": _env_bool("OMNIVOICE_PREPROCESS_PROMPT", True),
        "postprocess_output": _env_bool("OMNIVOICE_POSTPROCESS_OUTPUT", True),
    }

    instr = _env_str("OMNIVOICE_INSTRUCTIONS", _DEFAULT_INSTRUCTIONS).strip()
    if instr:
        body["instructions"] = instr

    dur = os.getenv("OMNIVOICE_DURATION")
    if dur and str(dur).strip():
        body["duration"] = float(dur)

    # OmniVoice ``generate`` speed factor (not OpenAI ``speed`` on the outer client)
    gsp = os.getenv("OMNIVOICE_GEN_SPEED")
    if gsp and str(gsp).strip():
        body["speed"] = float(gsp)

    return body
