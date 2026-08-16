"""Recover function calls that Gemma printed as plain assistant text.

Live failure (2026-08-13): instead of a real tool invocation the model wrote

    call:show_meal{meal_id:<|"|>amico-kitchen-offer-2<|"|>,reason:<|"|>…<|"|>}<tool_call|>

into the reply. TTS stripped it to empty audio, LiveKit raised
``no audio frames were pushed``, and the meal page never opened — even though
the meal_id was sitting right there in the text.

Converting that leak into a real ``FunctionToolCall`` before it reaches TTS
makes the action happen, and dropping the leak from the spoken text keeps the
user from hearing ``call:show_meal``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterable, Optional

# Gemma / OpenAI-compat wrappers leave these control crumbs inside the args.
_CTRL_TOKEN_RE = re.compile(r"<\|?[^<>|]{0,40}\|?>")
# call:name{…} — the body is non-greedy so a trailing <tool_call|> does not
# get swallowed into the args. Nested braces are not used by our tools.
_CALL_RE = re.compile(
    r"\bcall\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}\s*(?:<\s*tool_call\|?>)?",
    re.IGNORECASE | re.DOTALL,
)
# key:value pairs. Value runs until the next ,key: or end.
_KV_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)(?=\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*:|$)",
    re.DOTALL,
)


def _clean_arg_value(raw: str) -> Any:
    text = _CTRL_TOKEN_RE.sub("", str(raw or "")).strip()
    text = text.strip().strip("\"'`").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return text


def _parse_args_body(body: str) -> dict[str, Any]:
    cleaned = _CTRL_TOKEN_RE.sub("", body or "")
    cleaned = cleaned.strip().strip(",")
    if not cleaned:
        return {}
    # Prefer real JSON when the model managed to emit it.
    if cleaned.startswith("{"):
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    args: dict[str, Any] = {}
    for match in _KV_RE.finditer(cleaned):
        key = match.group(1).strip()
        args[key] = _clean_arg_value(match.group(2))
    return args


def extract_text_tool_calls(
    text: str,
    *,
    allowed_names: Optional[Iterable[str]] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Split leaked ``call:name{…}`` invocations out of assistant text.

    Returns ``(speech_text, calls)`` where each call is
    ``{"name": str, "arguments": dict, "call_id": str}``.
    Unknown tool names are dropped (left out of speech too, so the user never
    hears the raw call).
    """
    raw = str(text or "")
    if not raw.strip():
        return "", []

    allow = {n for n in (allowed_names or []) if n} or None
    calls: list[dict[str, Any]] = []
    pieces: list[str] = []
    cursor = 0
    for match in _CALL_RE.finditer(raw):
        pieces.append(raw[cursor:match.start()])
        cursor = match.end()
        name = match.group(1).strip()
        if allow is not None and name not in allow:
            continue
        args = _parse_args_body(match.group(2))
        calls.append(
            {
                "name": name,
                "arguments": args,
                "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
            }
        )
    pieces.append(raw[cursor:])
    speech = " ".join(" ".join(pieces).split()).strip()
    # A turn that was *only* a leaked tool call must stay silent.
    if re.search(r"\bcall\s*:", speech, re.IGNORECASE) or re.search(
        r"<\s*tool_call\b", speech, re.IGNORECASE
    ):
        speech = ""
    return speech, calls


def contains_text_tool_call(text: str) -> bool:
    """True when the text looks like a leaked tool invocation."""
    if not text:
        return False
    if _CALL_RE.search(text):
        return True
    if re.search(r"\bcall\s*:", text, re.IGNORECASE):
        return True
    if re.search(r"<\s*tool_call\b", text, re.IGNORECASE):
        return True
    return False


# Live failure 2026-08-16: after search_meals she said
# "فَتَحت لَك صَفْحَة وَجْبَة … مِن مَطْبَخ أميكو" with no show_meal call.
_OPEN_CLAIM_RE = re.compile(
    r"ف[\u064B-\u0652]*ت[\u064B-\u0652]*ح[\u064B-\u0652]*ت"
    r"(?:[\u064B-\u0652]*ه[\u064B-\u0652]*ا)?"
    r"(?:\s+ل[\u064B-\u0652]*ك[\u064B-\u0652]*)?"
    r"(?:\s+ص[\u064B-\u0652]*ف[\u064B-\u0652]*ح[\u064B-\u0652]*ة)?",
    re.IGNORECASE,
)
_TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670]")


def _fold_ar(text: str) -> str:
    return _TASHKEEL_RE.sub("", str(text or "")).replace("أ", "ا").replace("إ", "ا").replace(
        "آ", "ا"
    ).replace("ة", "ه").replace("ى", "ي").lower()


def claims_page_opened(text: str) -> bool:
    """True when the model claims it opened a page/meal without proving it."""
    if not text:
        return False
    return bool(_OPEN_CLAIM_RE.search(text))


def match_open_claim_to_meal(
    speech: str,
    candidates: Iterable[dict[str, Any]],
) -> Optional[str]:
    """Pick a meal_id from recent search hits that the open-claim is about.

    Scores restaurant name and meal name overlap against the spoken Arabic.
    Returns None when nothing is confident enough — caller must not invent.
    """
    folded = _fold_ar(speech)
    if not folded or not claims_page_opened(speech):
        return None

    best_id: Optional[str] = None
    best_score = 0
    ranked: list[tuple[int, str]] = []
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        meal_id = str(raw.get("meal_id") or raw.get("id") or "").strip()
        if not meal_id:
            continue
        score = 0
        restaurant = _fold_ar(raw.get("restaurant") or raw.get("restaurant_name") or "")
        name = _fold_ar(raw.get("name") or "")
        # Distinctive restaurant tokens (≥3 chars) present in speech.
        for token in re.findall(r"[\u0600-\u06FFa-z0-9]{3,}", restaurant):
            if token in folded:
                score += 4
        for token in re.findall(r"[\u0600-\u06FFa-z0-9]{3,}", name):
            if token in folded:
                score += 2
        # Latin / ASR variants that often appear after spoken_lexicon.
        for needle, boost in (
            ("amico", 5),
            ("اميکو", 5),
            ("اميكو", 5),
            ("pizza", 2),
            ("بيتزا", 2),
            ("برجر", 2),
            ("شاورما", 2),
        ):
            if needle in folded and (
                needle in restaurant or needle in name or needle in _fold_ar(meal_id)
            ):
                score += boost
        price = raw.get("price_sar")
        if price is not None:
            try:
                digits = str(int(float(price)))
                if digits and digits in re.sub(r"\D", "", speech):
                    score += 1
            except (TypeError, ValueError):
                pass
        if score > 0:
            ranked.append((score, meal_id))
        if score > best_score:
            best_score = score
            best_id = meal_id

    if best_id and best_score >= 4:
        return best_id
    # Single clear candidate from the last search when she claimed an open.
    if len(ranked) == 1 and ranked[0][0] >= 2:
        return ranked[0][1]
    return None


def meals_from_tool_output(output: Any) -> list[dict[str, Any]]:
    """Parse ``search_meals`` tool JSON (str or dict) into meal dicts."""
    data = output
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [r for r in results if isinstance(r, dict) and (r.get("meal_id") or r.get("id"))]


def latest_search_meals(chat_ctx: Any) -> list[dict[str, Any]]:
    """Walk chat history newest-first for the latest search_meals tool output."""
    items = getattr(chat_ctx, "items", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    if not items:
        return []
    for item in reversed(list(items)):
        name = getattr(item, "name", None) or (
            item.get("name") if isinstance(item, dict) else None
        )
        if name != "search_meals":
            continue
        output = getattr(item, "output", None)
        if output is None and isinstance(item, dict):
            output = item.get("output")
        meals = meals_from_tool_output(output)
        if meals:
            return meals
    return []


def recover_implied_show_meal(
    speech: str,
    chat_ctx: Any = None,
    *,
    candidates: Optional[Iterable[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """If speech claims a page open, recover a real ``show_meal`` call."""
    if not claims_page_opened(speech):
        return None
    pool = list(candidates or [])
    if not pool and chat_ctx is not None:
        pool = latest_search_meals(chat_ctx)
    meal_id = match_open_claim_to_meal(speech, pool)
    if not meal_id:
        return None
    return {
        "name": "show_meal",
        "arguments": {
            "meal_id": meal_id,
            "reason": "recovered_from_open_claim",
        },
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }
