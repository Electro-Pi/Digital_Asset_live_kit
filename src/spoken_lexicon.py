"""Light cleanup before TTS. Pronunciation is left to the speech model."""

from __future__ import annotations

import re

_TOOL_CALL_TEXT_RE = re.compile(r"\bcall\s*:", re.IGNORECASE)
_TOOL_CALL_MARKER_RE = re.compile(r"<\s*tool_call\b", re.IGNORECASE)

# A capability ("I can look") is not a promise to come back later.
_CAPABILITY_LOOKUP_RE = re.compile(
    r"\b(?:و?اقدر|و?نقدر|يمكنني|بامكاني)\s+"
    r"(?:(?:اني|ان|كمان|بعد|ايضا)\s+)?"
    r"(?:ادور\s+لك|اشوف\s+(?:لك|العروض)|اشيك(?:\s+لك)?)\b"
)
_DESCRIPTIVE_LOOKUP_RE = re.compile(
    r"\b(?:ادور\s+لك|اشوف\s+لك|اشيك\s+لك)\b"
    r"(?=\s+(?:(?:على|علي)\s+)?(?:احسن\s+)?"
    r"(?:الوجبات|الوجبه|الخيارات|العروض)\s+حسب\b)"
)


def strip_speech_artifacts(text: str) -> str:
    """Drop a leaked tool call. Spoken words are otherwise unchanged."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    if _TOOL_CALL_TEXT_RE.search(raw) or _TOOL_CALL_MARKER_RE.search(raw):
        return ""
    return raw


def mask_capability_lookups(folded: str) -> str:
    """Hide capability wording from stall checks. Does not change what is spoken."""
    return _DESCRIPTIVE_LOOKUP_RE.sub(" ", _CAPABILITY_LOOKUP_RE.sub(" ", folded))


def clean_for_speech(text: str) -> str:
    """Hand the speech model the words as written."""
    return strip_speech_artifacts(text)


def finalize_prosody(text: str) -> str:
    return " ".join(str(text or "").split())


def sanitize_tashkeel(text: str) -> str:
    return str(text or "")
