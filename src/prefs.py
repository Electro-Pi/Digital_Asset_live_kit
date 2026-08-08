"""Turn the frontend's preferences JSON into a prompt block for لقمة.

The web client stamps the onboarding answers into LiveKit participant metadata
(see ``jahez-frontend/components/voice-widget/VoiceWidget.tsx``), alongside the
``voice`` key that ``voice_profile.py`` reads:

    {
      "voice": "female",
      "prefs": {
        "done": true,
        "skipped": false,
        "schemaVersion": 8,
        "calories": {
          "preference": "care",          # care | dont_care | unknown
          "cares_about_calories": true,
          "label_ar": "يهتم بالسعرات الغذائية",
          "label_en": "User cares about calorie counts"
        }
      }
    }

Metadata is fixed at token-mint time, so this is read once per session and
appended to the system prompt rather than refreshed mid-call.

Unknown keys are rendered generically on purpose. The onboarding flow is
expected to grow (it is already on ``schemaVersion`` 8, and has shrunk to a
single question), and a new preference should reach the model by shipping the
frontend alone — a backend release just to teach it one more key would mean the
two repos have to deploy together. Keys this module knows about get phrasing
tuned for the voice agent; the rest fall back to their ``label_ar``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

# Bookkeeping fields of the prefs payload — real answers are everything else.
_META_KEYS = {"done", "skipped", "schemaVersion", "schema_version", "version"}

_HEADER = "# تفضيلات المستخدم (من تطبيق جاهز)"

_HEADER_NOTE = (
    "هذي معلومات وصلتك من التطبيق عن المستخدم اللي يكلمك الحين، مو كلام قاله "
    "بصوته. استخدميها بصمت وانت ترشحين، ولا تقرينها عليه، ولا تكررين "
    "\"حسب تفضيلاتك\" في كل رد."
)

_NO_PREFS = (
    "المستخدم ما عبى أسئلة التفضيلات، فما عندك أي معلومة محفوظة عنه. تعاملي "
    "معه كمستخدم جديد، وإذا احتجت معلومة اسألي سؤال واحد قصير."
)

# Per-preference phrasing: what the agent should actually *do*, not a restating
# of the label. A label alone ("يهتم بالسعرات") tends to get read out loud.
_CALORIES_GUIDANCE = {
    "care": (
        "يهتم بالسعرات الحرارية. اذكري عدد السعرات باختصار لما ترشحين وجبة، "
        "ورجحي الخيار الأخف إذا كان يناسب طلبه."
    ),
    "dont_care": (
        "ما يهتم بالسعرات الحرارية. لا تفتحين موضوع السعرات ولا الدايت إلا إذا "
        "سأل هو عنها."
    ),
    "unknown": (
        "ما حدد موقفه من السعرات الحرارية. لا تفترضين شي، واذكريها فقط إذا سأل."
    ),
}


def parse_prefs_from_metadata(metadata: Optional[str]) -> Optional[dict[str, Any]]:
    """Extract the ``prefs`` object from participant metadata.

    Returns ``None`` for anything unusable — absent, malformed, or the wrong
    shape. A bad payload must not take the session down, so every failure here
    degrades to "no preferences" and is logged.
    """
    if not metadata:
        return None
    try:
        meta = json.loads(metadata)
    except (ValueError, TypeError) as e:
        logger.warning("Participant metadata is not valid JSON: {}", e)
        return None
    if not isinstance(meta, dict):
        logger.warning(
            "Participant metadata is {} not an object; ignoring preferences",
            type(meta).__name__,
        )
        return None
    prefs = meta.get("prefs")
    if prefs is None:
        return None
    if not isinstance(prefs, dict):
        logger.warning(
            "Participant metadata `prefs` is {} not an object; ignoring",
            type(prefs).__name__,
        )
        return None
    return prefs


def _answer_keys(prefs: dict[str, Any]) -> list[str]:
    return [k for k in prefs if k not in _META_KEYS]


def _render_calories(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        preference = value.get("preference")
        if preference is None and "cares_about_calories" in value:
            preference = "care" if value.get("cares_about_calories") else "dont_care"
    elif isinstance(value, bool):
        preference = "care" if value else "dont_care"
    else:
        preference = value

    guidance = _CALORIES_GUIDANCE.get(str(preference).strip().lower())
    if guidance is None:
        return None
    return f"- السعرات: {guidance}"


def _render_generic(key: str, value: Any) -> Optional[str]:
    """Best-effort line for a preference this module has not been taught.

    Prefers the Arabic label the frontend already writes, so a new question
    ships without touching this file.
    """
    if isinstance(value, dict):
        label = value.get("label_ar") or value.get("label_en")
        if not label:
            parts = [
                f"{k}: {v}"
                for k, v in value.items()
                if not isinstance(v, (dict, list)) and v is not None
            ]
            label = "، ".join(parts) if parts else None
    elif isinstance(value, bool):
        label = "نعم" if value else "لا"
    elif isinstance(value, list):
        label = "، ".join(str(v) for v in value if v is not None) or None
    elif value is None:
        label = None
    else:
        label = str(value).strip() or None

    if not label:
        return None
    return f"- {key}: {label}"


def _render_pref(key: str, value: Any) -> Optional[str]:
    if key == "calories":
        line = _render_calories(value)
        if line is not None:
            return line
    return _render_generic(key, value)


def build_prefs_context(metadata: Optional[str]) -> str:
    """Build the prompt block appended to لقمة's system prompt.

    Always returns a block once metadata carries a ``prefs`` object, including
    the "nothing saved" case — telling the model it has no preferences is what
    stops it inventing some. Returns ``""`` only when the frontend sent no
    preferences at all (e.g. a direct LiveKit connection).
    """
    prefs = parse_prefs_from_metadata(metadata)
    if prefs is None:
        return ""

    lines: list[str] = []
    if prefs.get("done") and not prefs.get("skipped"):
        for key in _answer_keys(prefs):
            line = _render_pref(key, prefs[key])
            if line:
                lines.append(line)

    body = "\n".join(lines) if lines else _NO_PREFS
    return f"{_HEADER}\n{_HEADER_NOTE}\n{body}"


def summarize_prefs(metadata: Optional[str]) -> str:
    """One-line log summary: which preferences arrived, without the prose."""
    prefs = parse_prefs_from_metadata(metadata)
    if prefs is None:
        return "none"
    if not prefs.get("done") or prefs.get("skipped"):
        return f"skipped=True done={bool(prefs.get('done'))}"
    pairs = []
    for key in _answer_keys(prefs):
        value = prefs[key]
        if isinstance(value, dict):
            value = value.get("preference", value.get("label_en", "?"))
        pairs.append(f"{key}={value}")
    return " ".join(pairs) or "empty"
