"""Arabic text folding and word matching shared by the catalog and its tools.

Kept apart from ``src.catalog`` so offline scripts (``scripts/annotate_catalog.py``)
and the search engine (``src.search``) can use the same folding without loading
the catalog.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterable

# Marks removed before matching: tashkeel (the model emits it), dagger alef and
# tatweel. Hamza/maddah combining marks (U+0653-U+0655) are excluded because NFC
# folds them back into letters.
_MARKS = re.compile("[ً-ْٰـ]")

_LETTER_FOLDS = str.maketrans(
    {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ة": "ه",
        "ى": "ي", "ئ": "ي",
        "ؤ": "و",
    }
)


def normalize_ar(text: Any) -> str:
    """Fold Arabic text to a matchable form (tashkeel, hamza, ة/ه, ى/ي)."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    s = _MARKS.sub("", s)
    s = s.translate(_LETTER_FOLDS)
    return " ".join(s.split()).lower()


@lru_cache(maxsize=1024)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Match a keyword at the start of a word, allowing Arabic prefixes.

    Plain substring matching mis-tags food: "ريش" (ribs) is inside "جريش".
    A bare ``\\b`` is too strict the other way, because Arabic glues articles
    and prepositions on — "باللحمة" has to match لحم. The keyword may follow the
    article and/or one prefix letter, and may carry at most two trailing
    letters (لحمة, دجاجه), so "لاسي" no longer matches inside "كلاسيكية".
    Keywords shorter than three letters get no tail at all.
    """
    tail = r"\w{0,2}" if len(keyword) >= 3 else ""
    return re.compile(rf"(?<!\w)[وبفلك]?(?:ال)?{re.escape(keyword)}{tail}(?!\w)")


def has_keyword(blob: str, keywords: Iterable[str]) -> bool:
    return any(keyword_pattern(k).search(blob) for k in keywords)
