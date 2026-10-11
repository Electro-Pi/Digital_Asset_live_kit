"""Input guardrails: the few caller turns answered before the model sees them.

Two kinds, both checked once per committed turn in
``HarnessAgent.on_user_turn_completed`` (never in the LLM node, which may run
speculatively):

* **Noise.** The ASR sometimes returns a stretched howl («أيييييي…») or one
  unknown word repeated («إيميشي إيميشي …»). Gemma read those as a yes. They
  are dropped without a reply.
* **Abuse of the agent itself**: attempts to override her instructions, to
  extract the prompt or other customers' data, or to talk her into a discount
  or a free item she has no tool to grant. These get a fixed, safe line.

Everything else — price objections, hesitation, small talk — is selling, and
belongs to the model and its prompt.
"""

from __future__ import annotations

import re
from typing import Optional

from src.speech_claims import fold

ABUSE_PATTERNS: dict[str, tuple[str, ...]] = {
    "jailbreak": (
        "تجاهل تعليم", "انسى كل شي", "انس كل شي", "ignore your", "ignore instructions",
        "ignore previous", "jailbreak", "developer mode",
    ),
    "prompt": (
        "وش تعليماتك", "ورني البرومبت", "وريني البرومبت", "system prompt", "البرومبت",
        "البرومت", "السيستم برومبت",
    ),
    "internal": ("عدد الطلبات", "طلبات اليوم", "بيانات العميل", "بيانات عميل", "رقم عميل ثاني"),
    "discount": ("سويلي خصم", "سوي لي خصم", "عطني خصم", "ابي خصم", "ابغى خصم", "كود خصم"),
    "free": ("خله فاضي", "خليها فاضيه", "لا تحسبه", "ما تحسبه", "خله مجاني", "ببلاش", "على الحساب"),
}


def abuse_kind(text: str) -> Optional[str]:
    folded = fold(text)
    if not folded:
        return None
    for kind, patterns in ABUSE_PATTERNS.items():
        if any(p in folded for p in patterns):
            return kind
    return None


_SHORT_REPLIES = frozenset({"لا", "مو", "مش", "اي", "ايه", "يب", "اوك", "تمام", "ماشي", "نعم", "لالا"})


def is_asr_noise(text: str, known_words: frozenset[str] = frozenset()) -> bool:
    """True for a stretched howl or one unknown word repeated four times or more.

    Digit runs (a phone number 055555…) and repeated real words («لا لا لا»,
    «بطاطس بطاطس») are speech.
    """
    folded = fold(text)
    if not folded:
        return False
    letters = re.sub(r"[\d\s+.,،\-]+", "", folded)
    if len(letters) >= 8:
        run = re.search(r"(.)\1{7,}", letters)
        if run and len(run.group(0)) >= max(8, int(0.6 * len(letters))):
            return True
    words = folded.split()
    if len(words) >= 4 and len(set(words)) == 1:
        token = words[0]
        if token in _SHORT_REPLIES or len(token) < 4:
            return False
        return token not in known_words
    return False
