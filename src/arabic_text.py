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

_ARABIC_CHARACTER = re.compile("[\\u0600-\\u06ff]")
_MIXED_PLACE_CODE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9.-]*[A-Za-z])"
    r"(?=[A-Za-z0-9.-]*\d)"
    r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*"
    r"(?![A-Za-z0-9])"
)
_CODE_PART = re.compile(r"[A-Za-z]+|\d+")
_ARABIC_LETTER_NAMES = {
    "A": "إيه", "B": "بي", "C": "سي", "D": "دي", "E": "إي",
    "F": "إف", "G": "جي", "H": "إتش", "I": "آي", "J": "جاي",
    "K": "كيه", "L": "إل", "M": "إم", "N": "إن", "O": "أو",
    "P": "بي", "Q": "كيو", "R": "آر", "S": "إس", "T": "تي",
    "U": "يو", "V": "في", "W": "دبليو", "X": "إكس", "Y": "واي",
    "Z": "زد",
}
_ARABIC_SMALL_NUMBERS = (
    "صفر", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة",
    "ثمانية", "تسعة", "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر",
    "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر",
    "تسعة عشر",
)
_ARABIC_TENS = {
    20: "عشرين", 30: "ثلاثين", 40: "أربعين", 50: "خمسين",
    60: "ستين", 70: "سبعين", 80: "ثمانين", 90: "تسعين",
}
_ARABIC_HUNDREDS = {
    1: "مئة", 2: "مئتين", 3: "ثلاثمئة", 4: "أربعمئة", 5: "خمسمئة",
    6: "ستمئة", 7: "سبعمئة", 8: "ثمانمئة", 9: "تسعمئة",
}


def _arabic_number_words(value: str) -> str:
    if len(value) > 1 and value.startswith("0"):
        return " ".join(_ARABIC_SMALL_NUMBERS[int(digit)] for digit in value)
    number = int(value)
    if number < 20:
        return _ARABIC_SMALL_NUMBERS[number]
    if number < 100:
        tens = number // 10 * 10
        remainder = number % 10
        return (
            f"{_ARABIC_SMALL_NUMBERS[remainder]} و{_ARABIC_TENS[tens]}"
            if remainder
            else _ARABIC_TENS[tens]
        )
    if number < 1000:
        hundreds = _ARABIC_HUNDREDS[number // 100]
        remainder = number % 100
        return (
            f"{hundreds} و{_arabic_number_words(str(remainder))}"
            if remainder
            else hundreds
        )
    # LEAP's map codes currently stay below 1,000. Reading unexpected longer
    # identifiers digit by digit is safer for speech than leaving Latin digits.
    return " ".join(_ARABIC_SMALL_NUMBERS[int(digit)] for digit in value)


def normalize_arabic_speech_codes(text: str) -> str:
    """Spell mixed Latin/digit place codes as words inside Arabic speech.

    Examples: ``G-21`` becomes ``جي واحد وعشرين`` and ``H1.E30`` becomes
    ``إتش واحد إي ثلاثين``. Pure English utterances are deliberately untouched.
    """
    if not _ARABIC_CHARACTER.search(text):
        return text

    def replace_code(match: "re.Match[str]") -> str:
        spoken_parts: list[str] = []
        for part in _CODE_PART.findall(match.group(0)):
            if part.isdigit():
                spoken_parts.append(_arabic_number_words(part))
            else:
                spoken_parts.extend(
                    _ARABIC_LETTER_NAMES[letter]
                    for letter in part.upper()
                )
        return " ".join(spoken_parts)

    return _MIXED_PLACE_CODE.sub(replace_code, text)


def strip_tashkeel(text: str) -> str:
    """Strip diacritics for display, preserving letter identity.

    The NFC pass is load-bearing. Hamza and maddah also exist as COMBINING marks
    (U+0653-U+0655), so decomposed text spells أ as ا+U+0654. Deleting that range
    silently rewrites letters — رئيس becomes رييس, مؤسسة becomes موسسة — so it
    stays out of the class above, and composing first turns any such sequence
    back into a single letter no rule here can touch.
    """
    return _TASHKEEL_AND_MARKS.sub("", unicodedata.normalize("NFC", text))
