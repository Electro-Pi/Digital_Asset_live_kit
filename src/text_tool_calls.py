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
    if lower in {"none", "null", "nil"}:
        return ""
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


def offers_to_open_page(text: str) -> bool:
    """True when she asks permission to open instead of just opening.

    Live 2026-08-17: first pizza recommend was "تبي أوريك الصفحة؟" with
    no show_meal. That question is itself the signal to open.
    """
    folded = _fold_ar(text)
    if not folded:
        return False
    if "اوريك" in folded or "وريك الصفح" in folded:
        return True
    if "افتح الصفح" in folded or "افتحها لك" in folded:
        return True
    if "تبي افتح" in folded or "تبين افتح" in folded:
        return True
    return "وريكم" in folded


def user_asked_where_meal(text: str) -> bool:
    """True when they are looking at the wrong page and asking where the meal is.

    Live 2026-08-17: deal-chip recovery yanked them to /offers after show_meal,
    then "فين العرض ده أو فين الوجبة دي؟" and she claimed the page was open
    without calling show_meal again.
    """
    folded = _fold_ar(text)
    if not folded:
        return False
    return any(
        p in folded
        for p in (
            "فين العرض",
            "فين الوجبه",
            "وين العرض",
            "وين الوجبه",
            "فين الصفح",
            "وين الصفح",
            "الصفح فين",
            "ما فتحت",
            "ما انفتحت",
            "مش فاتحه",
            "مش فتحه",
        )
    )


def _chat_items(chat_ctx: Any) -> list[Any]:
    items = getattr(chat_ctx, "items", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    return list(items or [])


def tool_called_since_last_user(chat_ctx: Any, tool_name: str) -> bool:
    items = _chat_items(chat_ctx)
    last_user = -1
    for i, item in enumerate(items):
        role = getattr(item, "role", None)
        if role is None and isinstance(item, dict):
            role = item.get("role")
        if role == "user":
            last_user = i
    for item in items[last_user + 1 :]:
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        if name == tool_name:
            return True
    return False


def has_tool_output_since_last_user(chat_ctx: Any) -> bool:
    """True when this llm_node is the speech turn after tools already ran.

    Live 2026-08-17: after show_meal / get_meal_details Gemma often returns
    an empty follow-up. Treating that as a cutout made her say the fallback
    even though the page had already opened.
    """
    items = _chat_items(chat_ctx)
    last_user = -1
    for i, item in enumerate(items):
        role = getattr(item, "role", None)
        if role is None and isinstance(item, dict):
            role = item.get("role")
        if role == "user":
            last_user = i
    for item in items[last_user + 1 :]:
        typ = getattr(item, "type", None)
        if typ is None and isinstance(item, dict):
            typ = item.get("type")
        if typ in {"function_call", "function_call_output"}:
            return True
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        if name:
            return True
    return False


def should_speak_cutout_fallback(chat_ctx: Any = None) -> bool:
    """Only speak the cutout line when this turn never ran a tool."""
    return not has_tool_output_since_last_user(chat_ctx)


def match_open_claim_to_meal(
    speech: str,
    candidates: Iterable[dict[str, Any]],
) -> Optional[str]:
    """Pick a meal_id from recent search hits that the open-claim is about.

    Scores restaurant name and meal name overlap against the spoken Arabic.
    Returns None when nothing is confident enough — caller must not invent.
    """
    folded = _fold_ar(speech)
    if not folded:
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


def latest_tool_arguments(chat_ctx: Any, tool_name: str) -> Optional[dict[str, Any]]:
    """Newest arguments for a named tool call in the chat context."""
    items = getattr(chat_ctx, "items", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    if not items:
        return None
    for item in reversed(list(items)):
        name = getattr(item, "name", None) or (
            item.get("name") if isinstance(item, dict) else None
        )
        if name != tool_name:
            continue
        args = getattr(item, "arguments", None)
        if args is None and isinstance(item, dict):
            args = item.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = None
        if isinstance(args, dict):
            return args
    return None


def recover_implied_show_meal(
    speech: str,
    chat_ctx: Any = None,
    *,
    candidates: Optional[Iterable[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """If speech claims or offers a page open, recover a real ``show_meal``."""
    user = last_user_text(chat_ctx) if chat_ctx is not None else ""
    offering = offers_to_open_page(speech)
    asking_where = user_asked_where_meal(user)
    if not claims_page_opened(speech) and not offering and not asking_where:
        return None
    if chat_ctx is not None and tool_called_since_last_user(chat_ctx, "show_meal"):
        return None
    pool = list(candidates or [])
    if not pool and chat_ctx is not None:
        pool = latest_search_meals(chat_ctx)
    meal_id = match_open_claim_to_meal(speech, pool)
    if not meal_id and (offering or asking_where) and pool:
        meal_id = str(
            (pool[0] or {}).get("meal_id") or (pool[0] or {}).get("id") or ""
        ).strip()
    if not meal_id and asking_where:
        meal_id = latest_meal_id(chat_ctx)
    if not meal_id:
        return None
    return {
        "name": "show_meal",
        "arguments": {
            "meal_id": meal_id,
            "reason": (
                "recovered_from_where_is_it"
                if asking_where
                else "recovered_from_offer_to_open"
                if offering
                else "recovered_from_open_claim"
            ),
        },
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


def last_user_text(chat_ctx: Any) -> str:
    """Newest user utterance from the LiveKit chat context."""
    items = getattr(chat_ctx, "items", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    if not items:
        return ""
    for item in reversed(list(items)):
        role = getattr(item, "role", None)
        if role is None and isinstance(item, dict):
            role = item.get("role")
        if role != "user":
            continue
        text = getattr(item, "text_content", None)
        if not text:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content") or item.get("text")
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    else:
                        parts.append(str(getattr(part, "text", "") or part or ""))
                text = " ".join(parts)
            elif content is not None:
                text = str(content)
        if text:
            return str(text).strip()
    return ""


def last_assistant_text(chat_ctx: Any) -> str:
    """Newest assistant utterance from the LiveKit chat context."""
    for item in reversed(_chat_items(chat_ctx)):
        role = getattr(item, "role", None)
        if role is None and isinstance(item, dict):
            role = item.get("role")
        if role != "assistant":
            continue
        text = getattr(item, "text_content", None)
        if not text:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content") or item.get("text")
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    else:
                        parts.append(str(getattr(part, "text", "") or part or ""))
                text = " ".join(parts)
            elif content is not None:
                text = str(content)
        if text:
            return str(text).strip()
    return ""


_NAV_PAGE_MAP: list[tuple[tuple[str, ...], str]] = [
    # List chips before generic المطاعم: "افتح صفحة التوصيل سريع" is not /restaurants.
    (("كاش باك", "كاشباك", "الكاش باك", "cashback"), "cashback"),
    (
        (
            "توصيل سريع",
            "توصيل سريعا",
            "التوصيل السريع",
            "صفحه سريع",
            "صفحه التوصيل",
            "مكتوب عليها سريع",
        ),
        "fast",
    ),
    (
        (
            "24 ساعه",
            "٢٤ ساعه",
            "مفتوح 24",
            "24h",
            "24/7",
            "٢٤/٧",
            "على مدار الساعه",
            "على مدار الساعة",
        ),
        "24h",
    ),
    (("البقاله", "صفحه البقاله", "صفحه بقاله"), "grocery"),
    # Bare "مطبخ" is a restaurant name prefix (مطبخ دمشق) — require ال or kitchen.
    (("المطاعم", "مطاعم", "المطبخ", "المطابخ", "kitchen"), "restaurants"),
    (("العروض", "عروض"), "offers"),
    (("المحفوظات", "محفوظات"), "saved"),
    (("السله", "الكارت", "الكارت"), "cart"),
    (("البحث",), "search"),
    (("الرئيسيه", "الرئيسية", "البيت"), "home"),
    (("الطلبات",), "orders"),
    (("اتمام", "checkout"), "checkout"),
]
_NAV_VERBS = (
    "افتح", "ادخل", "روح", "روحي", "ودي", "وديني", "ودوني", "وريني",
    "انتقل", "move", "go to", "وديني",
)
_LIST_FILTER_PAGES = frozenset({"fast", "grocery", "24h", "cashback"})


def _page_from_nav_text(text: str) -> Optional[str]:
    folded = _fold_ar(text)
    if not folded:
        return None
    for needles, page in _NAV_PAGE_MAP:
        if any(n in folded for n in needles):
            return page
    if "بقاله" in folded and (
        any(v in folded for v in _NAV_VERBS)
        or "صفحه" in folded
        or "كده" in folded
        or len(folded.split()) <= 3
    ):
        return "grocery"
    if "سريع" in folded and not any(h in folded for h in _MEAL_FOOD_HINTS):
        if (
            "توصيل" in folded
            or "صفحه" in folded
            or "مكتوب" in folded
            or _looks_like_nav_request(text)
            or len(folded.split()) <= 3
        ):
            return "fast"
    return None


def _looks_like_nav_request(text: str) -> bool:
    folded = _fold_ar(text)
    if not folded:
        return False
    if any(v in folded for v in _NAV_VERBS):
        return True
    return "صفحه" in folded or "صفحة" in folded


def _canonical_open_page(page: str) -> str:
    raw = (page or "").strip()
    if not raw:
        return ""
    mapped = _page_from_nav_text(raw)
    if mapped:
        return mapped
    key = _fold_ar(raw)
    if key in {
        "home", "restaurants", "cart", "checkout", "orders", "offers",
        "search", "saved", "cashback", "grocery", "fast", "24h",
    }:
        return key
    return key


def recover_implied_open_page(
    speech: str,
    chat_ctx: Any = None,
) -> Optional[dict[str, Any]]:
    """If speech claims a main app page opened, recover ``open_page``.

    Live 2026-08-16: "فتحت لك صفحة المطاعم" with no open_page RPC — tester
    stayed on home (Bug-001). Also recovers when the user asked to go to
    المطبخ/المطاعم and she never called the tool.

    Live 2026-08-17 11:09: "افتحي صفحة التوصيل سريعا" / "مكتوب عليها سريع"
    must open /restaurants?filter=fast, not the unfiltered list. User intent
    wins over a speech claim of "صفحة المطاعم". Do not recover the same page
    twice in one turn (open-claim loop after a successful navigate).
    """
    user = last_user_text(chat_ctx) if chat_ctx is not None else ""
    page: Optional[str] = None
    if _user_asked_cashback(user):
        page = "cashback"
    if page is None:
        hinted = _page_from_nav_text(user)
        if hinted in _LIST_FILTER_PAGES or (
            hinted and _looks_like_nav_request(user)
        ):
            page = hinted
    if page is None and claims_page_opened(speech):
        page = _page_from_nav_text(speech)
    if not page:
        return None
    already = (
        latest_tool_arguments(chat_ctx, "open_page")
        if chat_ctx is not None
        else None
    )
    if already:
        prev = _canonical_open_page(str(already.get("page") or ""))
        if prev and prev == _canonical_open_page(page):
            return None
    return {
        "name": "open_page",
        "arguments": {"page": page},
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


_CALORIE_CLAIM_RE = re.compile(
    r"س[\u064B-\u0652]*ع[\u064B-\u0652]*ر(?:ات|ه|ة)?"
    r"|كالور"
    r"|مجموع"
    r"|توتال"
    r"|الحساب"
    r"|الكلي"
    r"|الاجمالي"
    r"|الإجمالي",
    re.IGNORECASE,
)
_SAVED_EMPTY_RE = re.compile(
    r"محفوظ"
    r".{0,40}"
    r"(?:فاغ|فاض|ما\s*فيها|ما\s*فيه|فارغ|empty)"
    r"|(?:فاغ|فاض|فارغ|empty).{0,40}محفوظ",
    re.IGNORECASE,
)
_NO_RESTAURANT_RE = re.compile(
    r"ما\s*عند(?:نا|ي)\s*مطعم"
    r"|مطعم\s*ب(?:هذا|هذ[اه])\s*الاسم",
    re.IGNORECASE,
)
_MEAL_FOOD_HINTS = (
    "شاورما", "برجر", "بيتزا", "كبسه", "مندي", "مشاوي", "حلا", "كومبو",
    "وجبه", "عرض", "دجاج", "لحم", "سمك", "فلافل", "بروتين", "كيتو",
    "مقرمش", "عائلي", "افراد", "اشخاص", "كاش",
)
_STALL_SPEECH_RE = re.compile(
    r"لحظ|"
    r"اشوف\s+لك|"
    r"اشوف\s+العروض|"
    r"ادور\s+لك|"
    r"خليني\s+(?:اشوف|ادور|اشيك)|"
    r"اشيك|"
    r"ثوان|"
    r"ارجع\s+لك|"
    r"بدور",
    re.IGNORECASE,
)

# Spoken when she promised to look and there is no tool we can recover,
# or when the LLM turn dies (context overflow, empty stream, session error).
# Never leave the turn silent — empty TTS kills the LiveKit session.
STALL_FALLBACK_SAY_AR = "ما قدرت أكمّل الحين. عيد طلبك بجملة أوضح."
CUTOUT_FALLBACK_SAY_AR = STALL_FALLBACK_SAY_AR

# Gemma's context is 32768. Keep the last N chat items and cap tool JSON so a
# long call cannot fill the window and request 0 output tokens (silent cutout).
LLM_CHAT_MAX_ITEMS = 30
LLM_TOOL_OUTPUT_MAX_CHARS = 2500


def claims_stall(text: str) -> bool:
    """True when she promised to look something up and then went silent."""
    if not text:
        return False
    return bool(_STALL_SPEECH_RE.search(_fold_ar(text)))


def _user_asked_cashback(text: str) -> bool:
    folded = _fold_ar(text)
    if not folded:
        return False
    if "cashback" in folded:
        return True
    if "كاشباك" in folded or "كاش باك" in folded:
        return True
    return "كاش" in folded and "باك" in folded


def recover_stalled_turn(
    speech: str,
    chat_ctx: Any = None,
) -> list[dict[str, Any]]:
    """If she said she'd look and called nothing, run a real tool.

    Live 2026-08-17 Bug-010: stall sentence, zero tools, then silence.
    Prefer showing the last search hit; otherwise search the last user turn.
    """
    if not claims_stall(speech):
        return []
    user = last_user_text(chat_ctx)
    hinted = _page_from_nav_text(user)
    # Cashback stall still searches (needs results to speak). Fast/grocery/24h
    # are URL chips — open the filtered list, do not search "سريع" as a dish.
    if hinted in {"fast", "grocery", "24h"} or (
        hinted
        and hinted not in _LIST_FILTER_PAGES
        and _looks_like_nav_request(user)
    ):
        return [
            {
                "name": "open_page",
                "arguments": {"page": hinted},
                "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
            }
        ]
    filter_args = ui_filter_args_from_user(user)
    if filter_args and any(
        key in filter_args
        for key in ("offers_kind", "query", "restaurants_filter", "menu_tab")
    ):
        return [
            {
                "name": "set_ui_filter",
                "arguments": filter_args,
                "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
            }
        ]
    pool = latest_search_meals(chat_ctx) if chat_ctx is not None else []
    for row in pool:
        meal_id = str((row or {}).get("meal_id") or "").strip()
        if meal_id:
            return [
                {
                    "name": "show_meal",
                    "arguments": {
                        "meal_id": meal_id,
                        "reason": "recovered_from_stall",
                    },
                    "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
                }
            ]
    user = last_user_text(chat_ctx)
    if not user:
        return []
    args: dict[str, Any] = {"query": user}
    if _user_asked_cashback(user):
        args["cashback"] = True
    return [
        {
            "name": "search_meals",
            "arguments": args,
            "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
        }
    ]


def recover_implied_search_meals(speech: str, chat_ctx: Any = None) -> Optional[dict[str, Any]]:
    """If she treated a meal request as a missing restaurant, search instead.

    Live 2026-08-16 Bug-003: ASR garbage → "ما عندنا مطعم بهذا الاسم" with
    no search_meals call, while the user wanted shawarma for 5.

    Live 2026-08-17 Bug-010: "عايز حاجة عليا كاش باك" → she said
    "لحظة أشوف لك العروض" and never called a tool. Any stall with a user
    utterance falls through to search so the turn cannot die silent.
    """
    user = last_user_text(chat_ctx)
    if not user:
        stalled = recover_stalled_turn(speech, chat_ctx)
        return stalled[0] if stalled else None
    folded = _fold_ar(user)
    if claims_no_restaurant(speech) and any(
        hint in folded for hint in _MEAL_FOOD_HINTS
    ):
        return {
            "name": "search_meals",
            "arguments": {"query": user},
            "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
        }
    if claims_stall(speech):
        stalled = recover_stalled_turn(speech, chat_ctx)
        return stalled[0] if stalled else None
    if _user_asked_cashback(user):
        return {
            "name": "search_meals",
            "arguments": {"query": user, "cashback": True},
            "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
        }
    return None


def claims_cart_numbers(text: str) -> bool:
    """True when the model is about to speak a cart total or calorie sum."""
    if not text:
        return False
    return bool(_CALORIE_CLAIM_RE.search(_TASHKEEL_RE.sub("", text)))


def claims_saved_empty(text: str) -> bool:
    if not text:
        return False
    folded = _fold_ar(text)
    return bool(_SAVED_EMPTY_RE.search(folded))


def claims_no_restaurant(text: str) -> bool:
    if not text:
        return False
    return bool(_NO_RESTAURANT_RE.search(_fold_ar(text)))


def _user_asked_cart_totals(user: str) -> bool:
    """True when the user asked for a cart/order sum, not a single-meal fact."""
    folded = _fold_ar(user)
    if not folded:
        return False
    cartish = any(w in folded for w in ("سله", "الكارت", "كارت", "الحساب"))
    sumish = any(w in folded for w in ("توتال", "مجموع", "اجمالي", "كلي", "الكل"))
    calorie = "سعرات" in folded or "كالور" in folded
    if cartish and (sumish or calorie or "سعر" in folded):
        return True
    if sumish and calorie:
        return True
    return False


def recover_implied_get_cart(speech: str, chat_ctx: Any = None) -> Optional[dict[str, Any]]:
    """Recover get_cart when she is about to invent a total (Bugs 007 / 009)."""
    user = last_user_text(chat_ctx)
    if not _user_asked_cart_totals(user):
        return None
    return {
        "name": "get_cart",
        "arguments": {},
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


_AR_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9\u0621-\u064A]")
_CONFIRM_WORDS = (
    "ماشي", "تمام", "زين", "اوكي", "ok", "يلا", "ايوه", "ايوا", "حاضر",
    "اه", "نعم",
)
_CONFIRM_FILLER = (
    "يا", "هلا", "طيب", "بس", "كده", "كذا", "ده", "دي", "في", "هذا", "هاذا",
    "ودي", "ودك", "فيها", "ابيها", "ابغاها", "ابغا", "خذها", "خذيها", "خلاص",
)


def _has_ar_word(folded: str, word: str) -> bool:
    """True when `word` appears as its own token, not inside a longer word.

    Live 2026-08-17: "إتمام الطلب" folded to اتمام, which contains تمام, so
    checkout recovered add_to_cart and duplicated the line.
    """
    if not folded or not word:
        return False
    start = 0
    while True:
        i = folded.find(word, start)
        if i < 0:
            return False
        before = folded[i - 1] if i else ""
        after_i = i + len(word)
        after = folded[after_i] if after_i < len(folded) else ""
        before_ok = not before or not _AR_WORD_CHAR_RE.match(before)
        after_ok = not after or not _AR_WORD_CHAR_RE.match(after)
        if before_ok and after_ok:
            return True
        start = i + 1


def _has_confirm_word(folded: str) -> bool:
    return any(_has_ar_word(folded, w) for w in _CONFIRM_WORDS)


def user_blocks_add_to_cart(text: str) -> bool:
    """True when this turn is some other action, not 'put it in the cart'.

    Live 2026-08-17: calories / open-cart / reduce-qty / checkout all recovered
    add_to_cart because the utterance had تمام or mentioned السلة.
    """
    folded = _fold_ar(text)
    if not folded:
        return False
    if any(
        p in folded
        for p in (
            "اتمام",
            "اكمال الطلب",
            "اضغط الزر",
            "اضغطي الزر",
            "ادفع",
        )
    ):
        return True
    if any(
        p in folded
        for p in ("سعرات", "سعره", "بروتين", "كارب", "دهون", "كالوري")
    ):
        return True
    if any(
        p in folded
        for p in (
            "اقلل",
            "قلل",
            "كميه",
            "شيلها",
            "شيليها",
            "احذف",
            "واحده بس",
            "واحد بس",
        )
    ):
        return True
    nav = any(
        p in folded
        for p in ("ادخل", "افتح", "روحي", "روح", "وديني", "وريني", "حصلني")
    )
    cartish = any(
        p in folded
        for p in ("سله", "كارت", "cart", "السنه", "سالب")
    )
    if nav and cartish:
        return True
    if nav and any(p in folded for p in ("صفحه", "العروض", "الرئيسيه")):
        return True
    return False


def user_cancels_repeat_add(text: str) -> bool:
    """True when the user is refusing a second add of the same meal.

    Live 2026-08-17: "دي تاني معلش" after an add — she add_to_cart'd again.
    "ضيفها تاني" still means add again and must not match.
    """
    folded = _fold_ar(text)
    if not folded:
        return False
    add_again = any(v in folded for v in ("ضيف", "اضيف", "حط")) and (
        "تاني" in folded or "ثاني" in folded
    )
    if add_again and "معلش" not in folded and "بلاش" not in folded:
        return False
    if any(
        p in folded
        for p in (
            "مش تاني",
            "مو تاني",
            "لا تاني",
            "لاء تاني",
            "مش ثاني",
            "مو ثاني",
        )
    ):
        return True
    if ("تاني" in folded or "ثاني" in folded) and any(
        w in folded for w in ("معلش", "بلاش")
    ):
        return True
    return "دي تاني" in folded or "دي ثاني" in folded or "هادي تاني" in folded


def _cartish(folded: str) -> bool:
    return any(
        w in folded
        for w in (
            "سله",
            "كارت",
            "cart",
            "السنه",
            "السلامه",
            "لسنه",
            "لسلامه",
            "سالب",
        )
    )


def _mostly_confirm(folded: str) -> bool:
    """True when the turn is just yes/ok, maybe with a cart ASR word."""
    if not _has_confirm_word(folded):
        return False
    words = [
        w.strip("،,.؟!؛:\"'")
        for w in folded.split()
        if w.strip("،,.؟!؛:\"'")
    ]
    leftover = []
    for w in words:
        if w in _CONFIRM_WORDS or w in _CONFIRM_FILLER:
            continue
        if any(c in w for c in ("سله", "سالب", "سنه", "كارت", "cart")):
            continue
        leftover.append(w)
    return not leftover


def user_asked_add_to_cart(text: str) -> bool:
    """True when the last user turn is an add-to-cart request."""
    if user_cancels_repeat_add(text) or user_blocks_add_to_cart(text):
        return False
    folded = _fold_ar(text)
    if not folded:
        return False
    cartish = _cartish(folded)
    pronoun_add = any(
        p in folded
        for p in (
            "ضيفها",
            "ضيفيها",
            "اضيفها",
            "ضيفه",
            "تضيفه",
            "حطها",
            "حطيه",
            "حطيها",
            "نضيفها",
            "هنضيفها",
        )
    )
    if pronoun_add and (
        cartish or not any(h in folded for h in _MEAL_FOOD_HINTS)
    ):
        return True
    verbs = (
        "ضيف",
        "اضيف",
        "حطها",
        "حطيه",
        "حطيها",
        "زودي",
        "زيدها",
        "ضيفيها",
        "اضيفها",
        "ضيفه",
        "حطه",
        "نضيف",
    )
    if any(v in folded for v in verbs) and cartish:
        return True
    # Confirm + cart word is add only when the turn is not another request.
    # "تمام ادخل على السلة" is blocked above. "السالب ده ماشي" still matches.
    if cartish and _mostly_confirm(folded):
        return True
    return any(
        p in folded
        for p in ("عايزك تضيف", "ابي تضيف", "ابغى تضيف", "ضيفها لي", "حطها لي")
    )


def claims_added_to_cart(text: str) -> bool:
    """True when she claims the meal is in the cart without proving it."""
    folded = _fold_ar(text)
    if not folded:
        return False
    return any(
        p in folded
        for p in ("ضفت", "اضفت", "اضفتها", "حطيتها", "زدتها", "ضيفتها")
    )


def user_confirms_add(user: str, chat_ctx: Any = None) -> bool:
    """True when they accept her offer to add the last meal to the cart.

    Must be a short yes after she asked to put it in the cart — not extras,
    calories, open-cart, or checkout. "تمام" inside "إتمام" does not count.
    """
    if user_blocks_add_to_cart(user) or user_cancels_repeat_add(user):
        return False
    folded = _fold_ar(user)
    if not folded or not _has_confirm_word(folded):
        return False
    if not _mostly_confirm(folded) and not user_asked_add_to_cart(user):
        return False
    asst = _fold_ar(last_assistant_text(chat_ctx))
    if not asst:
        return False
    offered_cart = ("سله" in asst or "حطها" in asst) and any(
        p in asst
        for p in ("تبي اضيف", "تبين اضيف", "اضيفها", "حطها", "ضيفها للسله")
    )
    return offered_cart


def should_run_add_to_cart(user: str, chat_ctx: Any = None) -> bool:
    """True only when this user turn actually wants a cart add."""
    if user_blocks_add_to_cart(user) or user_cancels_repeat_add(user):
        return False
    return user_asked_add_to_cart(user) or user_confirms_add(user, chat_ctx)


def latest_meal_id(chat_ctx: Any) -> str:
    """Newest meal_id from show_meal / get_meal_details / search hits."""
    for name in ("show_meal", "get_meal_details", "add_to_cart", "set_meal_options"):
        args = latest_tool_arguments(chat_ctx, name)
        if isinstance(args, dict):
            meal_id = str(args.get("meal_id") or "").strip()
            if meal_id:
                return meal_id
    meals = latest_search_meals(chat_ctx)
    if meals:
        return str((meals[0] or {}).get("meal_id") or (meals[0] or {}).get("id") or "").strip()
    return ""


def recover_implied_add_to_cart(
    speech: str,
    chat_ctx: Any = None,
) -> Optional[dict[str, Any]]:
    """If they asked to add and she never called add_to_cart, recover it.

    Live 2026-08-17: "عايزك تضيفه لي للسنة" (سلة) ran get_meal_details then
    the empty-turn fallback, and never add_to_cart.

    Do not recover from a spoken "أضفتها" alone — that re-added the meal
    while opening the cart, reading calories, reducing qty, or checking out.
    """
    user = last_user_text(chat_ctx)
    if not should_run_add_to_cart(user, chat_ctx):
        return None
    if chat_ctx is not None and tool_called_since_last_user(chat_ctx, "add_to_cart"):
        return None
    meal_id = latest_meal_id(chat_ctx)
    if not meal_id:
        return None
    return {
        "name": "add_to_cart",
        "arguments": {"meal_id": meal_id, "quantity": 1},
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


def recover_implied_get_saved(speech: str, chat_ctx: Any = None) -> Optional[dict[str, Any]]:
    """Recover get_saved when she claims bookmarks are empty (Bug-004)."""
    user = _fold_ar(last_user_text(chat_ctx))
    user_asked = any(
        w in user for w in ("محفوظ", "المفضله", "مفضله", "bookmark", "احذف", "حذف")
    )
    if not (claims_saved_empty(speech) or user_asked):
        return None
    return {
        "name": "get_saved",
        "arguments": {},
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


_OFFER_KIND_WORDS = {
    "combo": ("كومبو", "كمبو", "قنبو", "قمبو", "سومبو", "combo"),
    "family": ("عائلي", "عائله", "family"),
    "deal": ("ديل", "deal"),
}
_MENU_TAB_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("الاكثر طلبا", "الأكثر طلبا", "الاكثر طلباً", "popular"), "popular"),
    (("القائمه", "القائمة", "المنيو", "menu"), "menu"),
]
_HOME_CUISINES = (
    "شامي", "سعودي", "مشاوي", "برجر", "ياباني", "ايطالي", "إيطالي",
    "هندي", "صحي", "بحري", "حلويات", "فرايد تشيكن",
)


def ui_filter_args_from_user(user: str) -> dict[str, Any]:
    """Map a user utterance onto set_ui_filter arguments (chips, not dishes).

    Combo / family / deal / crispy / keto / restaurant-menu tabs / cuisine.
    Bare "عرض" is the Offers Deal chip; "العروض" is the offers page (open_page).
    "عرض شاورما" keeps the dish name and is left to search_meals.
    """
    folded = _fold_ar(user)
    if not folded:
        return {}
    args: dict[str, Any] = {}
    kinds: list[str] = []
    for kind, words in _OFFER_KIND_WORDS.items():
        if any(w in folded for w in words):
            kinds.append(kind)
    foodish = any(
        h in folded for h in _MEAL_FOOD_HINTS if h not in ("كومبو", "عائلي", "عرض")
    )
    # Chip "عرض" (singular) = deal, but only as a chip tap — not "عايز عرض
    # لتلات أشخاص" which is a meal search (live 2026-08-17 yanked the meal
    # page to /offers?kind=deal in a loop).
    if (
        "deal" not in kinds
        and "عرض" in folded
        and "عروض" not in folded
        and not foodish
    ):
        words = folded.split()
        chip_like = (
            "فلتر" in folded
            or "شيب" in folded
            or words == ["عرض"]
            or words == ["العرض"]
        )
        peopleish = any(
            w in folded
            for w in (
                "افراد",
                "اشخاص",
                "اصوات",
                "شخص",
                "تلات",
                "ثلاث",
                "عائلي",
            )
        )
        if chip_like and not peopleish:
            kinds.append("deal")
    if kinds:
        args["offers_kind"] = ",".join(kinds)
    query = ""
    if any(w in folded for w in ("كرسبي", "كريسبي", "مقرمش", "crispy")):
        query = "مقرمش"
    if any(w in folded for w in ("كيتو", "keto")):
        query = "كيتو" if not query else f"{query} كيتو"
    if query:
        args["query"] = query
    if _user_asked_cashback(user):
        args["cashback"] = True
    if any(w in folded for w in ("24 ساعه", "٢٤ ساعه", "24h", "24/7", "٢٤/٧", "على مدار")):
        args["restaurants_filter"] = "24h"
    elif "سريع" in folded and not foodish and "offers_kind" not in args:
        if "توصيل" in folded or "صفحه" in folded or len(folded.split()) <= 3:
            args["restaurants_filter"] = "fast"
    elif "بقاله" in folded and "offers_kind" not in args:
        args["restaurants_filter"] = "grocery"
    for needles, tab in _MENU_TAB_WORDS:
        if any(n in folded for n in needles):
            args["menu_tab"] = tab
            break
    else:
        if "عروض" in folded and "offers_kind" not in args and not _looks_like_nav_request(user):
            if "مطعم" in folded or "قائمه" in folded or "تبويب" in folded:
                args["menu_tab"] = "offers"
    for cuisine in _HOME_CUISINES:
        if _fold_ar(cuisine) in folded:
            args["cuisine"] = cuisine.replace("إيطالي", "ايطالي")
            break
    return args


def recover_implied_set_ui_filter(
    speech: str,
    chat_ctx: Any = None,
) -> Optional[dict[str, Any]]:
    """Recover set_ui_filter for Combo/Family/Deal/Crispy/Keto chips.

    Live: testers tap الكل / كومبو / عائلي / عرض. Saying the chip name (even
    without the word فلتر) must move the same URL the tap would.
    """
    user = last_user_text(chat_ctx)
    args = ui_filter_args_from_user(user)
    if not args:
        return None
    # Cuisine-only on a dish request is search_meals, not a home-chip flip.
    if list(args.keys()) == ["cuisine"] and any(
        h in _fold_ar(user) for h in _MEAL_FOOD_HINTS
    ):
        return None
    if chat_ctx is not None:
        if tool_called_since_last_user(chat_ctx, "set_ui_filter"):
            return None
        if tool_called_since_last_user(chat_ctx, "show_meal"):
            return None
        if tool_called_since_last_user(chat_ctx, "show_restaurant"):
            return None
    claimed = any(w in _fold_ar(speech) for w in ("فلتر", "طبقت", "صفيت", "شيب"))
    chip_intent = any(
        key in args
        for key in ("offers_kind", "query", "restaurants_filter", "menu_tab", "cashback")
    )
    if not (chip_intent or claimed or args.get("cuisine")):
        return None
    return {
        "name": "set_ui_filter",
        "arguments": args,
        "call_id": f"recovered_{uuid.uuid4().hex[:12]}",
    }


def build_offers_path(
    *,
    kind: str = "",
    cashback: bool = False,
    cuisine: str = "",
    query: str = "",
) -> str:
    """Build /offers?… matching the frontend chips (kind + cashback + cuisine)."""
    from urllib.parse import urlencode

    params: list[tuple[str, str]] = []
    if cashback:
        params.append(("cashback", "1"))
    if kind and kind != "all":
        params.append(("kind", kind))
    if cuisine:
        params.append(("cuisine", cuisine))
    if query:
        params.append(("q", query))
    if not params:
        return "/offers"
    return "/offers?" + urlencode(params, safe=",")


def prepare_chat_ctx_for_llm(chat_ctx: Any) -> Any:
    """Copy + truncate chat history so Gemma stays under 32768 tokens.

    Live 2026-08-16: full restaurant menus (with tashkeel) accumulated until
    the prompt was 32769 tokens and the API reserved 0 output tokens — the
    turn died with no speech. Truncation is in-place on a copy, not the
    session history.
    """
    if chat_ctx is None:
        return chat_ctx
    copy_fn = getattr(chat_ctx, "copy", None)
    ctx = copy_fn() if callable(copy_fn) else chat_ctx
    items = getattr(ctx, "items", None)
    if items is None:
        return ctx
    compacted: list[Any] = []
    for item in list(items):
        if getattr(item, "type", None) == "function_call_output":
            out = getattr(item, "output", "") or ""
            if len(out) > LLM_TOOL_OUTPUT_MAX_CHARS:
                clipped = out[:LLM_TOOL_OUTPUT_MAX_CHARS] + "…[truncated]"
                copier = getattr(item, "model_copy", None)
                if callable(copier):
                    item = copier(update={"output": clipped})
                else:
                    try:
                        item.output = clipped
                    except Exception:
                        pass
        compacted.append(item)
    try:
        ctx.items = compacted
    except Exception:
        pass
    trunc = getattr(ctx, "truncate", None)
    if callable(trunc):
        trunc(max_items=LLM_CHAT_MAX_ITEMS)
    return ctx
