"""How completely a line is diacritized, by the rule the TTS needs.

The rule (see ``src.prompts.agent_prompt``): a mark on every letter of a
word except the last. Some letters carry no mark by nature and are not
counted as missing:

- ا ى آ ٱ — a long alef, or the silent alef of «ال» and «اثنين»;
- a long و after a damma, a long ي after a kasra («صُوص», «فِيه»);
- the ل of «ال» (moon letters read the same with or without its sukun;
  before a sun letter it is silent and the shadda sits on the next letter).

Everything else before the last letter must carry a vowel, sukun or shadda.
Used to score prompts (``scripts/optimize_tashkeel_prompt.py``) and in tests
for the lines code speaks word for word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"[ء-ٰٟ]+")
_MARKS = frozenset(chr(c) for c in range(0x064B, 0x0653))
_DAMMA, _KASRA = "ُ", "ِ"
_NO_MARK_NEEDED = frozenset("اىآٱ")
# Proclitics that can stand before «ال»: و ف ب ك ل, their pairs, and the
# Saudi demonstrative ها («هَالشَّي»، «بِهَالسِّعْر»).
_ARTICLE = re.compile(r"^(?:[وف]?[بكل]?)(?:ال|هال)")


def _letters(word: str) -> list[tuple[str, str]]:
    """(letter, its marks) for each letter in the word."""
    out: list[tuple[str, str]] = []
    for ch in word:
        if ch in _MARKS:
            if out:
                out[-1] = (out[-1][0], out[-1][1] + ch)
        elif "ء" <= ch <= "ي":
            out.append((ch, ""))
    return out


def missing_marks(word: str) -> tuple[int, int]:
    """(letters that need a mark, of those the ones without one); last letter excluded."""
    letters = _letters(word)
    if len(letters) < 2:
        return 0, 0
    bare = "".join(ch for ch, _ in letters)
    article_lam = -1
    m = _ARTICLE.match(bare)
    if m and len(bare) > m.end():
        article_lam = m.end() - 1
    needed = missing = 0
    for i, (ch, marks) in enumerate(letters[:-1]):
        if ch in _NO_MARK_NEEDED or i == article_lam:
            continue
        prev = letters[i - 1][1] if i else ""
        if not marks and ((ch == "و" and _DAMMA in prev) or (ch == "ي" and _KASRA in prev)):
            continue
        needed += 1
        if not marks:
            missing += 1
    return needed, missing


def last_letter_marked(word: str) -> bool:
    """A vowel, sukun or tanween on the last letter (إعراب; the TTS reads it)."""
    letters = _letters(word)
    return len(letters) >= 2 and bool(letters[-1][1])


@dataclass
class Audit:
    words: int = 0
    bare_words: list[str] = field(default_factory=list)
    partial_words: list[str] = field(default_factory=list)
    needed: int = 0
    missing: int = 0
    final_marked: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return 1.0 if not self.needed else 1 - self.missing / self.needed

    @property
    def clean_finals(self) -> float:
        return 1.0 if not self.words else 1 - len(self.final_marked) / self.words


def audit(text: str) -> Audit:
    result = Audit()
    for word in _WORD.findall(text or ""):
        needed, missing = missing_marks(word)
        if len(_letters(word)) < 2:
            continue
        result.words += 1
        result.needed += needed
        result.missing += missing
        if needed and missing == needed:
            result.bare_words.append(word)
        elif missing:
            result.partial_words.append(word)
        if last_letter_marked(word):
            result.final_marked.append(word)
    return result
