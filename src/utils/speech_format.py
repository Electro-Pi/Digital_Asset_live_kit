"""Normalize any text into TTS-safe Arabic prose."""

from __future__ import annotations

import re

_DIGIT_MAP = {
    "0": "صفر",
    "1": "واحد",
    "2": "اثنين",
    "3": "ثلاثة",
    "4": "أربعة",
    "5": "خمسة",
    "6": "ستة",
    "7": "سبعة",
    "8": "ثمانية",
    "9": "تسعة",
}

_ORDINAL_PREFIX = re.compile(
    r"(?:أولاً|أولا|ثانياً|ثانيا|ثالثاً|ثالثا|رابعاً|رابعا|خامساً|خامسا|سادساً|سادسا|أخيراً|أخيرا)\s*[,،:؛\-]?\s*",
    re.IGNORECASE,
)

COMMA_PAUSE_MS = 320
PERIOD_PAUSE_MS = 520

_DETAIL_FOLLOWUP = re.compile(
    r"تفاصيل|أكثر|اكثر|أكتر|اكتر|زيادة|وضح|شرح|توضيح|بالتفصيل",
    re.IGNORECASE,
)

_NUMBERED_ITEM = re.compile(
    r"\b(?:واحد|اثنين|اثنان|ثلاثة|أربعة|خمسة|ستة|سبعة|ثمانية|تسعة|عشرة)\.\s*"
)


def is_detail_followup(message: str) -> bool:
    return bool(_DETAIL_FOLLOWUP.search(message or ""))


def _digits_to_words(text: str) -> str:
    return re.sub(
        r"\d+",
        lambda m: " ".join(_DIGIT_MAP.get(d, d) for d in m.group()),
        text,
    )


def format_for_speech(text: str) -> str:
    """Strip symbols/lists and keep only Arabic prose with commas and periods."""
    if not text:
        return ""

    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\*\*?|__|~~|#+|`+", "", text)
    text = re.sub(r"[@$&%+=^|\\/<>_\[\]\(\)\{\}]", "", text)
    text = _ORDINAL_PREFIX.sub("، ", text)
    text = re.sub(r"[-•·؛:]", "،", text)
    text = re.sub(r"[\n\r\t]+", " ", text)
    text = _digits_to_words(text)
    text = _NUMBERED_ITEM.sub("، ", text)
    text = re.sub(r"[a-zA-Z0-9]", "", text)
    text = re.sub(r"[^أ-ي\s،.]", "", text)
    text = text.replace("؟", ".")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(،\s*)+", "، ", text)
    text = re.sub(r"(\.\s*)+", ". ", text)
    return text.strip(" ،.")


def split_tts_clauses(text: str) -> list[str]:
    """Split formatted prose into short clauses for paced TTS synthesis."""
    formatted = format_for_speech(text)
    if not formatted:
        return []

    parts = re.split(r"([،.])", formatted)
    clauses: list[str] = []
    current = ""
    for part in parts:
        if part in ("،", "."):
            current = f"{current}{part}".strip()
            if current:
                clauses.append(current)
            current = ""
        else:
            current += part
    if current.strip():
        clauses.append(current.strip())
    return clauses


def prepare_voice_reply(text: str, *, max_sentences: int | None = None) -> str:
    """Return the full cleaned text for voice (no mid-sentence cuts)."""
    _ = max_sentences  # kept for call-site compatibility
    formatted = format_for_speech(text)
    if formatted and not formatted.endswith("."):
        formatted += "."
    return formatted


def is_complete_voice_response(text: str) -> bool:
    """Reject answers cut mid-word or mid-sentence (bad cache / backend truncation)."""
    if not text or len(text) < 80:
        return False
    stripped = text.strip()
    if not stripped.endswith("."):
        return False
    last_word = stripped.rstrip(". ،").split()[-1] if stripped.split() else ""
    if len(last_word) <= 3:
        return False
    return True


def split_for_tts_synthesis(text: str, max_chars: int = 200) -> list[str]:
    """Split long prose into OmniVoice-safe chunks (server caps ~20s audio per call)."""
    formatted = format_for_speech(text)
    if not formatted:
        return []
    if len(formatted) <= max_chars:
        return [formatted]

    chunks: list[str] = []
    remaining = formatted
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break

        window = remaining[:max_chars]
        split_at = -1
        for sep in (". ", "، "):
            pos = window.rfind(sep)
            if pos > max_chars // 4:
                split_at = pos + len(sep)
                break
        if split_at <= 0:
            pos = window.rfind(" ")
            split_at = pos + 1 if pos > max_chars // 4 else max_chars

        piece = remaining[:split_at].strip()
        if piece:
            chunks.append(piece)
        remaining = remaining[split_at:].strip()

    return chunks


def pause_ms_for_clause(clause: str) -> int:
    clause = clause.strip()
    if clause.endswith("."):
        return PERIOD_PAUSE_MS
    if clause.endswith("،"):
        return COMMA_PAUSE_MS
    return COMMA_PAUSE_MS // 2


async def format_stream_for_speech(text_stream):
    """Wait for the full LLM reply, then yield one cleaned string for paced TTS."""
    buffer = ""
    async for chunk in text_stream:
        if chunk:
            buffer += chunk

    formatted = format_for_speech(buffer)
    if formatted:
        yield formatted
