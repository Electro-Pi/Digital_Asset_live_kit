"""Normalize any text into TTS-safe Arabic prose."""

from __future__ import annotations

import os
import re

# ── Prosody finalization (from Digital_Asset_live_kit/spoken_lexicon.py) ─────
#
# VoxCPM holds pitch up on a trailing comma/ellipsis, making every reply
# sound like it's about to keep talking. Forcing a period-ended contour
# prevents that.

_TAG_QUESTION_RE = re.compile(
    r"[،,]\s*(?=(?:تِ?بِ?(?:ي|يه)|وِ?ش|تَب(?:غى|ي)|ودّ?ك|تِرِيد))"
)
_TRAILING_OPEN_RE = re.compile(r"[،,;…]+(?:\s*[\"'»«)])*\s*$")
_HAS_TERMINAL_RE = re.compile(r"[.!?؟۔]\s*$")
_AR_WORD_BOUNDARY = r"(?<![ء-ي]){src}(?![ء-ي])"


def finalize_prosody(text: str) -> str:
    """Force a falling/closed contour so turns don't end on a continuation rise.

    Splits tag-questions onto their own sentence, removes trailing commas/
    ellipses, and ensures the text ends with a terminal punctuation mark.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return text
    text = _TAG_QUESTION_RE.sub(". ", text)
    text = _TRAILING_OPEN_RE.sub(".", text)
    if not _HAS_TERMINAL_RE.search(text):
        text = text + "."
    return text


_TASHKEEL_FIXES: tuple[tuple[str, str], ...] = (
    ("تصعد", "تُصَعِّد"),
    ("عداد", "عَدَّاد"),
    ("السداد", "السَّدَاد"),
    ("الهيئة", "الهَيْئَة"),
    ("الشكوى", "الشَّكْوى"),
    ("شكوى", "شَكْوى"),
    ("التعريفة", "التَّعْرِيفَة"),
    ("الوحدات السكنية", "الوَحَدات السَّكَنِيَّة"),
    ("حرية المعلومات", "حُرِّيَّة المَعْلُومات"),
    ("مقدم الخدمة", "مُقَدِّم الخِدْمَة"),
    ("البوابة الإلكترونية", "البَوَّابَة الإِلِكْتُرُونِيَّة"),
)


def apply_tashkeel_fixes(text: str) -> str:
    """Pin critical domain words to stable spoken forms."""
    if not text:
        return ""
    out = text
    for src, dst in _TASHKEEL_FIXES:
        out = re.sub(_AR_WORD_BOUNDARY.format(src=re.escape(src)), dst, out)
    return out

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
    text = re.sub(r"[^ء-ي\u064B-\u0652\u0670\s،.؟]", "", text)
    text = text.replace("؟", ".")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(،\s*)+", "، ", text)
    text = re.sub(r"(\.\s*)+", ". ", text)
    text = text.strip(" ،")
    return finalize_prosody(apply_tashkeel_fixes(text))


def softly_shorten_response(text: str, *, max_chars: int = 420, max_sentences: int = 5) -> str:
    """Shorten slightly while keeping complete sentence boundaries."""
    if not text:
        return ""
    formatted = finalize_prosody(apply_tashkeel_fixes(" ".join(text.split())))
    if len(formatted) <= max_chars:
        return formatted

    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", formatted) if s.strip()]
    if not sentences:
        return formatted[:max_chars].rsplit(" ", 1)[0].strip(" ،.") + "."

    selected: list[str] = []
    total = 0
    for sentence in sentences:
        if not sentence.endswith("."):
            sentence = f"{sentence}."
        next_total = total + len(sentence) + (1 if selected else 0)
        if selected and (next_total > max_chars or len(selected) >= max_sentences):
            break
        selected.append(sentence)
        total = next_total
        if len(selected) >= max_sentences:
            break

    if not selected:
        cut = sentences[0][:max_chars].rsplit(" ", 1)[0].strip(" ،.")
        return f"{cut}."
    return " ".join(selected)


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


def prepare_voice_reply(
    text: str,
    *,
    max_sentences: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Clean text and keep a short spoken answer (Moss GPU can't take long replies)."""
    formatted = format_for_speech(text)
    if not formatted:
        return ""

    if max_sentences is None:
        max_sentences = int(os.getenv("VOICE_MAX_SENTENCES", "2"))
    if max_chars is None:
        max_chars = int(os.getenv("VOICE_MAX_CHARS", "200"))

    parts = [p.strip() for p in re.split(r"(?<=\.)\s+", formatted) if p.strip()]
    if not parts:
        parts = [formatted]

    selected: list[str] = []
    total = 0
    for part in parts:
        sentence = part if part.endswith(".") else f"{part}."
        if selected and (total + len(sentence) > max_chars or len(selected) >= max_sentences):
            break
        if not selected and len(sentence) > max_chars:
            # Prefer ending at a sentence boundary over a mid-sentence word cut.
            cut = sentence[:max_chars].rsplit(" ", 1)[0].strip(" ،.")
            if cut and cut.endswith((".", "،")):
                sentence = cut
            elif cut and len(cut) > max_chars * 0.6:
                sentence = f"{cut}."
            else:
                sentence = f"{sentence[:max_chars].rstrip(' ،.')}."
        selected.append(sentence)
        total += len(sentence) + 1
        if len(selected) >= max_sentences:
            break

    return " ".join(selected).strip()


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


def split_for_tts_synthesis(text: str, max_chars: int | None = None) -> list[str]:
    """Split long prose into Moss-safe chunks (GPU OOMs on long inputs)."""
    # Keep chunks short — Moss Saudi 3 OOMs around ~200+ chars when GPU is busy.
    if max_chars is None:
        max_chars = int(os.getenv("TTS_CHUNK_MAX_CHARS", "80"))

    # Prefer the caller's text as-is (may already be normalized); only light cleanup.
    formatted = re.sub(r"\s+", " ", (text or "")).strip()
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
        for sep in (". ", "، ", " "):
            pos = window.rfind(sep)
            if pos > max_chars // 3:
                split_at = pos + len(sep)
                break
        if split_at <= 0:
            split_at = max_chars

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
