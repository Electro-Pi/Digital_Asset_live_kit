"""Display-side Arabic text handling for the agent's published transcript.

The agent writes diacritized Arabic on purpose: the prompt asks for
word-internal tashkeel (bare word endings, so no i'rab) so the TTS voices the
vowels instead of guessing them, and those marks survive all the way into the
synthesis request — see ``normalize_arabic`` in
``src/plugins/voxcpm_tts_plugin.py``.

The marks are for the voice, not the reader. Ordinary Saudi prose is written
bare, and a fully vowelled transcript reads like a schoolbook, so the text
published to the room is stripped instead. Mirrors ``stripTashkeel`` in the
frontend's ``src/lib/arabic.ts``: the frontend strip stays as a second line of
defence for text from other sources, but doing it here is what makes the rule
hold for every client, not just this one.
"""

from __future__ import annotations

import re
import unicodedata

# Removed:
#   * U+064B-U+0652  — standard tashkeel (fatha…sukun, shadda, tanween)
#   * U+0670         — dagger alef; a real long /aː/ in هٰذا, but dropping it
#                      yields هذا, which is the ordinary bare spelling
#   * U+0640 tatweel — a typographic stretch, never phonetic
#   * U+0610-U+061A  — honorific annotations
#   * U+06D6-U+06ED  — Quranic recitation/annotation marks
_TASHKEEL_AND_MARKS = re.compile(
    "[\\u064b-\\u0652\\u0670\\u0640\\u0610-\\u061a\\u06d6-\\u06dc\\u06df-\\u06e8\\u06ea-\\u06ed]"
)


def strip_tashkeel(text: str) -> str:
    """Strip diacritics for display, preserving letter identity.

    The NFC pass is load-bearing. Hamza and maddah also exist as COMBINING marks
    (U+0653-U+0655), so decomposed text spells أ as ا+U+0654. Deleting that range
    silently rewrites letters — رئيس becomes رييس, مؤسسة becomes موسسة — so it
    stays out of the class above, and composing first turns any such sequence
    back into a single letter no rule here can touch.
    """
    return _TASHKEEL_AND_MARKS.sub("", unicodedata.normalize("NFC", text))
