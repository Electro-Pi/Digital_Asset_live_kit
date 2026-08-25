"""In-memory search over the static Jahez restaurant/meal catalog.

The catalog is 11 restaurants / 110 meals / ~128KB, so it is loaded once per
process and filtered with plain Python. There is deliberately no service, no
database and no network hop here: a round trip to fetch a 128KB dict would cost
latency on a live voice call and buy nothing. When the catalog outgrows the
process (its own write path, its own scaling), the bodies of these functions
change — the tools that call them do not.

Three things in here are less obvious than they look:

* **Text is normalized before matching.** The سلمى prompt makes the LLM write
  Arabic *with* tashkeel, so a tool argument arrives as "دَجاج" while the catalog
  says "دجاج". Matching raw strings silently returns nothing. Everything on both
  sides goes through ``normalize_ar`` first, which also folds hamza forms,
  taa marbuta and alef maqsura — the spellings users and the model disagree on.

* **Protein tags are derived, not stored.** The catalog has no "meat/chicken/
  fish" field, but "I want something with meat" is the single most common way to
  ask. Tags are inferred from name + description keywords, and a dish can carry
  several — "مشكل مشاوي" is described as "لحم ودجاج", so it is honestly both.
  ``vegetarian`` is only applied from an explicit allow-list, never as "no meat
  keyword found": شوربة حريرة is cooked with مرقة, and guessing there would be a
  false claim about someone's food.

* **Groceries are excluded by default.** ``ماركت لقمة`` is in the same catalog
  as the restaurants, and nobody asking for dinner wants 5kg of basmati rice.
  The frontend's own ``filterRestaurants`` hides it the same way.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

from src.spoken_lexicon import (  # noqa: E402
    PRONUNCIATION_FIXES,
    clean_for_speech,
    fix_pronunciation,
    sanitize_tashkeel,
    speakable_name,
    written_phone,
    written_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_PATH = REPO_ROOT / "resturant's-catalog" / "catalog.json"

# Marks removed before matching: tashkeel (the model now emits it), dagger alef
# and tatweel. Hamza/maddah combining marks (U+0653-U+0655) are excluded because
# NFC folds them back into letters — see the same reasoning in the TTS plugin.
_MARKS = re.compile("[ً-ْٰـ]")

_LETTER_FOLDS = str.maketrans(
    {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ة": "ه",
        "ى": "ي", "ئ": "ي",
        "ؤ": "و",
    }
)

# Cuisines that are not "a meal" in the sense سلمى recommends.
GROCERY_CUISINES = {"بقاله"}

# Keyword → protein tag. Matched against normalized name + description, so the
# keys are written in normalized form (ة→ه, أ→ا).
_PROTEIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "meat": (
        "لحم", "لحوم", "لحمه", "غنم", "بقري", "بقر", "كفته", "كباب", "ستيك",
        "كبده", "بيبروني", "بولونيز", "فيندالو", "برجر", "ريش",
    ),
    # "تكا" and "برياني" are deliberately absent: both name a preparation, not a
    # protein (بانير تكّا is cheese, برياني exists with either), and every
    # chicken instance in the catalog says دجاج in its name or description.
    "chicken": ("دجاج", "دجاجه", "تشيكن", "فراخ", "مظبي", "طاووق", "وينجز", "اجنحه", "ستربس"),
    "seafood": (
        "سمك", "سمكه", "روبيان", "جمبري", "سلمون", "تونا", "هامور", "كاليماري",
        "حبار", "صياديه", "انقليس", "تمبورا", "بحري", "سوشي", "فيليه",
    ),
}

# Vegetarian is an allow-list, never an inference from absence. See module docs.
_VEGETARIAN_KEYWORDS = (
    "خضار", "سلطه", "فلافل", "حمص", "فول", "عدس", "دال", "بانير", "كينوا",
    "افوكادو", "سموذي", "زبادي", "ادمامي", "توفو", "مناقيش", "زعتر", "فتوش",
    "سبانخ", "متبل", "بطاطس", "بصل", "خيار", "جزر", "مشروم", "زيتون", "جبن",
    "موزاريلا",
)

# "لاسي" is deliberately absent: at four letters it still matches inside
# "كلاسيك" once a ك prefix is allowed, which tagged برجر كلاسيك as a drink. The
# lassi's own description says "مشروب", so nothing is lost.
_DRINK_KEYWORDS = (
    "قهوه", "عصير", "مشروب", "ميلك شيك", "سموذي", "لبن", "مياه", "حليب", "شاي",
)

_DESSERT_CUISINES = {"حلويات"}

# Dessert is a property of the dish, not of the shop. Taking it from the
# restaurant's cuisine alone worked while "حلويات" meant a sweets counter; in
# the current catalog it also covers cafés and bakeries ("كافيه الهيل",
# "بار التوست", "مختبر الكرواسون"), so "أبغى تحلية" was answered with a latte,
# karak tea and avocado toast. Those are wrong answers, not weak ones, because
# the dessert hint is a filter.
_DESSERT_KEYWORDS = (
    "حلا", "حلويات", "حلاوه", "كنافه", "بقلاوه", "بسبوسه", "لقيمات", "معمول",
    "كيك", "كيكه", "تشيز كيك", "براوني", "تيراميسو", "مولتن", "وافل", "كريب",
    "دونات", "مافن", "سينابون", "ايس كريم", "مثلجات", "جيلاتي", "قطايف",
    "مهلبيه", "ام علي", "نوتيلا", "شوكولاته", "تمر محشي", "بوكس حلويات",
)

# Savoury words that override the cuisine fallback below. A bakery's toast and
# sandwiches share its "حلويات" cuisine but are not dessert.
_SAVOURY_KEYWORDS = (
    "توست", "ساندويتش", "راب", "سلطه", "بيض", "جبنه", "دجاج", "لحم", "برجر",
    "بيتزا", "شوربه", "فلافل", "حمص", "مناقيش", "منقوشه", "بعجين", "مسخن",
    "فطور", "صحن", "نقانق", "بيكون",
)

# Pairing uses the dish *name*, not noisy diet tags. "فتة" matching "لبن"
# inside a description is not a drink; "ميلك شيك" in the name is.
_PAIR_DRINK_NAME = (
    "قهوه", "لاتيه", "ميلك شيك", "عصير", "مياه", "مشروب", "شاي",
    "سموذي", "كولا", "غازي",
)
_PAIR_SIDE_NAME = ("بطاطس", "سلطه", "فتوش", "حمص", "مخلل", "ثوم")
_PAIR_ADDON_DRINK = ("مشروب", "غازي", "عصير", "كولا")
_PAIR_ADDON_SIDE = ("بطاطس", "ثوم", "مخلل", "صلصه", "صوص")

# Thresholds used for derived tags and for the "why this meal" reasons. They are
# named because they appear in both filtering and explanation, and drifting them
# apart would make سلمى justify a pick with a rule it did not actually use.
LIGHT_CALORIES_MAX = 400
HIGH_PROTEIN_MIN = 30
RICH_CALORIES_MIN = 650
RICH_FAT_MIN = 30
FAMILY_KEYWORDS = ("عائلي", "عائليه", "يكفي", "كبير")
CRISPY_KEYWORDS = ("مقرمش", "كرسبي", "كريسبي", "crispy")
KETO_CARBS_MAX = 20

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# How many people a meal feeds. The catalog states this in prose only — "لـ 6
# أشخاص" in the name, "تكفي 6 أشخاص" or "وجبة كاملة لشخص واحد" in the
# description — so it has to be parsed out. Leaving it unparsed is what let
# سلمى answer "تكفي الستة" about a single 25-riyal shawarma wrap and invent a
# 94-riyal family meal that does not exist: the prompt promises she can answer
# "يكفي كم شخص" while no field ever carried the number.
_SERVES_RE = re.compile(r"(?:لـ?\s*|تكفي\s*)([0-9]{1,2})\s*(?:أشخاص|اشخاص|شخص|أفراد|افراد)")
_SERVES_ONE_RE = re.compile(r"لشخص\s+واحد|فردي")


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Match a keyword only at the start of a word, allowing Arabic prefixes.

    Plain substring matching mis-tags food: "ريش" (ribs) is inside "جريش", so
    the Saudi wheat dish came back as meat. A bare ``\\b`` is too strict in the
    other direction, because Arabic glues its articles and prepositions on —
    "باللحمة" and "بالروبيان" have to keep matching لحم and روبيان. So the
    keyword may be preceded by the definite article and/or a single-letter
    prefix, but nothing else.

    The tail is bounded too, at two characters, because anchoring only the
    start is not enough: the drink keyword "لاسي" matched inside "كلاسيكية"
    (ك read as a prefix), which tagged every "classic" dish as a drink. Two
    characters still covers the real suffixes — لحمة, مشويات, دجاجه — while a
    three-letter tail like كيه no longer qualifies.
    """
    return re.compile(
        rf"(?<!\w)[وبفلك]?(?:ال)?{re.escape(keyword)}\w{{0,2}}(?!\w)"
    )


def _has_keyword(blob: str, keywords: Iterable[str]) -> bool:
    return any(_keyword_pattern(k).search(blob) for k in keywords)


def normalize_ar(text: Any) -> str:
    """Fold Arabic text to a matchable form (tashkeel, hamza, ة/ه, ى/ي)."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    s = _MARKS.sub("", s)
    s = s.translate(_LETTER_FOLDS)
    return " ".join(s.split()).lower()


# ASR regularly mangles Levantine dish names into near-homophones that mean
# something else entirely. Live failure: "مناقيش زعتر" arrived as
# "ما نعيش زعتر" / "ملايش زعتر" / "ملاقيش", and سلمى answered "ما عندنا"
# without ever finding the dish sitting on the open restaurant page.
# Longer phrases first so "ما نعيش" wins over a bare "نعيش".
_ASR_PHRASE_FIXES: tuple[tuple[str, str], ...] = (
    ("ما نعيش", "مناقيش"),
    ("ماعيش", "مناقيش"),
    ("ملايش", "مناقيش"),
    ("ملاقيش", "مناقيش"),
    ("مناقش", "مناقيش"),
    ("منقوشه", "مناقيش"),
    ("منقوشة", "مناقيش"),
    ("شااورما", "شاورما"),
    ("شاورمه", "شاورما"),
    ("شاورمة", "شاورما"),
    ("مزق الشام", "مذاق الشام"),
    ("مزاج الشام", "مذاق الشام"),
    # Live failure 2026-08-12: "عرض على الشوارع" (streets) for شاورما.
    ("عرض على الشوارع", "عرض شاورما"),
    ("على الشوارع", "شاورما"),
    ("الشوارع", "شاورما"),
    # New fried-chicken catalog (2026-08-13): users and ASR write ستريبس /
    # استربس while the meals are named ستربس.
    ("ستريبس", "ستربس"),
    ("ستريب", "ستربس"),
    ("استربس", "ستربس"),
    # Live failure 2026-08-13: "مليانة دهون" arrived as "مديانة دهون".
    ("مديانه", "مليانه"),
    ("مديانة", "مليانة"),
    # Live 2026-08-16 tester: كومبو arrived as قنبو / قمبو / سومبو / قنبوع.
    ("قنبو", "كومبو"),
    ("قمبو", "كومبو"),
    ("سومبو", "كومبو"),
    ("قنبوع", "كومبو"),
    ("كمبو", "كومبو"),
    ("combo", "كومبو"),
    ("كرسبي", "مقرمش"),
    ("كريسبي", "مقرمش"),
    ("crispy", "مقرمش"),
    ("كيتو", "كيتو"),
    ("keto", "كيتو"),
    ("cashback", "كاش باك"),
    ("cash back", "كاش باك"),
    ("كاشباك", "كاش باك"),
    ("اكله شوكليت", "اتولييه الشوكولاته"),
    ("اكلة شوكليت", "أتولييه الشوكولاتة"),
    ("اكلين شوكليت", "أتولييه الشوكولاتة"),
    ("اتليه الشوكولاته", "اتولييه الشوكولاته"),
    ("اتيليه الشوكولاته", "اتولييه الشوكولاته"),
    ("ونفد المانجا", "وافل مانجا"),
    # Live 2026-08-17: جنيه for ريال; ورقاني for وريني.
    ("الجنيه", "الريال"),
    ("جنيه", "ريال"),
    ("ورقاني", "وريني"),
    ("وركاني", "وريني"),
    ("للسنة", "للسلة"),
    ("للسنه", "للسلة"),
    ("للسلامة", "للسلة"),
    ("للسلامه", "للسلة"),
    ("السالب", "السله"),
    ("تلات اصوات", "تلات اشخاص"),
    ("ثلاث اصوات", "ثلاث اشخاص"),
)

# Whole-utterance ASR repairs for navigation. Applied to the transcript the
# LLM hears, not just catalog search — "روحي عالسلة" was landing as
# "السالفة" (a story) so open_page(cart) never fired.
_ASR_UTTERANCE_FIXES: tuple[tuple[str, str], ...] = (
    ("السالفه", "السله"),
    ("السالفة", "السلة"),
    ("عالسالفه", "عالسلة"),
    ("عالسالفة", "عالسلة"),
    ("للسالفه", "للسلة"),
    ("للسالفة", "للسلة"),
    ("في السالفه", "في السلة"),
    ("في السالفة", "في السلة"),
    ("الكارت", "السلة"),
    ("الكرت", "السلة"),
    ("الكارد", "السلة"),
    ("الكات", "السلة"),
    ("the cart", "السلة"),
    ("to cart", "السلة"),
    ("مديانة", "مليانة"),
    ("مديانه", "مليانه"),
    ("قنبو", "كومبو"),
    ("قمبو", "كومبو"),
    ("سومبو", "كومبو"),
    ("قنبوع", "كومبو"),
    ("صحة المطاعم", "صفحة المطاعم"),
    ("على المطبخ", "على المطاعم"),
    ("صفحة المطبخ", "صفحة المطاعم"),
    ("ادخل على المطبخ", "ادخل على المطاعم"),
    # Live 2026-08-16: "أتولييه الشوكولاتة" arrived as أكلة / أكلين شوكليت
    ("اكله شوكليت", "اتولييه الشوكولاته"),
    ("اكلة شوكليت", "أتولييه الشوكولاتة"),
    ("اكلين شوكليت", "أتولييه الشوكولاتة"),
    ("مطعم اكلين شوكليت", "أتولييه الشوكولاتة"),
    ("مطعم اكله شوكليت", "أتولييه الشوكولاتة"),
    ("اتليه الشوكولاته", "اتولييه الشوكولاته"),
    ("اتيليه الشوكولاته", "اتولييه الشوكولاته"),
    # Live 2026-08-16: waffle mango arrived as ونفد المانجا
    ("ونفد المانجا", "وافل مانجا"),
    ("ونفد مانجا", "وافل مانجا"),
    # Live 2026-08-17: "ضيفه للسلة" arrived as للسنة / للسلامة.
    ("للسنة", "للسلة"),
    ("للسنه", "للسلة"),
    ("للسلامة", "للسلة"),
    ("للسلامه", "للسلة"),
    ("ورقاني", "وريني"),
    ("وركاني", "وريني"),
    ("الجنيه", "الريال"),
    ("جنيه", "ريال"),
    ("السالب", "السلة"),
    ("هذا السالب", "هذا السلة"),
    ("لتلات اصوات", "لتلات اشخاص"),
    ("لثلاث اصوات", "لثلاث اشخاص"),
    ("تلات اصوات", "تلات اشخاص"),
    ("ثلاث اصوات", "ثلاث اشخاص"),
)


def repair_asr_query(text: str) -> str:
    """Rewrite common ASR mishearings before catalog matching."""
    if not text:
        return ""
    raw = str(text)
    # Apply on the diacritic-stripped form so tashkeel does not block a hit,
    # then map the replacements back through a simple whole-string rewrite.
    folded = normalize_ar(raw)
    for src, dst in _ASR_PHRASE_FIXES:
        src_n = normalize_ar(src)
        if src_n and src_n in folded:
            folded = folded.replace(src_n, normalize_ar(dst))
    return folded


def repair_user_transcript(text: str) -> str:
    """Rewrite ASR slips in the utterance the model actually hears.

    ``repair_asr_query`` folds ة→ه and strips hamza, which is right for catalog
    matching and wrong for the LLM: it would receive a smashed spelling of the
    whole turn. Here we only swap the known mishearings in place.
    """
    out = " ".join(str(text or "").split())
    if not out:
        return ""
    for src, dst in _ASR_UTTERANCE_FIXES:
        if not src:
            continue
        if src.isascii():
            out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
        elif src in out:
            out = out.replace(src, dst)
    return out


# Headcounts as users say them. Keys are normalize_ar output (ة→ه folded).
_COUNT_WORD_VALUES: dict[str, int] = {
    "واحد": 1, "اثنين": 2, "ثنين": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4,
    "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
}

# "بيتزا لثلاث أشخاص" / "بيتزا لـ 3 أشخاص" / "أكل يكفي ٥ أفراد". The tatweel in
# "لـ" is stripped by normalize_ar, so the proclitic here is a bare ل.
_QUERY_HEADCOUNT_RE = re.compile(
    r"ل?\s*(\d{1,2}|" + "|".join(_COUNT_WORD_VALUES) + r")\s*(?:اشخاص|شخص|افراد|انفار|نفر)(?!\w)"
)
_QUERY_DUAL_RE = re.compile(r"(?<!\w)ل?(?:شخصين|نفرين)(?!\w)")

# "أنا ومعايا خمسة" = me + five others = 6. Number words are already folded
# (خمسة → خمسه) by normalize_ar before we see them.
_WITH_ME_COUNT_RE = re.compile(
    r"(?<!\w)انا\s+و?\s*(?:معايا|معي|معاي|معانا)\s+"
    r"(\d{1,2}|" + "|".join(_COUNT_WORD_VALUES) + r")(?!\w)"
)

# Named people after "أنا و…". Live failure: "أكل أنا وأمي وأبوي" was treated
# like "أنا ومعايا N" and padded to four, then a tray for four was recommended.
_PERSON_NOUNS = frozenset({
    "امي", "ماما", "والدتي",
    "ابوي", "ابويا", "بابا", "والدي", "ابي",
    "اخوي", "اخويا", "اختي",
    "زوجتي", "زوجي", "مرتي",
    "بنتي", "ابني", "ابنتي",
    "جدتي", "جدي",
    "عمي", "عمتي", "خالي", "خالتي",
    "صاحبتي", "صاحبي",
})
_COMPANION_SKIP = frozenset({
    "و", "معايا", "معي", "معاي", "معانا", "كده", "كذا", "كلها", "كلنا",
    "ناكلها", "ناكل", "عايز", "ابي", "ابغى", "حاجه", "اكله", "وجبه",
    "عائليه", "عائلي", "عشان", "اقدر", "اكل", "انا", "كدوت",
})


def _bare_ar_word(word: str) -> str:
    """Strip a leading و so 'وأمي' matches أمي after normalize_ar."""
    if word.startswith("و") and len(word) > 1:
        return word[1:]
    return word


def _extract_companion_list(folded: str) -> tuple[str, Optional[int]]:
    """Count named people after أنا: أنا وأمي وأبوي = 3, not 4."""
    words = [w for w in re.split(r"[^\w؀-ۿ]+", folded) if w]
    if "انا" not in words:
        return folded, None
    start = words.index("انا")
    people: list[str] = []
    end = start + 1
    for j in range(start + 1, len(words)):
        w = words[j]
        bare = _bare_ar_word(w)
        if bare in _PERSON_NOUNS:
            people.append(bare)
            end = j + 1
            continue
        if bare.isdigit() or bare in _COUNT_WORD_VALUES:
            # "أنا ومعايا خمسة" is a different pattern; don't mix the two.
            return folded, None
        if w in _COMPANION_SKIP or bare in _COMPANION_SKIP:
            end = j + 1
            continue
        break
    if not people:
        return folded, None
    total = 1 + len(people)
    if not (2 <= total <= 20):
        return folded, None
    rest = words[:start] + words[end:]
    return " ".join(rest), total


def _extract_headcount(query_norm: str) -> tuple[str, Optional[int]]:
    """Pull "for N people" out of a query.

    The count is returned separately and the phrase removed, because its words
    poison token scoring in both directions: "اشخاص" appears in the name of
    every family meal in the catalog, so leaving it in ranks *all* of them up;
    and the LLM usually drops the phrase entirely, which is how "بيتزا لثلاث
    أشخاص" became a search for bare "بيتزا" and recommended a single-person
    combo to a table of three.
    """
    folded = query_norm.translate(_ARABIC_INDIC_DIGITS)
    match = _QUERY_HEADCOUNT_RE.search(folded)
    if match:
        raw = match.group(1)
        count = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (count if 1 <= count <= 20 else None)
    match = _QUERY_DUAL_RE.search(folded)
    if match:
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), 2
    match = _WITH_ME_COUNT_RE.search(folded)
    if match:
        raw = match.group(1)
        others = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        total = 1 + others
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (total if 2 <= total <= 20 else None)
    return _extract_companion_list(folded)


def _parse_serves(name: str, description: str) -> int:
    """People this meal feeds, or 0 when the catalog does not say.

    0 means "unknown" and must stay distinguishable from 1: the agent is
    allowed to state a headcount only when there is one to state.
    """
    text = f"{name} {description}".translate(_ARABIC_INDIC_DIGITS)
    match = _SERVES_RE.search(text)
    if match:
        count = int(match.group(1))
        return count if 1 <= count <= 20 else 0
    return 1 if _SERVES_ONE_RE.search(text) else 0


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of character bigrams — good enough for short Arabic names."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    aa = {a[i : i + 2] for i in range(len(a) - 1)} or {a}
    bb = {b[i : i + 2] for i in range(len(b) - 1)} or {b}
    return len(aa & bb) / len(aa | bb)


@dataclass(frozen=True)
class Meal:
    """One catalog meal plus the tags derived from its text."""

    id: str
    name: str
    description: str
    price: float
    rating: float
    calories: int
    protein: int
    carbs: int
    fat: int
    category: str
    is_popular: bool
    is_offer: bool
    offer_price: Optional[float]
    offer_kind: str
    is_combo: bool
    combo_includes: tuple[str, ...]
    cashback_percent: float
    serves: int
    spicy_option: bool
    addons: tuple[dict[str, Any], ...]
    restaurant_id: str
    restaurant_name: str
    cuisine: str
    restaurant_tags: tuple[str, ...]
    delivery_time: str
    delivery_fee: float
    min_order: float
    restaurant_rating: float
    protein_tags: frozenset[str] = field(default_factory=frozenset)
    diet_tags: frozenset[str] = field(default_factory=frozenset)
    search_blob: str = ""

    @property
    def effective_price(self) -> float:
        """What the user actually pays — offers have a separate price field."""
        return self.offer_price if (self.is_offer and self.offer_price) else self.price

    @property
    def has_discount(self) -> bool:
        """True only when a *lower* offer price is actually recorded.

        ``is_offer`` on its own means "this row is a bundle" — combo, family
        platter or buy-one-get-one — and in the current catalog no offer row
        carries an ``offerPrice`` at all. Reading ``is_offer`` as "reduced
        price" is how a plain 28-riyal shawarma got announced as a deal, so the
        two questions are kept separate.
        """
        return bool(self.is_offer and self.offer_price and self.offer_price < self.price)

    @property
    def page_path(self) -> str:
        """Frontend route for this meal (app/meals/[id]/page.tsx)."""
        return f"/meals/{self.id}"


def _derive_protein_tags(blob: str, own_blob: Optional[str] = None) -> frozenset[str]:
    """Protein tags for a meal.

    ``blob`` may include a bundle's ``comboIncludes``, because a listed
    شيش طاووق is real evidence of chicken. ``own_blob`` is the dish's own name
    and description, and vegetarian is inferred from that alone: a bundle's
    side of إدامامي or سلطة says nothing about its main course, and
    "عرض رامن مع جيوزا" was tagged vegetarian off exactly that. Claiming a dish
    is meat-free when it is not is the one error here with real consequences.
    """
    tags = {
        tag
        for tag, keywords in _PROTEIN_KEYWORDS.items()
        if _has_keyword(blob, keywords)
    }
    if not tags and _has_keyword(own_blob if own_blob is not None else blob, _VEGETARIAN_KEYWORDS):
        tags.add("vegetarian")
    return frozenset(tags)


def _derive_diet_tags(meal_json: dict[str, Any], cuisine: str, meal_blob: str) -> frozenset[str]:
    tags: set[str] = set()
    calories = int(meal_json.get("calories") or 0)
    protein = int(meal_json.get("protein") or 0)
    fat = int(meal_json.get("fat") or 0)
    # Dessert keys off the name alone. Descriptions mention sweets in passing
    # ("يقدم مع تمر") and would drag savoury plates into the dessert filter.
    name_norm = normalize_ar(meal_json.get("name") or "")

    if 0 < calories <= LIGHT_CALORIES_MAX:
        tags.add("light")
    elif fat >= RICH_FAT_MIN or calories >= RICH_CALORIES_MIN:
        tags.add("rich")
    if protein >= HIGH_PROTEIN_MIN:
        tags.add("high_protein")
    if meal_json.get("isOffer"):
        tags.add("offer")
    kind = str(meal_json.get("offerKind") or "")
    if kind == "combo" or meal_json.get("isCombo"):
        tags.add("combo")
    if kind == "family":
        tags.add("family")
    if meal_json.get("isPopular"):
        tags.add("popular")
    if meal_json.get("spicyOption"):
        tags.add("spicy_available")
    if _has_keyword(meal_blob, FAMILY_KEYWORDS):
        tags.add("family")
    if cuisine in GROCERY_CUISINES:
        tags.add("grocery")
    # Nearly every bundle throws in a drink, and `meal_blob` now includes that
    # list, so the keyword alone would file family platters under "drink" and
    # hide them from a food search.
    is_drink = not meal_json.get("isCombo") and _has_keyword(meal_blob, _DRINK_KEYWORDS)
    if is_drink:
        tags.add("drink")
    # A named sweet is dessert wherever it is sold, and even when it comes with
    # a drink ("حلا مع قهوة"). Anything else in a sweets shop is dessert only
    # if nothing savoury in its name says otherwise, and never if it is itself
    # a drink — "أبغى حلا" should not return a latte.
    if _has_keyword(name_norm, _DESSERT_KEYWORDS) or (
        cuisine in _DESSERT_CUISINES
        and not is_drink
        and not _has_keyword(name_norm, _SAVOURY_KEYWORDS)
    ):
        tags.add("dessert")
    if _has_keyword(meal_blob, CRISPY_KEYWORDS):
        tags.add("crispy")
    carbs = int(meal_json.get("carbs") or 0)
    if (
        0 < carbs <= KETO_CARBS_MAX
        and "dessert" not in tags
        and "drink" not in tags
        and "grocery" not in tags
    ):
        tags.add("keto")
    if float(meal_json.get("cashbackPercent") or 0) > 0:
        tags.add("cashback")
    return frozenset(tags)


def _build_meal(meal_json: dict[str, Any], restaurant: dict[str, Any]) -> Meal:
    cuisine = normalize_ar(restaurant.get("cuisine"))
    name = str(meal_json.get("name") or "")
    description = str(meal_json.get("description") or "")
    restaurant_tags = tuple(str(t) for t in (restaurant.get("tags") or []))

    # Two haystacks, and the split is load-bearing.
    #
    # `meal_blob` is the dish's own words. Classification reads only this,
    # because a restaurant's tags describe the venue, not the plate: "ميلك شيك"
    # at أصيل برجر would inherit برجر and come back as meat, غيوزا at سوشي نوي
    # would come back as seafood, and تكة دجاج at بيت الشواية (tagged لحوم)
    # would come back as meat. Those are false claims about what someone is
    # about to eat — exactly what a vegetarian or an allergy question relies on.
    #
    # `search_blob` is wider, and stays wide: free-text search *should* find
    # "سوشي" by restaurant and "مشاوي" by cuisine.
    # A bundle's protein lives in `comboIncludes`, nowhere else: "وجبة مشاوي
    # فردية" is described only as "طبق رئيسي مع أرز وسلطة ومشروب", and its
    # شيش طاووق is listed in that field. Classifying without it tagged all 357
    # bundles from their side dishes — that grill combo came back `vegetarian`
    # and `drink`, so a search for مشاوي with protein_type=meat dropped the very
    # card the user was pointing at on screen.
    includes = tuple(str(i) for i in (meal_json.get("comboIncludes") or []))
    own_blob = normalize_ar(f"{name} {description}")
    meal_blob = normalize_ar(f"{name} {description} {' '.join(includes)}")
    blob = normalize_ar(
        " ".join(
            [
                name,
                description,
                str(restaurant.get("name") or ""),
                str(restaurant.get("cuisine") or ""),
                " ".join(restaurant_tags),
            ]
        )
    )

    return Meal(
        id=str(meal_json.get("id")),
        name=name,
        description=description,
        price=float(meal_json.get("price") or 0),
        rating=float(meal_json.get("rating") or 0),
        calories=int(meal_json.get("calories") or 0),
        protein=int(meal_json.get("protein") or 0),
        carbs=int(meal_json.get("carbs") or 0),
        fat=int(meal_json.get("fat") or 0),
        category=str(meal_json.get("category") or ""),
        is_popular=bool(meal_json.get("isPopular")),
        is_offer=bool(meal_json.get("isOffer")),
        offer_price=(
            float(meal_json["offerPrice"])
            if meal_json.get("offerPrice") is not None
            else None
        ),
        offer_kind=str(meal_json.get("offerKind") or ""),
        is_combo=bool(meal_json.get("isCombo")),
        combo_includes=includes,
        cashback_percent=float(meal_json.get("cashbackPercent") or 0),
        serves=_parse_serves(name, description),
        spicy_option=bool(meal_json.get("spicyOption")),
        addons=tuple(meal_json.get("addons") or []),
        restaurant_id=str(restaurant.get("id")),
        restaurant_name=str(restaurant.get("name") or ""),
        cuisine=str(restaurant.get("cuisine") or ""),
        restaurant_tags=restaurant_tags,
        delivery_time=str(restaurant.get("deliveryTime") or ""),
        delivery_fee=float(restaurant.get("deliveryFee") or 0),
        min_order=float(restaurant.get("minOrder") or 0),
        restaurant_rating=float(restaurant.get("rating") or 0),
        protein_tags=_derive_protein_tags(meal_blob, own_blob),
        diet_tags=_derive_diet_tags(meal_json, cuisine, meal_blob),
        search_blob=blob,
    )


@dataclass(frozen=True)
class Catalog:
    meals: tuple[Meal, ...]
    restaurants: tuple[dict[str, Any], ...]

    def by_id(self, meal_id: str) -> Optional[Meal]:
        target = normalize_ar(meal_id)
        for meal in self.meals:
            if normalize_ar(meal.id) == target:
                return meal
        return None

    def restaurant_by_id(self, restaurant_id: str) -> Optional[dict[str, Any]]:
        target = normalize_ar(restaurant_id)
        for restaurant in self.restaurants:
            if normalize_ar(restaurant.get("id")) == target:
                return restaurant
        return None


@lru_cache(maxsize=1)
def load_catalog(path: Optional[str] = None) -> Catalog:
    """Load and index the catalog once per process.

    Cached because every tool call would otherwise re-read and re-tag 128KB
    while the user waits mid-sentence.
    """
    catalog_path = Path(path or os.getenv("CATALOG_PATH") or DEFAULT_CATALOG_PATH)
    with catalog_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    restaurants = tuple(raw.get("restaurants") or [])
    meals = tuple(
        _build_meal(meal_json, restaurant)
        for restaurant in restaurants
        for meal_json in (restaurant.get("meals") or [])
    )
    logger.info(
        "Catalog loaded path={} restaurants={} meals={} tagged_meat={} "
        "tagged_chicken={} tagged_seafood={} tagged_vegetarian={}",
        catalog_path,
        len(restaurants),
        len(meals),
        sum(1 for m in meals if "meat" in m.protein_tags),
        sum(1 for m in meals if "chicken" in m.protein_tags),
        sum(1 for m in meals if "seafood" in m.protein_tags),
        sum(1 for m in meals if "vegetarian" in m.protein_tags),
    )
    return Catalog(meals=meals, restaurants=restaurants)


# ─── Search ──────────────────────────────────────────────────────────────────

# Arabic words that carry a filter rather than a dish name. The model is told to
# use the structured arguments, but users say "شي خفيف" and the model often
# forwards that verbatim as the query — honouring it here beats returning junk.
_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "light": ("خفيف", "خفيفه", "دايت", "رجيم", "قليل السعرات", "لايت", "صحي"),
    "rich": (
        "دسم", "دسمه", "دهون", "دهن",
        "ثقيل", "ثقيله", "سمين", "سمينه",
    ),
    "high_protein": (
        "بروتين", "بروتينات", "مشبع",
        "بروتين عالي", "عالي البروتين", "البروتين عالي",
        "هاي بروتين", "high protein",
    ),
    "spicy_available": ("حار", "حاره", "سبايسي", "حريف"),
    "offer": ("عرض", "عروض", "خصم"),
    "cheap": ("ارخص", "رخيص", "رخيصه", "اوفر سعر", "ارخص شي", "اقل سعر"),
    "expensive": ("اغلى", "غالي", "غاليه", "افخم", "اغلى شي", "اعلى سعر"),
    "family": ("عائله", "عائلي", "عائليه", "عزيمه", "ضيوف", "جماعه"),
    "dessert": ("حلا", "حلو", "حلي", "حلويات", "تحليه", "ديزرت", "سكر", "حادق"),
    "drink": ("مشروب", "عصير", "شراب", "بارد"),
    "crispy": ("مقرمش", "كرسبي", "كريسبي", "crispy"),
    "keto": ("كيتو", "keto", "keto diet"),
    "combo": ("كومبو", "كمبو", "combo"),
    "deal": ("ديل", "deal"),
    "cashback": (
        "كاش باك", "كاشباك", "الكاش باك", "كاش", "باك",
        "cashback", "cash back",
    ),
}

# Hints that name a kind of food rather than a property of one. These narrow
# the result set; the rest (light, rich, high_protein, offer, family) only re-rank.
# crispy/keto/combo/deal are tester filters (Bug-002): ranking-only left the UI on "all".
# cashback (Bug-010): "عروض الكاش باك" must not be treated as a missing dish name.
# "عرض" stays on the ranking-only `offer` tag so "عرض شاورما" is not deal-only.
_CATEGORY_HINTS = frozenset({
    "dessert", "drink", "crispy", "keto", "combo", "deal", "cashback",
})

_STOPWORDS = {
    "ابغى", "ابي", "اريد", "وجبه", "اكل", "اكله", "حاجه", "طعام",
    "شي", "شيء", "من", "في", "على", "مع",
    "او", "و", "لي", "لك", "الي", "هذا", "هذي", "بس", "كثير", "مره", "زين",
    "عليا", "عليها", "عليها", "عايز", "عاوز",
}

# Adjectives that ride next to a hint ("بروتين عالي") and must not be treated
# as a dish name. Live failure: "رشحلي أكل بروتين عالي" tokenized to
# ["بروتين", "عالي"], "عالي" matched no meal, search returned query_not_found,
# and سلمى stalled instead of ranking high-protein dishes.
_HINT_MODIFIERS = frozenset({
    "عالي", "عاليه", "منخفض", "منخفضه", "قليل", "قليله", "مرتفع", "مرتفعه",
    "كثير", "كثيره", "مليان", "مليانه", "مديان", "مديانه",
})

# protein_type / cuisine must be grounded in the user's words. Live failure:
# asked for حلا → model passed protein_type=vegetarian and wiped dessert offers.
_PROTEIN_QUERY_CUES: dict[str, tuple[str, ...]] = {
    "meat": (
        "لحم", "لحمه", "لحوم", "مشاوي", "ريش", "كباب", "كفته", "مندي لحم",
        "كبسه لحم", "meat", "beef", "lamb",
    ),
    "chicken": (
        "دجاج", "دجاجه", "فراخ", "تشيكن", "شيكن", "ستربس", "ستريبس",
        "chicken", "shawarma chicken",
    ),
    "seafood": (
        "سمك", "سمكه", "بحري", "روبيان", "جمبري", "سلمون", "تونه",
        "seafood", "fish", "shrimp",
    ),
    "vegetarian": (
        "نباتي", "نباتيه", "بدون لحم", "من غير لحم", "فيجان", "فيجتيريان",
        "vegetarian", "vegan",
    ),
}

# Cuisine is kept only when the query names that kitchen (or a clear alias).
# "حلا" → حلويات is grounded; "سعودي" on a bare "أكل" is a guess.
_CUISINE_QUERY_CUES: dict[str, tuple[str, ...]] = {
    "حلويات": (
        "حلا", "حلو", "حلي", "حلويات", "تحليه", "ديزرت", "كنافه", "بقلاوه",
        "بسبوسه", "شوكولاته", "كيك",
    ),
    "مشاوي": ("مشاوي", "شوايه", "مشوي", "فحم", "ريش", "كباب"),
    "شامي": ("شامي", "شاورما", "فلافل", "حمص", "مناقيش", "فتوش"),
    "سعودي": ("سعودي", "كبسه", "مندي", "مظبي", "سليق", "جريش", "هريس", "مرقوق"),
    "بحري": ("بحري", "سمك", "روبيان", "صياديه"),
    "برجر": ("برجر", "برغر", "burger"),
    "ياباني": ("ياباني", "سوشي", "رامن", "تمبورا", "ساشيمي"),
    "ايطالي": ("ايطالي", "بيتزا", "باستا", "لازانيا"),
    "هندي": ("هندي", "برياني", "تكا", "ماسالا", "كاري"),
    "صحي": ("صحي", "دايت", "لايت", "رجيم"),
    "فرايد تشيكن": ("فرايد", "ستربس", "ستريبس", "باكت", "فرايد تشيكن"),
}

# Words that are not a kitchen — if the query is only these, a cuisine
# argument is the user's request (live: query='' cuisine=حلويات was dropped).
_GENERIC_QUERY_WORDS = frozenset({
    "عرض", "عروض", "اكل", "اكله", "وجبه", "شي", "حاجه", "طعام",
})


def _query_words(query_norm: str) -> set[str]:
    words = {w for w in re.split(r"[^\w؀-ۿ]+", query_norm or "") if w}
    for word in list(words):
        stripped = re.sub(r"^[وف]?[بلك]?(?:ال)?", "", word)
        if len(stripped) >= 2:
            words.add(stripped)
    return words


def _cue_in_query(query_norm: str, cues: Iterable[str]) -> bool:
    if not query_norm:
        return False
    words = _query_words(query_norm)
    for cue in cues:
        cue_n = normalize_ar(cue)
        if not cue_n:
            continue
        if " " in cue_n:
            if cue_n in query_norm:
                return True
        elif cue_n in words or cue_n in query_norm:
            return True
    return False


def protein_explicit_in_query(query: str, protein_type: str) -> bool:
    """True only when the user words justify this protein_type filter."""
    protein = (protein_type or "").strip().lower()
    cues = _PROTEIN_QUERY_CUES.get(protein)
    if not cues:
        return False
    return _cue_in_query(normalize_ar(query), cues)


def cuisine_explicit_in_query(query: str, cuisine: str) -> bool:
    """True only when the user words justify this cuisine filter."""
    cuisine_n = normalize_ar(cuisine)
    query_n = normalize_ar(query)
    if not cuisine_n:
        return False
    if cuisine_n in query_n:
        return True
    cues = _CUISINE_QUERY_CUES.get(cuisine_n)
    if cues and _cue_in_query(query_n, cues):
        return True
    # Live 2026-08-17: "عروض حلويات لتلات أشخاص" arrived as query='' +
    # cuisine=حلويات. Treating that as a guess dropped the kitchen and
    # returned pizza family meals, then she said there were no desserts.
    if not query_n:
        return True
    words = _query_words(query_n)
    if words and words <= _GENERIC_QUERY_WORDS:
        return True
    return False


_MEAL_QUERY_FOOD = (
    "شاورما", "برجر", "بيتزا", "كبسه", "مندي", "مشاوي", "حلا", "كومبو",
    "وجبه", "عرض", "دجاج", "لحم", "سمك", "فلافل", "بروتين", "كيتو",
    "مقرمش", "عائلي",
)


def looks_like_meal_query(text: str) -> bool:
    """True when the utterance is a dish/offer search, not a restaurant name.

    Live 2026-08-16: "عرض شاورما لحمة لـ 5 أفراد" was passed to
    show_restaurant, which failed, and she said "ما عندنا مطعم بهذا الاسم".
    """
    n = normalize_ar(text)
    if not n:
        return False
    if n.lower() in {"none", "null", "nil"}:
        return False
    has_people = bool(
        _QUERY_HEADCOUNT_RE.search(n)
        or _QUERY_DUAL_RE.search(n)
        or "افراد" in n
        or "اشخاص" in n
    )
    food = _cue_in_query(n, _MEAL_QUERY_FOOD)
    return has_people or food


def resolve_restaurant(value: Optional[str], catalog: Optional[Catalog] = None) -> Optional[str]:
    """Resolve an Arabic name or a (possibly mangled) id to a real restaurant id.

    This exists because of a live failure. The model was given a restaurant
    filter but never given any real ids, so it invented them from the names it
    could see: ``aseel-burger-id`` for ``aseel-burger``, ``mazaq_alsham_id`` for
    ``madhaq-alsham``. The filter then matched nothing, search returned zero,
    and سلمى told the user there was a burger it could not open — the search had
    silently excluded every meal.

    So: accept what the model actually has (the Arabic name), tolerate the id
    shapes it invents, and return ``None`` rather than a wrong match. The caller
    drops an unresolvable filter instead of returning an empty result.
    """
    if not value:
        return None
    cat = catalog or load_catalog()

    raw = normalize_ar(repair_asr_query(value) or value).replace("_", "-").strip()
    trimmed = raw[:-3].rstrip("-") if raw.endswith("-id") else raw

    for candidate in (raw, trimmed):
        for restaurant in cat.restaurants:
            if normalize_ar(restaurant.get("id")) == candidate:
                return str(restaurant["id"])

    # Arabic name, which is what the prompt's index actually shows the model.
    # Exact equality checks every restaurant before any substring match gets a
    # say: names share prefixes ("ماركت لقمة" / "ماركت لقمة السريع"), and a
    # substring hit on an earlier row silently opened the wrong shop.
    for restaurant in cat.restaurants:
        if normalize_ar(restaurant.get("name")) == raw:
            return str(restaurant["id"])
    for restaurant in cat.restaurants:
        name = normalize_ar(restaurant.get("name"))
        if name and (raw in name or name in raw):
            return str(restaurant["id"])

    # Id with extra or missing pieces: aseel-burger-id, aseel_burger, burger.
    for restaurant in cat.restaurants:
        rid = normalize_ar(restaurant.get("id"))
        if rid and (trimmed.startswith(rid) or rid.startswith(trimmed)):
            return str(restaurant["id"])

    # Last resort: token overlap, which catches a misspelt transliteration
    # (mazaq-alsham vs madhaq-alsham share "alsham").
    tokens = {t for t in re.split(r"[-\s]+", trimmed) if len(t) > 2 and t != "id"}
    best, best_score = None, 0
    for restaurant in cat.restaurants:
        other = set(re.split(r"[-\s]+", normalize_ar(restaurant.get("id"))))
        other |= set(normalize_ar(restaurant.get("name")).split())
        score = len(tokens & other)
        if score > best_score:
            best, best_score = str(restaurant["id"]), score
    return best


@dataclass
class SearchFilters:
    """Normalized view of what the caller asked for, used for the `why` text."""

    query: str = ""
    cuisine: Optional[str] = None
    protein_type: Optional[str] = None
    max_calories: Optional[int] = None
    min_protein: Optional[int] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    spicy: Optional[bool] = None
    only_offers: bool = False
    restaurant_id: Optional[str] = None
    # Soft preference, not a filter: the restaurant whose page is open. A hard
    # filter here would answer "ما عندنا بيتزا" to someone standing in a grill
    # house, so this only reorders.
    prefer_restaurant_id: Optional[str] = None
    include_grocery: bool = False
    implied: frozenset[str] = frozenset()
    offer_kinds: Optional[frozenset[str]] = None

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.query,
                self.cuisine,
                self.protein_type,
                self.max_calories,
                self.min_protein,
                self.max_price,
                self.min_price,
                self.spicy is not None,
                self.only_offers,
                self.restaurant_id,
                self.offer_kinds,
            ]
        )


def _implied_tags(query_norm: str) -> frozenset[str]:
    """Which hint categories the query asks for.

    Single-word hints match whole words only. Substring matching put "حليب"
    (milk) under dessert because "حلي" sits inside it, and dessert is a filter —
    a milk search came back as كنافة. Multi-word hints keep substring matching
    since they cannot collide by accident.
    """
    words = {w for w in re.split(r"[^\w؀-ۿ]+", query_norm) if w}
    for word in list(words):
        stripped = re.sub(r"^[وف]?[بلك]?(?:ال)?", "", word)
        if len(stripped) >= 2:
            words.add(stripped)

    tags = set()
    for tag, hints in _QUERY_HINTS.items():
        for hint in hints:
            if (" " in hint and hint in query_norm) or hint in words:
                tags.add(tag)
                break
    return frozenset(tags)


@lru_cache(maxsize=1)
def _hint_words() -> frozenset[str]:
    return frozenset(w for words in _QUERY_HINTS.values() for w in words)


def _tokens(query_norm: str) -> list[str]:
    """Words in the query that could name a dish, stopwords removed."""
    return [
        t for t in re.split(r"[^\w؀-ۿ]+", query_norm)
        if len(t) > 1 and t not in _STOPWORDS
    ]


def _score(
    meal: Meal,
    tokens: Iterable[str],
    implied: frozenset[str],
    prefer_restaurant_id: Optional[str] = None,
    serves_wanted: Optional[int] = None,
) -> float:
    score = 0.0
    if prefer_restaurant_id and normalize_ar(meal.restaurant_id) == normalize_ar(
        prefer_restaurant_id
    ):
        score += OPEN_RESTAURANT_BONUS
    if serves_wanted:
        # A stated headcount outranks everything except an exact name match.
        # Live failure: "أنا وأمي وأبوي" searched with serves=3 still ranked
        # a popular tray for 4 first, because overshoot cost 0.05 while
        # is_popular is +0.4. Exact match must beat a famous larger tray.
        if meal.serves == serves_wanted:
            score += 8.0
        elif meal.serves > serves_wanted:
            overshoot = meal.serves - serves_wanted
            score += max(0.0, 2.5 - 1.5 * overshoot)
        elif meal.serves:
            score -= 3.0
    name_norm = normalize_ar(meal.name)
    desc_norm = normalize_ar(meal.description)
    cuisine_norm = normalize_ar(meal.cuisine)
    restaurant_norm = normalize_ar(meal.restaurant_name)

    for token in tokens:
        # Word-boundary matching, not substring: "حلى" normalizes to "حلي",
        # which sits inside "الحليب" and "ساحلية" — a dessert request came back
        # as milkshake, سليق and بطاطس ساحلية. Same failure as ريش in جريش.
        if _has_keyword(name_norm, (token,)):
            score += 3.0
        elif _has_keyword(cuisine_norm, (token,)) or _has_keyword(restaurant_norm, (token,)):
            score += 2.0
        elif _has_keyword(desc_norm, (token,)):
            score += 1.0
        elif _has_keyword(meal.search_blob, (token,)):
            score += 0.5

    # Implied tags are a soft preference, not a filter: "شي خفيف" should rank
    # light meals first without hiding a 420-calorie dish that also fits.
    for tag in implied:
        if tag in meal.diet_tags:
            score += 1.5
    if "cashback" in implied and meal.cashback_percent:
        score += meal.cashback_percent / 4.0

    if meal.is_popular:
        score += 0.4
    score += meal.rating / 10.0
    return score


def _name_matches(meals: Iterable[Meal], tokens: Iterable[str]) -> list[Meal]:
    """Meals whose own name carries every query token.

    Deliberately stricter than ``_score``, which also rewards a hit anywhere in
    the restaurant name or cuisine. "Did the user name this dish?" has to be
    answerable without the venue's words counting as the dish's.
    """
    tokens = list(tokens)
    if not tokens:
        return []
    return [
        m
        for m in meals
        if all(_has_keyword(normalize_ar(m.name), (t,)) for t in tokens)
    ]


def _matches(meal: Meal, f: SearchFilters) -> bool:
    if not f.include_grocery and "grocery" in meal.diet_tags:
        return False
    if f.restaurant_id and normalize_ar(meal.restaurant_id) != normalize_ar(f.restaurant_id):
        return False
    if f.cuisine and normalize_ar(f.cuisine) not in normalize_ar(meal.cuisine):
        return False
    if f.protein_type and f.protein_type not in meal.protein_tags:
        return False
    if f.max_calories is not None and not (0 < meal.calories <= f.max_calories):
        return False
    if f.min_protein is not None and meal.protein < f.min_protein:
        return False
    if f.max_price is not None and meal.effective_price > f.max_price:
        return False
    if f.min_price is not None and meal.effective_price < f.min_price:
        return False
    if f.spicy is True and not meal.spicy_option:
        return False
    if f.only_offers and not meal.is_offer:
        return False
    if f.offer_kinds:
        kind = (meal.offer_kind or "").strip() or ("combo" if meal.is_combo else "")
        if kind not in f.offer_kinds:
            return False
    # A category word is a filter, not a preference: asking for حلا and being
    # offered a burger ranked second is not a worse answer, it is a wrong one.
    for category in _CATEGORY_HINTS & f.implied:
        if category not in meal.diet_tags:
            return False
    return True


_PROTEIN_LABEL_AR = {
    "meat": "لحم",
    "chicken": "دجاج",
    "seafood": "بحري",
    "vegetarian": "نباتي",
}


def _why(meal: Meal, f: SearchFilters) -> list[str]:
    """Grounded reasons for this pick — numbers only, no adjectives invented.

    These exist so سلمى explains a choice from catalog facts instead of making
    up a rationale. Each entry is a short Arabic phrase the model can speak.
    """
    reasons: list[str] = []
    wants_light = f.max_calories is not None or "light" in f.implied
    wants_rich = "rich" in f.implied
    wants_protein = f.min_protein is not None or "high_protein" in f.implied

    if wants_light and meal.calories:
        reasons.append(f"سعراته {meal.calories} فقط")
    if wants_rich and meal.fat:
        reasons.append(f"دهونه {meal.fat} جرام")
    elif wants_rich and meal.calories:
        reasons.append(f"سعراته {meal.calories}")
    if wants_protein and meal.protein:
        reasons.append(f"بروتينه {meal.protein} جرام")
    if f.protein_type:
        reasons.append(_PROTEIN_LABEL_AR.get(f.protein_type, f.protein_type))
    if f.max_price is not None or "offer" in f.implied:
        reasons.append(f"سعره {meal.effective_price:.0f} ريال")
    if meal.is_offer:
        reasons.append("عليه عرض")
    if f.spicy and meal.spicy_option:
        reasons.append("يتوفر حار")
    if "cashback" in f.implied and meal.cashback_percent:
        reasons.append(f"كاش باك {int(meal.cashback_percent)} بالمية")

    # Fall back to the meal's own strengths when no filter drove the pick.
    if not reasons:
        if meal.is_popular:
            reasons.append("من الأكثر طلبا")
        if meal.rating >= 4.7:
            reasons.append(f"تقييمه {meal.rating}")
        if meal.calories:
            reasons.append(f"سعراته {meal.calories}")
    return reasons[:3]


# A question is only worth asking when the result set is genuinely wide. This
# was tuned up from "more matches than we showed": at that setting six matches
# counted as ambiguous, the agent asked a narrowing question on every single
# turn, and it never got as far as recommending anything. Silence here is what
# makes it commit to a pick.
#
# Raised when the catalog grew from 110 meals to ~1550, where "تشيز كيك" alone
# matches 46 rows across a dozen restaurants.
#
# The count on its own cannot separate a named dish from a vague craving —
# measured on the current catalog, "برجر" and "شي حلو" both return 221. So the
# real gate is `named_dish` below, and this only bounds the vague case.
AMBIGUITY_MIN_MATCHES = 40

# How much a meal from the restaurant the user is currently looking at outranks
# an identical meal elsewhere. Big enough to win, small enough that it never
# hides a better match when the user has moved on to a different craving.
OPEN_RESTAURANT_BONUS = 6.0


def _next_question(
    results: list[Meal],
    f: SearchFilters,
    total: int,
    shown: int,
    named_dish: bool = False,
) -> Optional[dict[str, str]]:
    """Which single unset filter would cut the result set down the most.

    Returned as a hint, not a script: the point is that سلمى asks a question
    because the answer is genuinely ambiguous, not because a checklist says the
    third question comes now. ``None`` means just recommend.

    A ``named_dish`` query is never ambiguous however many rows it matches. The
    user who says "تشيز كيك" has already answered the only question worth
    asking; the 46 matches are the same dessert in 46 kitchens, and ranking
    (open restaurant first, then rating) picks among them without a question.
    """
    if named_dish:
        return None
    if total <= shown or total < AMBIGUITY_MIN_MATCHES:
        return None

    if f.protein_type is None:
        kinds = {t for m in results for t in m.protein_tags if t in _PROTEIN_LABEL_AR}
        if len(kinds) >= 2:
            return {"ask_about": "protein_type"}
    if f.max_price is None:
        prices = [m.effective_price for m in results]
        if prices and (max(prices) - min(prices)) >= 30:
            return {"ask_about": "max_price"}
    if f.cuisine is None:
        cuisines = {m.cuisine for m in results}
        if len(cuisines) >= 3:
            return {"ask_about": "cuisine"}
    if f.max_calories is None and f.min_protein is None:
        cals = [m.calories for m in results if m.calories]
        if cals and (max(cals) - min(cals)) >= 300:
            return {"ask_about": "max_calories"}
    return None


_ONES = (
    "صفر", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية",
    "تسعة", "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر",
)
_TENS = ("", "", "عشرين", "ثلاثين", "أربعين", "خمسين", "ستين", "سبعين", "ثمانين", "تسعين")
_HUNDREDS = (
    "", "مئة", "مئتين", "ثلاث مئة", "أربع مئة", "خمس مئة", "ست مئة", "سبع مئة",
    "ثمان مئة", "تسع مئة",
)


def number_to_arabic_words(value: float) -> str:
    """Spell a number the way it is said aloud in Saudi speech.

    This exists because the model gets it wrong. Measured in the conversation
    harness: a 32 ريال dish was quoted as "ثلاثة وثلاثين", and a 46 ريال cart
    total was read as "ثلاثة وأربعين". Digits are the one thing a voice agent
    cannot let the LLM improvise — a wrong price is a wrong order. The tools
    hand it the finished words and the prompt tells it to read them verbatim.
    """
    n = int(round(float(value)))
    if n < 0:
        return f"سالب {number_to_arabic_words(-n)}"
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] if unit == 0 else f"{_ONES[unit]} و{_TENS[tens]}"
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = _HUNDREDS[hundreds]
        return head if rest == 0 else f"{head} و{number_to_arabic_words(rest)}"
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        if thousands == 1:
            head = "ألف"
        elif thousands == 2:
            head = "ألفين"
        elif thousands < 11:
            # 3–10: ثلاثة آلاف … عشرة آلاف
            head = f"{_ONES[thousands]} آلاف"
        else:
            # Live crash 2026-08-16: 560×88=49280 → thousands=49 and
            # _ONES[49] IndexError, so add_to_cart/get_cart died mid-turn.
            head = f"{number_to_arabic_words(thousands)} ألف"
        return head if rest == 0 else f"{head} و{number_to_arabic_words(rest)}"
    millions, rest = divmod(n, 1_000_000)
    if millions == 1:
        head = "مليون"
    elif millions == 2:
        head = "مليونين"
    else:
        head = f"{number_to_arabic_words(millions)} مليون"
    return head if rest == 0 else f"{head} و{number_to_arabic_words(rest)}"


def sar_words(value: float) -> str:
    return f"{number_to_arabic_words(value)} ريال"


def _price_speech(meal: Meal) -> dict[str, Any]:
    """Spoken price fields. The model converts 71 to 'واحد وستين' on its own."""
    spoken = sar_words(meal.effective_price)
    return {
        "price_sar": round(meal.effective_price, 2),
        "price_say_ar": spoken,
        "price_sentence_ar": f"السعر {spoken}",
    }


def cart_nutrition(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum catalog macros for cart lines so the model never adds them itself.

    Live failure: the user asked for the cart calorie total, the model added
    the per-meal numbers in its head, and spoke the wrong sum. Tools must hand
    it the finished total and the spoken form, same as price.
    """
    calories = 0
    protein = 0
    carbs = 0
    fat = 0
    missing = 0
    for line in lines:
        qty = int(line.get("quantity") or 1)
        cal = int(line.get("calories") or 0)
        if cal <= 0:
            missing += 1
            continue
        calories += cal * qty
        protein += int(line.get("protein_g") or 0) * qty
        carbs += int(line.get("carbs_g") or 0) * qty
        fat += int(line.get("fat_g") or 0) * qty
    result: dict[str, Any] = {
        "calories_total": calories,
        "calories_total_say_ar": number_to_arabic_words(calories),
        "protein_g_total": protein,
        "protein_total_say_ar": number_to_arabic_words(protein),
        "carbs_g_total": carbs,
        "carbs_total_say_ar": number_to_arabic_words(carbs),
        "fat_g_total": fat,
        "fat_total_say_ar": number_to_arabic_words(fat),
        "nutrition_incomplete": missing > 0,
    }
    if calories:
        result["nutrition_say_ar"] = (
            f"المجموع {result['calories_total_say_ar']} سعرة، "
            f"والبروتين {result['protein_total_say_ar']} جرام"
        )
        if missing:
            result["nutrition_say_ar"] += "، والحسبة للأصناف اللي سعراتها مسجلة فقط"
    else:
        result["nutrition_say_ar"] = "ما عندي مجموع سعرات مسجّل للسلة الحين"
    return result


_OFFER_KIND_AR = {
    "combo": "وجبة كاملة (طبق مع مقاديمه)",
    "family": "وجبة عائلية",
    "deal": "عرض: كمية أكبر بسعر واحد",
}


def _offer_facts(meal: Meal) -> dict[str, Any]:
    """Exactly what kind of offer this is, and what it contains.

    ``is_offer`` alone was the only offer signal the model ever saw, and it read
    it as "discounted". It then announced a plain shawarma as a deal, invented a
    percentage, and described bundle contents it had never been told
    ("شاورما دجاج مكتارة"). Both halves of that are answered here: the kind of
    bundle, and the itemised list from ``comboIncludes``.
    """
    facts: dict[str, Any] = {
        "is_offer": meal.is_offer,
        "has_discount": meal.has_discount,
    }
    if meal.is_offer:
        facts["offer_kind"] = meal.offer_kind or "deal"
        facts["offer_kind_ar"] = _OFFER_KIND_AR.get(meal.offer_kind, "عرض")
    if meal.combo_includes:
        facts["includes"] = list(meal.combo_includes)
        facts["includes_say_ar"] = [speakable_name(i) for i in meal.combo_includes]
    if meal.cashback_percent:
        facts["cashback_percent"] = meal.cashback_percent
    return facts


def _serves_facts(meal: Meal) -> dict[str, Any]:
    """Headcount, and an explicit "unknown" when the catalog does not say."""
    if meal.serves:
        return {
            "serves_people": meal.serves,
            "serves_say_ar": number_to_arabic_words(meal.serves),
        }
    return {"serves_people": None}


def meal_summary(meal: Meal, f: Optional[SearchFilters] = None) -> dict[str, Any]:
    """Compact result row. Deliberately small — this goes into an LLM turn."""
    row: dict[str, Any] = {
        "meal_id": meal.id,
        "name": meal.name,
        # Spoken forms are canonical TTS spellings; read them verbatim like
        # price_say_ar so the model does not invent vowels for food names.
        "name_say_ar": speakable_name(meal.name),
        "restaurant": meal.restaurant_name,
        "restaurant_say_ar": speakable_name(meal.restaurant_name),
        "restaurant_id": meal.restaurant_id,
        "cuisine": meal.cuisine,
        **_price_speech(meal),
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        # Carbs and fat ride along even though the row is meant to stay small.
        # Without them the model answered "ما عندي المعلومة" for a macro
        # question and then invented grams on the next turn, because it had
        # already searched and would not call get_meal_details again.
        "protein_g": meal.protein,
        "protein_say_ar": number_to_arabic_words(meal.protein),
        "carbs_g": meal.carbs,
        "carbs_say_ar": number_to_arabic_words(meal.carbs),
        "fat_g": meal.fat,
        "fat_say_ar": number_to_arabic_words(meal.fat),
        "rating": meal.rating,
        "spicy_available": meal.spicy_option,
        "kind": sorted(meal.protein_tags) or ["unspecified"],
        **_offer_facts(meal),
        **_serves_facts(meal),
    }
    if f is not None:
        row["why"] = _why(meal, f)
    return row


def search_meals(
    *,
    query: str = "",
    cuisine: Optional[str] = None,
    protein_type: Optional[str] = None,
    max_calories: Optional[int] = None,
    min_protein: Optional[int] = None,
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    spicy: Optional[bool] = None,
    only_offers: bool = False,
    restaurant_id: Optional[str] = None,
    prefer_restaurant_id: Optional[str] = None,
    serves: Optional[int] = None,
    include_grocery: bool = False,
    offer_kind: Optional[str] = None,
    cashback: Optional[bool] = None,
    sort: str = "relevance",
    limit: int = 3,
    catalog: Optional[Catalog] = None,
) -> dict[str, Any]:
    """Filter + rank meals. Returns a small dict shaped for a voice turn.

    Every filter is optional on purpose. Searching with one filter and reporting
    ``total_matches`` is what lets the agent ask a question *because* the answer
    is ambiguous, instead of interrogating the user before its first search.

    ``prefer_restaurant_id`` ranks the open restaurant's menu first without
    excluding anything, so "ابغى بيتزا" said inside a grill house still finds
    pizza somewhere else.
    """
    cat = catalog or load_catalog()
    # ASR repair first: "ما نعيش زعتر" → "مناقيش زعتر" so token scoring hits
    # the real dish instead of falling through to query_not_found.
    repaired = repair_asr_query(query)
    query_norm = repaired or normalize_ar(query)
    # "for N people" comes out of the text and into a ranking signal, whether
    # the caller passed `serves` explicitly or buried it in the query.
    query_norm, query_serves = _extract_headcount(query_norm)
    serves_wanted = serves or query_serves
    implied = _implied_tags(query_norm)
    if cashback:
        implied = frozenset(set(implied) | {"cashback"})
    # "بروتين عالي" is a ranking+filter request, not a dish name. Honour it even
    # when the model forgot to pass min_protein / sort.
    if "high_protein" in implied:
        if min_protein is None:
            min_protein = HIGH_PROTEIN_MIN
        if (sort or "relevance") == "relevance":
            sort = "protein_desc"
    # "حاجة دسمة" is a vibe, not a dish named دسمة. Rank fatty/high-cal meals
    # instead of returning query_not_found.
    if "rich" in implied and (sort or "relevance") == "relevance":
        sort = "fat_desc"
    # "أرخص" / "أغلى" must re-rank by price even when the model keeps
    # sort=relevance and over-tightens max_price.
    if "cheap" in implied and (sort or "relevance") == "relevance":
        sort = "price_asc"
    if "expensive" in implied and (sort or "relevance") == "relevance":
        sort = "price_desc"

    offer_kinds: Optional[frozenset[str]] = None
    raw_kinds = [
        k.strip().lower()
        for k in re.split(r"[,+|/\s]+", str(offer_kind or ""))
        if k.strip()
    ]
    valid_kinds = {"combo", "family", "deal"}
    parsed_kinds = {k for k in raw_kinds if k in valid_kinds}
    if "combo" in implied:
        parsed_kinds.add("combo")
    if "family" in implied:
        parsed_kinds.add("family")
    if "deal" in implied:
        parsed_kinds.add("deal")
    if parsed_kinds:
        offer_kinds = frozenset(parsed_kinds)
        only_offers = True

    # Drop model-invented filters. Live: حلا → protein_type=vegetarian wiped
    # every dessert offer. Cuisine/protein stay only when the query supports them.
    dropped_unguessed: list[str] = []
    if protein_type and not protein_explicit_in_query(query_norm, protein_type):
        dropped_unguessed.append("protein_type")
        protein_type = None
    if cuisine and not cuisine_explicit_in_query(query_norm, cuisine):
        dropped_unguessed.append("cuisine")
        cuisine = None
    if cuisine:
        implied = frozenset(set(implied) | _implied_tags(normalize_ar(cuisine)))

    # An unrecognised restaurant must never silently empty the result set — that
    # is exactly how "there is a burger but I cannot open it" happened. Drop the
    # filter, search everything else, and report what was ignored.
    ignored_restaurant: Optional[str] = None
    if restaurant_id:
        resolved = resolve_restaurant(restaurant_id, cat)
        if resolved is None:
            ignored_restaurant = restaurant_id
        restaurant_id = resolved

    prefer_restaurant = (
        resolve_restaurant(prefer_restaurant_id, cat) if prefer_restaurant_id else None
    )

    filters = SearchFilters(
        query=query_norm,
        cuisine=cuisine or None,
        protein_type=(protein_type or None),
        max_calories=max_calories,
        min_protein=min_protein,
        max_price=max_price,
        min_price=min_price,
        spicy=spicy,
        only_offers=bool(only_offers),
        restaurant_id=restaurant_id or None,
        prefer_restaurant_id=prefer_restaurant,
        include_grocery=bool(include_grocery),
        implied=implied,
        offer_kinds=offer_kinds,
    )

    matched = [m for m in cat.meals if _matches(m, filters)]
    price_ceiling_hit = False
    price_floor_hit = False
    dropped_guessed_filters_early: list[str] = list(dropped_unguessed)

    # Live 2026-08-16: desserts/offers often have empty protein_tags, so a
    # guessed protein_type=vegetarian wiped every حلويات offer. Only relax
    # protein for offer/dessert browses — never for a hard "seafood under 100".
    if (
        not matched
        and filters.protein_type
        and (
            filters.only_offers
            or "dessert" in filters.implied
            or normalize_ar(filters.cuisine or "") in {"حلويات", "حلا", "dessert"}
        )
    ):
        relaxed = replace(filters, protein_type=None)
        rescued = [m for m in cat.meals if _matches(m, relaxed)]
        if rescued:
            dropped_guessed_filters_early.append("protein_type")
            filters = relaxed
            matched = rescued

    if (
        not matched
        and filters.max_price is not None
        and (
            "cheap" in implied
            or (sort or "") == "price_asc"
            or "ارخص" in (query_norm or "")
            or "رخيص" in (query_norm or "")
        )
    ):
        # "أرخص من ٢٢" when every dessert is ٢٢ → empty. Show the cheapest in
        # the same category and tell the model nothing cheaper exists.
        relaxed = replace(filters, max_price=None)
        rescued = [m for m in cat.meals if _matches(m, relaxed)]
        if rescued:
            price_ceiling_hit = True
            filters = relaxed
            matched = rescued
            if (sort or "relevance") == "relevance":
                sort = "price_asc"

    if (
        not matched
        and filters.min_price is not None
        and (
            "expensive" in implied
            or (sort or "") == "price_desc"
            or "اغلى" in (query_norm or "")
            or "غالي" in (query_norm or "")
        )
    ):
        relaxed = replace(filters, min_price=None)
        rescued = [m for m in cat.meals if _matches(m, relaxed)]
        if rescued:
            price_floor_hit = True
            filters = relaxed
            matched = rescued
            if (sort or "relevance") == "relevance":
                sort = "price_desc"

    if not matched and filters.only_offers and (filters.cuisine or filters.protein_type):
        # Offer browse must never die because cuisine/protein were carried over
        # from the previous turn (sweets → family offers for 3).
        had = [
            label
            for label, value in (
                ("cuisine", filters.cuisine),
                ("protein_type", filters.protein_type),
            )
            if value
        ]
        relaxed = replace(filters, cuisine=None, protein_type=None)
        rescued = [m for m in cat.meals if _matches(m, relaxed)]
        if rescued:
            dropped_guessed_filters_early = list(
                dict.fromkeys(dropped_guessed_filters_early + had)
            )
            filters = relaxed
            matched = rescued

    raw_tokens = _tokens(query_norm)
    # Hint words and their modifiers are filters, not dish names. Leaving
    # "عالي" in the token list made "بروتين عالي" look like a missing dish.
    tokens = [
        t for t in raw_tokens
        if t not in _hint_words() and t not in _HINT_MODIFIERS
    ]
    hint_only = bool(implied) and not tokens

    # The model guesses `cuisine` and `protein_type` from the wording, and a bad
    # guess must not delete the dish the user just named. Live failure: asked to
    # open "وجبة مشاوي فردية" — a card visible on screen — the model searched
    # with protein_type=meat, the combo is chicken, and the exact name match was
    # filtered out; the next-best row ("اشتري كباب واحصل على الثاني مجاناً") was
    # read out as if it were the requested meal. The user's words outrank the
    # model's inference about their category.
    dropped_guessed_filters: list[str] = list(dropped_guessed_filters_early)
    if tokens and not _name_matches(matched, tokens):
        relaxed = replace(
            filters, cuisine=None, protein_type=None, implied=filters.implied - _CATEGORY_HINTS
        )
        rescued = _name_matches([m for m in cat.meals if _matches(m, relaxed)], tokens)
        if rescued:
            dropped_guessed_filters = list(
                dict.fromkeys(
                    dropped_guessed_filters
                    + [
                        label
                        for label, value in (
                            ("cuisine", filters.cuisine),
                            ("protein_type", filters.protein_type),
                        )
                        if value
                    ]
                    or ["category"]
                )
            )
            filters = relaxed
            matched = [m for m in cat.meals if _matches(m, relaxed)]
    unmatched_query = ""
    # Did the user name a dish, or only gesture at a category? Drives whether a
    # narrowing question is worth asking at all — see _next_question.
    named_dish = False
    if tokens:
        scored = [
            (m, _score(m, tokens, implied, prefer_restaurant, serves_wanted))
            for m in matched
        ]
        # The open-restaurant bonus must not make an unrelated dish look like a
        # text match, so the "did the query hit anything" test ignores it.
        hits = [
            (m, s)
            for m, s in scored
            if _score(m, tokens, implied) >= 1.0
        ]
        if hits:
            scored = hits
            named_dish = True
        elif all(t in _hint_words() for t in tokens):
            # Every word was a category hint ("تحلية", "شي حلو"): the user named
            # a kind of food, not a dish. Treating that as "not found" reported
            # "ما عندنا تحلية" with ten desserts in the catalog. The category
            # filter in _matches has already narrowed the set — just rank it.
            scored = [
                (m, _score(m, [], implied, prefer_restaurant, serves_wanted))
                for m in matched
            ]
        else:
            # The query named something the catalog does not have. Falling back
            # to "everything, ranked by popularity" here is what turned a
            # request for ستربس into a burger recommendation: 100 matches came
            # back and the agent read the first one out as the answer.
            #
            # A term that matched nothing has to be reported as nothing. The
            # stopword-only case ("ابغى شي خفيف") is different and still falls
            # through to the branch below, because `_tokens` drops those words
            # and leaves this list empty.
            #
            # Exception: when the user is already inside one restaurant, ASR
            # typos of a dish name on that menu ("ملاقيش") should fuzzy-match
            # the nearest meal rather than invent "ما عندنا" with the item on
            # screen. Global fuzzy is still banned — that is how wrong dishes
            # get relabeled with the user's word.
            fuzzy_hits: list[tuple[Meal, float]] = []
            if filters.restaurant_id:
                needle = " ".join(tokens)
                for meal in matched:
                    name_n = normalize_ar(meal.name)
                    overlap = max(
                        _token_overlap(needle, name_n),
                        max((_token_overlap(t, name_n) for t in tokens), default=0.0),
                    )
                    if overlap >= 0.55:
                        fuzzy_hits.append((meal, 2.0 + overlap))
            if fuzzy_hits:
                scored = fuzzy_hits
                unmatched_query = ""
                named_dish = True
            else:
                unmatched_query = query.strip()
                scored = []
    else:
        scored = [
            (m, _score(m, [], implied, prefer_restaurant, serves_wanted))
            for m in matched
        ]

    matched = [m for m, _ in scored]
    limit = max(1, min(int(limit or 3), 5))

    sort_keys = {
        "relevance": lambda pair: (-pair[1], pair[0].id),
        "calories_asc": lambda pair: (pair[0].calories or 10**6, pair[0].id),
        "price_asc": lambda pair: (pair[0].effective_price, pair[0].id),
        "price_desc": lambda pair: (-pair[0].effective_price, pair[0].id),
        "rating_desc": lambda pair: (-pair[0].rating, pair[0].id),
        "protein_desc": lambda pair: (-pair[0].protein, pair[0].id),
        "fat_desc": lambda pair: (-pair[0].fat, pair[0].id),
        "calories_desc": lambda pair: (-(pair[0].calories or 0), pair[0].id),
    }
    scored.sort(key=sort_keys.get(sort, sort_keys["relevance"]))

    top = [m for m, _ in scored[:limit]]
    total = len(matched)

    result: dict[str, Any] = {
        "total_matches": total,
        "showing": len(top),
        "results": [meal_summary(m, filters) for m in top],
    }
    if implied:
        result["intent_tags"] = sorted(implied)
    if hint_only:
        result["hint_only"] = True
    if "high_protein" in implied:
        result["sort_used"] = sort
        result["min_protein_used"] = min_protein
    if "rich" in implied:
        result["sort_used"] = sort

    if ignored_restaurant:
        result["ignored_restaurant"] = ignored_restaurant

    if serves_wanted:
        result["serves_requested"] = serves_wanted
        exact = [m for m in matched if m.serves == serves_wanted]
        larger = [m for m in matched if m.serves > serves_wanted]
        if exact:
            result["serves_exact_available"] = True
            # Live 2026-08-25: "شاورما لثلاثة" + family still ranked a 4-person
            # shawarma tray first. Exact size has to lead the spoken pick.
            scored.sort(
                key=lambda pair: (
                    0 if pair[0].serves == serves_wanted else 1,
                    sort_keys.get(sort, sort_keys["relevance"])(pair),
                )
            )
            top = [m for m, _ in scored[:limit]]
            result["results"] = [meal_summary(m, filters) for m in top]
            result["showing"] = len(top)
        else:
            # No exact size in *this* filtered set. A 4-person tray is not an
            # answer to "ثلاثة بس مش أربعة". The old `>= N` check hid 30 real
            # 3-person family offers because a larger shawarma tray existed.
            result["no_meal_serves_requested"] = True
            extras = [
                m
                for m in cat.meals
                if m.serves == serves_wanted
                and m.is_offer
                and (include_grocery or "grocery" not in m.diet_tags)
            ]
            extras.sort(key=lambda m: (not m.is_popular, -m.rating, m.id))
            nearest = (
                min(larger, key=lambda m: (m.serves, m.effective_price, m.id))
                if larger
                else None
            )
            people = number_to_arabic_words(serves_wanted)
            dish = fix_pronunciation(" ".join(tokens)) if tokens else ""
            parts: list[str] = []
            if dish:
                parts.append(f"ما فيه {dish} تكفي {people} بالضبط.")
            else:
                parts.append(f"ما فيه وجبة تكفي {people} بالضبط بهالبحث.")
            if nearest:
                result["nearest_larger"] = meal_summary(nearest, filters)
            if extras:
                result["serves_exact_available"] = True
                result["offers_for_requested_serves"] = [
                    meal_summary(m, filters) for m in extras[:limit]
                ]
                alts = extras[:2]
                bits = [
                    f"{speakable_name(m.name)} من {speakable_name(m.restaurant_name)} "
                    f"بـ {sar_words(m.effective_price)}"
                    for m in alts
                ]
                if len(bits) == 1:
                    parts.append(f"بدائل لـ {people}: {bits[0]}. تبيها؟")
                else:
                    parts.append(
                        f"بدائل لـ {people}: {bits[0]}، و{bits[1]}. تبي أحدها؟"
                    )
                # Family/offer browse with a leftover dish word (شاورما) must
                # not keep showing the wrong size. Put the real N-person
                # offers in `results` so she opens one of those.
                if filters.only_offers or filters.offer_kinds:
                    top = extras[:limit]
                    result["results"] = [meal_summary(m, filters) for m in top]
                    result["showing"] = len(top)
                    result["total_matches"] = len(extras)
            elif nearest:
                parts.append(
                    f"أقرب شي {speakable_name(nearest.name)} تكفي "
                    f"{number_to_arabic_words(nearest.serves)}."
                )
            result["say_to_user_ar"] = " ".join(parts)

    if dropped_guessed_filters:
        result["dropped_filters"] = dropped_guessed_filters

    if price_ceiling_hit:
        cheapest = min(matched, key=lambda m: m.effective_price)
        result["no_cheaper_available"] = True
        result["sort_used"] = sort
        result["say_to_user_ar"] = (
            f"ما فيه أرخص من كذا بهالفئة الحين، أرخص شي عندنا "
            f"{speakable_name(cheapest.name)} بـ {sar_words(cheapest.effective_price)}"
        )

    if price_floor_hit:
        priciest = max(matched, key=lambda m: m.effective_price)
        result["no_pricier_available"] = True
        result["sort_used"] = sort
        result["say_to_user_ar"] = (
            f"ما فيه أغلى من كذا بهالفئة الحين، أغلى شي عندنا "
            f"{speakable_name(priciest.name)} بـ {sar_words(priciest.effective_price)}"
        )

    if total == 0:
        # "Not in this filtered set" is not "not in the catalog", and collapsing
        # the two produced the worst sentence of the CEO session: asked for a
        # shawarma *offer*, سلمى answered "ما عندنا شاورما" three turns running
        # while shawarma sat on the menu at 25 riyals. Whether the dish exists is
        # a question about the catalog, so it is asked against the catalog.
        elsewhere = _name_matches(
            [m for m in cat.meals if include_grocery or "grocery" not in m.diet_tags],
            tokens,
        )
        if unmatched_query and elsewhere:
            spoken_term = fix_pronunciation(unmatched_query)
            cheapest = min(elsewhere, key=lambda m: m.effective_price)
            result["exists_but_filtered"] = {
                "term": unmatched_query,
                "count": len(elsewhere),
                "example_meal_id": cheapest.id,
                "example_name_say_ar": speakable_name(cheapest.name),
                "example_restaurant_say_ar": speakable_name(cheapest.restaurant_name),
                "example_price_say_ar": sar_words(cheapest.effective_price),
            }
            constraint = "بعرض" if only_offers else "بهذي المواصفات"
            result["say_to_user_ar"] = (
                f"{spoken_term} موجودة عندنا، بس ما فيه منها شي {constraint}"
            )
            return result
        if unmatched_query:
            # No alternatives are returned here, deliberately.
            #
            # They were, and the model merged them with the request: asked for
            # ستربس it answered "رشحت لك ستربس من أصيل برجر" and opened برجر
            # دجاج مقرمش — twice, in two separate calls. Handing it a substitute
            # in the same payload as the user's word is an invitation to relabel
            # the substitute with that word, and an instruction not to ("لا
            # تسمين البديل باسم اللي طلبه") did not hold.
            #
            # With nothing to conflate, the honest sentence is the only one
            # available. If the user wants something else, the next turn can
            # search for it.
            # Echo the term back in its speakable spelling, not the user's. The
            # sentence handed over here is read verbatim and also ends up in the
            # transcript, so respelling only at the TTS boundary would leave
            # "ستربس" written everywhere the text is visible.
            spoken_term = fix_pronunciation(unmatched_query)
            result["query_not_found"] = unmatched_query
            result["say_to_user_ar"] = f"ما عندنا {spoken_term}"
        else:
            result["filters_too_strict"] = True
        return result

    hint = _next_question(matched, filters, total, len(top), named_dish)
    if hint:
        result["ambiguous"] = hint
    return result


def _name_kind(meal: Meal) -> Optional[str]:
    """Pairing bucket from the dish name: drink, dessert, side, or None (main)."""
    if "grocery" in meal.diet_tags:
        return None
    name = normalize_ar(meal.name)
    if _has_keyword(name, _PAIR_DRINK_NAME):
        return "drink"
    if "dessert" in meal.diet_tags or _has_keyword(name, _DESSERT_KEYWORDS):
        return "dessert"
    if _has_keyword(name, _PAIR_SIDE_NAME):
        return "side"
    return None


def _addon_kind(name: str) -> Optional[str]:
    folded = normalize_ar(name)
    if _has_keyword(folded, _PAIR_ADDON_DRINK):
        return "drink"
    if _has_keyword(folded, _PAIR_ADDON_SIDE):
        return "side"
    return None


def _pair_meal_row(meal: Meal, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "meal_id": meal.id,
        "name": meal.name,
        "name_say_ar": speakable_name(meal.name),
        "price_say_ar": sar_words(meal.effective_price),
        "price_sar": round(meal.effective_price, 2),
        "restaurant": meal.restaurant_name,
        "restaurant_say_ar": speakable_name(meal.restaurant_name),
    }


def _pair_addon_row(addon: dict[str, Any], kind: str) -> dict[str, Any]:
    name = str(addon.get("name") or "")
    price = float(addon.get("price") or 0)
    return {
        "kind": "addon",
        "addon_id": addon.get("id"),
        "name": name,
        "name_say_ar": speakable_name(name),
        "price_say_ar": sar_words(price),
        "price_sar": round(price, 2),
        "pairs_as": kind,
    }


def pair_speech(pairs: Optional[Iterable[dict[str, Any]]]) -> str:
    """One speakable cross-sell line from the first catalog pair.

    Names and prices come from ``*_say_ar`` on the pair row. Empty if there is
    nothing real to offer — the model should then only confirm the add.
    """
    rows = [p for p in (pairs or ()) if isinstance(p, dict)]
    if not rows:
        return ""
    pair = rows[0]
    name = str(pair.get("name_say_ar") or pair.get("name") or "").strip()
    if not name:
        return ""
    price = str(pair.get("price_say_ar") or "").strip()
    kind = str(pair.get("pairs_as") or pair.get("kind") or "").strip()
    lead = {
        "drink": "لو ودك مشروب معاها",
        "dessert": "لو ودك حلا بعدها",
        "side": "لو ودك جنب معاها",
        "addon": "لو ودك لمسة معاها",
    }.get(kind, "لو ودك شيء معاها")
    if price:
        return f"{lead}، {name} بـ {price}."
    return f"{lead}، {name}."


def suggest_pairs(
    meal: Meal,
    *,
    exclude_meal_ids: Optional[Iterable[str]] = None,
    exclude_addon_ids: Optional[Iterable[str]] = None,
    catalog: Optional[Catalog] = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Real addons/meals that pair with ``meal``. Facts only — no spoken script.

    Same restaurant first. Combo/family mains skip drinks (the bundle usually
    already includes one) and lean dessert. Already-carted ids are excluded.
    """
    cat = catalog or load_catalog()
    skip_meals = {str(x) for x in (exclude_meal_ids or ()) if x}
    skip_meals.add(meal.id)
    skip_addons = {str(x) for x in (exclude_addon_ids or ()) if x}

    source_kind = _name_kind(meal)
    combo = "combo" in meal.diet_tags or "family" in meal.diet_tags
    if source_kind == "drink":
        wanted = ("dessert",)
    elif source_kind == "dessert":
        wanted = ("drink",)
    elif source_kind == "side":
        wanted = ("drink",)
    elif combo:
        wanted = ("dessert", "side")
    else:
        wanted = ("drink", "dessert", "side")

    picks: list[dict[str, Any]] = []

    def take(row: dict[str, Any]) -> None:
        if len(picks) >= limit:
            return
        key = row.get("addon_id") or row.get("meal_id")
        if any((p.get("addon_id") or p.get("meal_id")) == key for p in picks):
            return
        slot = row.get("pairs_as") or row.get("kind")
        if any((p.get("pairs_as") or p.get("kind")) == slot for p in picks):
            return
        picks.append(row)

    same_rest = [
        m for m in cat.meals
        if m.restaurant_id == meal.restaurant_id
        and m.id not in skip_meals
        and "grocery" not in m.diet_tags
        and not m.is_combo
    ]
    pool_rest = sorted(same_rest, key=lambda m: (not m.is_popular, -m.rating, m.id))
    elsewhere = [
        m for m in cat.meals
        if m.restaurant_id != meal.restaurant_id
        and m.id not in skip_meals
        and "grocery" not in m.diet_tags
        and not m.is_combo
    ]
    pool_else = sorted(elsewhere, key=lambda m: (not m.is_popular, -m.rating, m.id))

    for kind in wanted:
        if len(picks) >= limit:
            break
        for addon in meal.addons:
            addon_id = str(addon.get("id") or "")
            if not addon_id or addon_id in skip_addons:
                continue
            if _addon_kind(str(addon.get("name") or "")) != kind:
                continue
            take(_pair_addon_row(addon, kind))
            break
        if len(picks) >= limit:
            break
        chosen: Optional[Meal] = None
        for candidate in pool_rest:
            if _name_kind(candidate) == kind:
                chosen = candidate
                break
        if chosen is None and not picks:
            for candidate in pool_else:
                if _name_kind(candidate) == kind:
                    chosen = candidate
                    break
        if chosen is not None:
            take(_pair_meal_row(chosen, kind))

    return picks[:limit]


def meal_details(meal_id: str, catalog: Optional[Catalog] = None) -> Optional[dict[str, Any]]:
    """Full detail for one meal, including the addons the agent can offer."""
    cat = catalog or load_catalog()
    meal = cat.by_id(meal_id)
    if meal is None:
        return None
    return {
        "meal_id": meal.id,
        "name": meal.name,
        "name_say_ar": speakable_name(meal.name),
        "description": meal.description,
        "restaurant": meal.restaurant_name,
        "restaurant_say_ar": speakable_name(meal.restaurant_name),
        "restaurant_id": meal.restaurant_id,
        "cuisine": meal.cuisine,
        **_price_speech(meal),
        "base_price_sar": round(meal.price, 2),
        **_offer_facts(meal),
        **_serves_facts(meal),
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        "protein_g": meal.protein,
        "protein_say_ar": number_to_arabic_words(meal.protein),
        "carbs_g": meal.carbs,
        "carbs_say_ar": number_to_arabic_words(meal.carbs),
        "fat_g": meal.fat,
        "fat_say_ar": number_to_arabic_words(meal.fat),
        "nutrition_source": "catalog",
        "rating": meal.rating,
        "spicy_available": meal.spicy_option,
        "kind": sorted(meal.protein_tags) or ["unspecified"],
        "addons": [
            {
                "addon_id": a.get("id"),
                "name": a.get("name"),
                "name_say_ar": speakable_name(str(a.get("name") or "")),
                "price_sar": a.get("price"),
                "price_say_ar": sar_words(float(a.get("price") or 0)),
            }
            for a in meal.addons
        ],
        "delivery_time": meal.delivery_time,
        "delivery_fee_sar": meal.delivery_fee,
        "min_order_sar": meal.min_order,
        "page_path": meal.page_path,
    }


def restaurant_details(
    value: str, catalog: Optional[Catalog] = None
) -> Optional[dict[str, Any]]:
    """Resolve a restaurant and describe it, with a few of its popular dishes.

    Accepts the same loose input as ``resolve_restaurant`` — an Arabic name is
    what the model normally has. The popular dishes ride along because the next
    question is almost always "وش أشهر شي عندهم؟", and answering it from here
    saves a second round trip mid-conversation.
    """
    cat = catalog or load_catalog()
    restaurant_id = resolve_restaurant(value, cat)
    if restaurant_id is None:
        return None
    restaurant = cat.restaurant_by_id(restaurant_id)
    if restaurant is None:
        return None

    meals = [m for m in cat.meals if m.restaurant_id == restaurant_id]
    popular = sorted(meals, key=lambda m: (not m.is_popular, -m.rating, m.id))[:3]
    # Names of every dish ride along so سلمى can answer "فيه مناقيش؟" from the
    # restaurant open itself. popular_meals alone hid مناقيش زعتر. Spoken
    # tashkeel (`name_say_ar`) is NOT copied onto the full menu — that field
    # 3–4×s every name and blew Gemma's 32k context after a few restaurant
    # opens (2026-08-16: prompt ≥32769, 0 output tokens, silent turn).
    menu = sorted(meals, key=lambda m: (not m.is_popular, -m.rating, m.id))
    menu_preview_cap = 25

    return {
        "restaurant_id": restaurant_id,
        "name": restaurant.get("name"),
        "name_say_ar": speakable_name(str(restaurant.get("name") or "")),
        "cuisine": restaurant.get("cuisine"),
        "rating": restaurant.get("rating"),
        "delivery_time": restaurant.get("deliveryTime"),
        "delivery_fee_sar": restaurant.get("deliveryFee"),
        "delivery_fee_say_ar": sar_words(float(restaurant.get("deliveryFee") or 0)),
        "min_order_sar": restaurant.get("minOrder"),
        "min_order_say_ar": sar_words(float(restaurant.get("minOrder") or 0)),
        "open_24h": bool(restaurant.get("open24h")),
        "meals_count": len(meals),
        "popular_meals": [
            {
                "meal_id": m.id,
                "name": m.name,
                "name_say_ar": speakable_name(m.name),
                "price_say_ar": sar_words(m.effective_price),
            }
            for m in popular
        ],
        "menu_names": [m.name for m in menu],
        "menu": [
            {
                "meal_id": m.id,
                "name": m.name,
                "price_sar": round(m.effective_price, 2),
            }
            for m in menu[:menu_preview_cap]
        ],
        "page_path": f"/restaurants/{restaurant_id}",
    }


# Pronunciation / tashkeel enforcement lives in src/spoken_lexicon.py.
# Re-exported here so existing imports (agent, tests, audit) keep working:
# PRONUNCIATION_FIXES, sanitize_tashkeel, fix_pronunciation, clean_for_speech,
# speakable_name.


def catalog_index(catalog: Optional[Catalog] = None) -> str:
    """A compact "what exists" summary for the system prompt.

    Restaurant names stop the model inventing venues. Grouping by cuisine and
    dropping per-row delivery/count keeps this well under a kilobyte of tokens
    instead of 127 tashkeel-heavy lines on every turn (the 32k overflow).
    Meal names and prices still come from search_meals, never from this block.
    """
    cat = catalog or load_catalog()
    by_cuisine: dict[str, list[str]] = {}
    for restaurant in cat.restaurants:
        name = str(restaurant.get("name") or "").strip()
        if not name:
            continue
        cuisine = str(restaurant.get("cuisine") or "أخرى").strip() or "أخرى"
        by_cuisine.setdefault(cuisine, []).append(name)
    lines = [
        f"{cuisine}: {', '.join(names)}"
        for cuisine, names in sorted(by_cuisine.items(), key=lambda kv: kv[0])
    ]
    return "\n".join(lines)
