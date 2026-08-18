"""TTS normalization: domain dictionary + optional automated tashkeel."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import httpx
from loguru import logger
from pyarabic import araby

from src.config.model_endpoints import (
    TASHKEEL_API_KEY,
    TASHKEEL_BASE_URL,
    TASHKEEL_ENABLED,
    TASHKEEL_MODEL,
    TASHKEEL_TIMEOUT_S,
    TTS_STRIP_TASHKEEL,
)
from src.utils.speech_format import format_for_speech, split_tts_clauses

_DICTIONARY_PATH = Path(__file__).resolve().parent.parent / "data" / "tts_pronunciation_dictionary.json"

_TASHKEEL_SYSTEM = (
    "أنت أداة تشكيل عربي للنطق الصوتي بلهجة سعودية واضحة. "
    "أضف الحركات (التشكيل) للنص العربي فقط. "
    "احفظ نفس الكلمات والترتيب تماماً ولا تغيّر المعنى ولا تضف أو تحذف كلمات. "
    "شكّل كل كلمة بحركات واضحة تناسب السياق. "
    "ارجع النص المشكّل فقط بدون شرح أو ترجمة أو رموز إضافية."
)

_ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u0671-\u06D3]")
_TASHKEEL_MARK = re.compile(r"[\u064B-\u0652\u0670]")


@lru_cache(maxsize=1)
def _load_dictionary() -> tuple[dict[str, str], list[tuple[re.Pattern[str], str]]]:
    path = Path(os.getenv("TTS_PRONUNCIATION_DICT", str(_DICTIONARY_PATH)))
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    exact: dict[str, str] = dict(data.get("exact", {}))
    compiled: list[tuple[re.Pattern[str], str]] = []
    for rule in data.get("regex", []):
        flags = 0
        for flag_name in str(rule.get("flags", "")).split():
            if flag_name.lower() == "i":
                flags |= re.IGNORECASE
        compiled.append((re.compile(rule["pattern"], flags), rule["replace"]))
    return exact, compiled


def _normalize_arabic_letters(text: str) -> str:
    if TTS_STRIP_TASHKEEL:
        text = araby.strip_tashkeel(text)
    text = araby.normalize_hamza(text)
    text = araby.normalize_ligature(text)
    text = araby.normalize_alef(text)
    text = araby.normalize_teh(text)
    return text


def apply_pronunciation_dictionary(text: str) -> str:
    if not text:
        return ""

    exact, regex_rules = _load_dictionary()
    updated = text

    for pattern, replacement in sorted(exact.items(), key=lambda item: len(item[0]), reverse=True):
        updated = re.sub(re.escape(pattern), replacement, updated, flags=re.IGNORECASE)

    for pattern, replacement in regex_rules:
        updated = pattern.sub(replacement, updated)

    return updated


def _extract_tashkeel_response(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _tashkeel_coverage(text: str) -> float:
    letters = len(_ARABIC_LETTER.findall(text))
    if letters == 0:
        return 1.0
    marks = len(_TASHKEEL_MARK.findall(text))
    return marks / letters


def _needs_tashkeel(text: str) -> bool:
    min_coverage = float(os.getenv("TASHKEEL_MIN_COVERAGE", "0.12"))
    return _tashkeel_coverage(text) < min_coverage


def _merge_tashkeel(original: str, diacritized: str) -> str:
    """Keep diacritized text only if it still looks like the same utterance."""
    orig_letters = re.sub(r"[\s\u064B-\u0652\u0670،.؟]+", "", original)
    diac_letters = re.sub(r"[\s\u064B-\u0652\u0670،.؟]+", "", diacritized)
    if not diac_letters:
        return original
    if len(diac_letters) < len(orig_letters) * 0.75:
        return original
    if len(diac_letters) > len(orig_letters) * 1.35:
        return original
    return diacritized.strip()


async def _tashkeel_one_clause(text: str) -> str:
    if not text or not TASHKEEL_ENABLED or not _needs_tashkeel(text):
        return text

    max_for_tashkeel = int(os.getenv("TASHKEEL_MAX_CHARS", "220"))
    if len(text) > max_for_tashkeel:
        logger.debug(f"Tashkeel skipped for long clause ({len(text)} > {max_for_tashkeel})")
        return text

    base = TASHKEEL_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"

    payload = {
        "model": TASHKEEL_MODEL,
        "temperature": 0.0,
        "max_tokens": max(256, len(text) * 4),
        "messages": [
            {"role": "system", "content": _TASHKEEL_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    headers = {"Authorization": f"Bearer {TASHKEEL_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TASHKEEL_TIMEOUT_S, connect=5.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            diacritized = _extract_tashkeel_response(content)
            merged = _merge_tashkeel(text, diacritized)
            if merged != text:
                logger.debug(
                    f"Tashkeel applied: {len(text)} -> {len(merged)} chars "
                    f"(coverage {_tashkeel_coverage(merged):.2f})"
                )
            return merged
    except Exception as exc:
        logger.warning(f"Tashkeel skipped ({exc}); using un-diacritized text.")

    return text


async def add_tashkeel(text: str) -> str:
    if not text or not TASHKEEL_ENABLED:
        return text

    clauses = split_tts_clauses(text) or [text]
    if len(clauses) == 1:
        return await _tashkeel_one_clause(clauses[0])

    out: list[str] = []
    for clause in clauses:
        out.append(await _tashkeel_one_clause(clause))
    return " ".join(out)


async def normalize_clause_for_tts(clause: str) -> str:
    """Normalize one spoken clause (dictionary + letters + tashkeel)."""
    if not clause:
        return ""

    step1 = apply_pronunciation_dictionary(clause)
    step2 = format_for_speech(step1)
    step3 = _normalize_arabic_letters(step2)
    step4 = await _tashkeel_one_clause(step3)
    return step4.strip()


async def normalize_for_tts(text: str) -> str:
    """Full TTS pipeline for a complete utterance."""
    if not text:
        return ""

    step1 = apply_pronunciation_dictionary(text)
    step2 = format_for_speech(step1)
    step3 = _normalize_arabic_letters(step2)
    step4 = await add_tashkeel(step3)
    return step4.strip()
