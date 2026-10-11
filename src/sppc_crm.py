"""Client for the SPPC demo CRM's internal (agent-only) API.

The CRM lives in the sppc-web Next.js app on loopback; every call carries the
shared CRM_INTERNAL_KEY. Nothing here may raise into the voice session: a CRM
outage must never take a live call down, so failures are logged and reported
back as None.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx
from loguru import logger

CRM_INTERNAL_URL = os.getenv(
    "CRM_INTERNAL_URL", "http://127.0.0.1:8095/sppc/api/internal"
).rstrip("/")
CRM_INTERNAL_KEY = os.getenv("CRM_INTERNAL_KEY", "").strip()

TICKET_TYPES = {"complaint", "suggestion", "inquiry"}
TICKET_TYPE_ALIASES = {
    "شكوى": "complaint",
    "شكوي": "complaint",
    "مقترح": "suggestion",
    "اقتراح": "suggestion",
    "استفسار": "inquiry",
}
PRIORITIES = {"عالي": "high", "متوسط": "medium", "منخفض": "low"}
SENTIMENTS = {"غاضب": "angry", "منزعج": "upset", "محايد": "neutral", "راض": "satisfied", "راضي": "satisfied"}

_DIGIT_WORDS = {
    "0": "صِفْر",
    "1": "وَاحِد",
    "2": "اثْنِين",
    "3": "ثَلَاثَة",
    "4": "أَرْبَعَة",
    "5": "خَمْسَة",
    "6": "سِتَّة",
    "7": "سَبْعَة",
    "8": "ثَمَانِيَة",
    "9": "تِسْعَة",
}

# Caller names the model sometimes emits instead of null when none was given.
_NO_NAME = {"", "null", "none", "unknown", "غير معروف", "مجهول", "لا يوجد", "بدون"}


_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Words that say a ticket exists without saying what it is about. A caller who
# only said «أبي أقدم مقترح» produced a ticket reading «تقديم مقترح»; without
# this check the model saved it before ever hearing the suggestion.
_FILLER_WORDS = {
    "تقديم", "يقدم", "بتقديم", "أقدم", "اقدم", "تسجيل", "يسجل", "رفع",
    "مقترح", "اقتراح", "مقترحا", "شكوى", "شكوي", "بلاغ", "استفسار", "طلب",
    "متصل", "عميل", "يود", "يريد", "يبي", "يرغب", "لديه", "عنده", "عندي",
    "شركة", "للشركة", "جديد", "عام", "عامة", "خاص", "بخصوص", "حول", "عن", "في",
    "من", "على", "الى", "إلى", "أن", "ان", "له", "لها", "هو", "هي", "و", "او", "أو",
}


def _bare_word(word: str) -> str:
    for prefix in ("وال", "بال", "فال", "كال", "لل", "ال"):
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            return word[len(prefix):]
    return word


def has_substance(*texts: str) -> bool:
    """True when the ticket says what the issue is, not just that there is one."""
    words = re.findall(r"[ء-يA-Za-z0-9]+", re.sub(r"[ً-ْٰـ]", "", " ".join(texts)))
    meaningful = {
        w for w in words
        if len(w) > 1 and w not in _FILLER_WORDS and _bare_word(w) not in _FILLER_WORDS
    }
    return len(meaningful) >= 3


# Gemma's reasoning channel occasionally leaks into reply content around a tool
# call as «<|channel>thought\n<channel|>». Spoken, it is garbage; shown, it is
# noise. Any other <|…|> special token is dropped with it.
_CHANNEL_OPEN = "<|channel>"
_CHANNEL_CLOSE = "<channel|>"
_SPECIAL_TOKEN_RE = re.compile(r"<\|?[a-zA-Z_]{1,24}\|?>")


def strip_model_tokens(text: str) -> str:
    """Remove leaked reasoning blocks and special tokens from a finished string."""
    stream = ModelTokenFilter()
    return stream.feed(text) + stream.flush()


class ModelTokenFilter:
    """Streamed version of :func:`strip_model_tokens`.

    A token can arrive split across deltas («<|cha», «nnel>»), so anything that
    could still be the start of one is held back until it is decided.
    """

    _MAX_HELD = 4000

    def __init__(self) -> None:
        self._buffer = ""
        self._in_channel = False

    def feed(self, delta: str) -> str:
        self._buffer += delta
        out: list[str] = []
        while self._buffer:
            if self._in_channel:
                end = self._buffer.find(_CHANNEL_CLOSE)
                if end == -1:
                    # Reasoning that never closes is dropped, not voiced.
                    if len(self._buffer) > self._MAX_HELD:
                        self._buffer = ""
                    break
                self._buffer = self._buffer[end + len(_CHANNEL_CLOSE):].lstrip()
                self._in_channel = False
                continue
            start = self._buffer.find(_CHANNEL_OPEN)
            if start != -1:
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start + len(_CHANNEL_OPEN):]
                self._in_channel = True
                continue
            lt = self._buffer.rfind("<")
            if lt != -1 and ">" not in self._buffer[lt:] and len(self._buffer) - lt <= 26:
                out.append(self._buffer[:lt])
                self._buffer = self._buffer[lt:]
                break
            out.append(self._buffer)
            self._buffer = ""
        return _SPECIAL_TOKEN_RE.sub("", "".join(out))

    def flush(self) -> str:
        tail, self._buffer = ("" if self._in_channel else self._buffer), ""
        self._in_channel = False
        return _SPECIAL_TOKEN_RE.sub("", tail)


def spoken_digits(number: str) -> str:
    """Say every digit of ``number`` one by one, in diacritized Arabic words."""
    digits = number.translate(_ARABIC_INDIC)
    return " ".join(_DIGIT_WORDS[d] for d in digits if d in _DIGIT_WORDS)


def ticket_serial(ticket_number: str) -> str:
    return ticket_number.rsplit("-", 1)[-1]


# A ticket number as the model writes it: SPPC-26-1043, with or without the
# dashes, spaces around them, or Arabic-Indic digits.
_TICKET_RE = re.compile(
    r"SPPC\s*[-–]?\s*[0-9٠-٩]{2}\s*[-–]?\s*([0-9٠-٩]{3,6})", re.IGNORECASE
)


def speak_ticket_numbers(text: str, serials: list[str] | tuple[str, ...] = ()) -> str:
    """Rewrite ticket numbers in TTS-bound text as Arabic digit words.

    The model is never trusted to pronounce a ticket number: left to itself it
    transliterated SPPC-26-1043 as «إس بي بي سي اثنين وستة واشنين…», reading
    the year as a sum and garbling a digit. It writes the number verbatim
    instead, and this turns it into the serial said digit by digit, «واحد صفر
    أربعة ثلاثة»: the prefix and year are the same on every ticket and only
    lengthen what the caller has to remember. ``serials`` are this call's own
    tickets, so a bare «1043» is caught too.
    """
    text = _TICKET_RE.sub(lambda m: spoken_digits(m.group(1)), text)
    for serial in serials:
        text = re.sub(rf"(?<![0-9٠-٩]){serial}(?![0-9٠-٩])", spoken_digits(serial), text)
    return text


class TicketSpeechStream:
    """Apply :func:`speak_ticket_numbers` across streamed LLM deltas.

    A number can arrive split over several deltas («SPP», «C-26-10», «43»), so
    text is held back until a sentence or clause boundary. A ticket number
    contains none of those characters, so it is always rewritten whole. The TTS
    already batches by sentence, so holding a clause back costs no latency.
    """

    _BOUNDARIES = ".،,؟?!؛;\n"

    def __init__(self, serials: list[str]) -> None:
        # The live list: tickets saved earlier in this turn are already in it.
        self._serials = serials
        self._buffer = ""

    def feed(self, delta: str) -> str:
        self._buffer += delta
        cut = max(self._buffer.rfind(c) for c in self._BOUNDARIES)
        if cut < 0:
            return ""
        ready, self._buffer = self._buffer[: cut + 1], self._buffer[cut + 1 :]
        return speak_ticket_numbers(ready, self._serials)

    def flush(self) -> str:
        ready, self._buffer = self._buffer, ""
        return speak_ticket_numbers(ready, self._serials)


def clean_name(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    text = re.sub(r"[ً-ْٰـ]", "", str(name)).strip()
    if text.lower() in _NO_NAME:
        return None
    return text[:80]


def normalize_type(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in TICKET_TYPES:
        return v
    return TICKET_TYPE_ALIASES.get(v, "complaint")


def normalize_priority(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in {"high", "medium", "low"}:
        return v
    return PRIORITIES.get(v, "medium")


def normalize_sentiment(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in {"angry", "upset", "neutral", "satisfied"}:
        return v
    return SENTIMENTS.get(v, "neutral")


async def _post(path: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{CRM_INTERNAL_URL}{path}",
                json=payload,
                headers={"X-Internal-Key": CRM_INTERNAL_KEY},
            )
        if resp.status_code >= 400:
            logger.warning("CRM {} failed status={} body={}", path, resp.status_code, resp.text[:300])
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("CRM {} unreachable: {}", path, e)
        return None


async def call_started(call_id: str, caller_number: Optional[str], caller_label: Optional[str]) -> None:
    await _post(
        "/calls/start",
        {"callId": call_id, "callerNumber": caller_number, "callerLabel": caller_label},
    )


async def call_ended(
    call_id: str,
    duration_s: int,
    transcript: list[dict[str, Any]],
    outcome: str,
) -> None:
    await _post(
        "/calls/end",
        {
            "callId": call_id,
            "durationS": duration_s,
            "transcript": transcript,
            "outcome": outcome,
        },
    )


async def create_ticket(call_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    return await _post("/tickets", {"callId": call_id, **fields})


async def set_caller_name(call_id: str, name: str) -> Optional[dict[str, Any]]:
    return await _post("/caller-name", {"callId": call_id, "name": name})


EXTRACTION_PROMPT = """
You read a transcript of a customer-care phone call to المشتري الرئيس (Saudi
Power Procurement Company). The agent failed to register a ticket during the
call. Decide whether the caller raised a complaint or a suggestion that should
be registered. Set "register" to true only if the caller actually said what the
complaint or suggestion is; a caller who only said they wanted to make one,
without its content, is "register": false. Use only what the caller said and
never invent details. Reply with ONLY a JSON object, no prose:
{"register": true|false,
 "type": "complaint"|"suggestion"|"inquiry",
 "category": "<exactly one of: مشاريع الطاقة المتجددة، مشاريع الطاقة الحرارية، تخزين الطاقة، الموردون والمناقصات، بوابة المشتريات الإلكترونية، التوظيف والتدريب، الشراكات والاستثمار، التواصل وخدمة العملاء، البيئة والاستدامة، خارج الاختصاص – الكهرباء السعودية، أخرى>",
 "title": "<short Arabic title, max 8 words>",
 "description": "<Arabic description of the issue in the caller's terms>",
 "summary": "<one or two Arabic sentences summarising the call>",
 "priority": "high"|"medium"|"low",
 "sentiment": "angry"|"upset"|"neutral"|"satisfied",
 "caller_name": "<the name the caller said, or null if they did not give one>"}
Arabic text must be written without tashkeel.
"""


async def extract_ticket(
    transcript: list[dict[str, Any]],
    *,
    base_url: str,
    model: str,
    api_key: str,
) -> Optional[dict[str, Any]]:
    """Ask the LLM to recover a ticket from a call that ended without one."""
    convo = "\n".join(
        f"{'المتصل' if t['role'] == 'user' else 'الموظفة'}: {t['text']}" for t in transcript
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": EXTRACTION_PROMPT},
                        {"role": "user", "content": convo},
                    ],
                },
            )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"] or ""
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("Ticket extraction failed: {}", e)
        return None
