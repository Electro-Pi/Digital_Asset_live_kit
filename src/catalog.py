"""The Jahez restaurant/meal catalog: loading, spoken forms, pairing facts.

The catalog is ~1,650 meals, loaded once per process. Search lives in
``src.search`` (typed filters + BM25); ``search_meals`` here is its entry point
for callers that pass keyword arguments. Structured attributes the frontend
catalog does not carry — dish type, protein, crispy — come from
``meal_attributes.json`` beside it, written by ``scripts/annotate_catalog.py``.
The catalog itself is a byte-for-byte copy of the app's
(``scripts/sync_catalog.py``); ``check_catalog_sync`` reports drift at startup.

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
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from loguru import logger

from src import flow_say
from src.catalog_text import has_keyword as _has_keyword, normalize_ar
from src.spoken_lexicon import LATIN_PHRASES, LATIN_WORDS, normalize_key, speakable_name
# Re-exported: the TTS text filter is wired as ``catalog.clean_for_speech``.
from src.spoken_lexicon import clean_for_speech

__all__ = ["clean_for_speech"]
from src.config import CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_PATH = REPO_ROOT / "resturant's-catalog" / "catalog.json"
ATTRIBUTES_FILENAME = "meal_attributes.json"
# Where the app keeps its copy when both repos sit side by side.
_SIBLING_FRONTEND_CATALOG = REPO_ROOT.parent / "jahez-frontend" / "data" / "catalog.json"

# Cuisines that are not "a meal" in the sense سلمى recommends.
GROCERY_CUISINES = {"بقاله"}


_PAIR_ADDON_DRINK = ("مشروب", "غازي", "عصير", "كولا")
_PAIR_ADDON_SIDE = ("بطاطس", "ثوم", "مخلل", "صلصه", "صوص")

# Thresholds used for derived tags and for the "why this meal" reasons. They are
# named because they appear in both filtering and explanation, and drifting them
# apart would make سلمى justify a pick with a rule it did not actually use.
# Defined once, in src/config.py. Kept under these names for the callers that
# read them.
LIGHT_CALORIES_MAX = CONFIG.rules.light_calories_max
HIGH_PROTEIN_MIN = CONFIG.rules.high_protein_min
RICH_CALORIES_MIN = CONFIG.rules.rich_calories_min
RICH_FAT_MIN = CONFIG.rules.rich_fat_min
KETO_CARBS_MAX = CONFIG.rules.keto_carbs_max

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# How many people a meal feeds. The catalog states this in prose only — "لـ 6
# أشخاص" in the name, "تكفي 6 أشخاص" or "وجبة كاملة لشخص واحد" in the
# description — so it has to be parsed out. Leaving it unparsed is what let
# سلمى answer "تكفي الستة" about a single 25-riyal shawarma wrap and invent a
# 94-riyal family meal that does not exist: the prompt promises she can answer
# "يكفي كم شخص" while no field ever carried the number.
_SERVES_RE = re.compile(r"(?:لـ?\s*|تكفي\s*)([0-9]{1,2})\s*(?:أشخاص|اشخاص|شخص|أفراد|افراد)")
_SERVES_ONE_RE = re.compile(r"لشخص\s+واحد|فردي")


# Glued article only — «البرجر» → «برجر». A lone ب/و must stay: «برجر»
# stripped by `[بلك]?` would become «رجر» and miss the kitchen.
_ARTICLE_PREFIX_RE = re.compile(r"^[وفبلك]*ال")


def _bare_query_token(token: str) -> str:
    """Stem a spoken word so `_has_keyword` can match the catalog name.

    Live 2026-08-30: ASR «البرغر» repaired to «البرجر», then required the
    meal name to contain that exact token and answered «ما عندنا» with 211
    burgers on the menu. Prefixes belong on the catalog side, not the query.
    """
    tok = normalize_ar(token)
    if not tok:
        return ""
    stripped = _ARTICLE_PREFIX_RE.sub("", tok)
    return stripped if len(stripped) >= 2 else tok


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
    ("برغر", "برجر"),
    ("البرغر", "البرجر"),
    ("دوناتس", "دونات"),
    ("دونتس", "دونات"),
    ("ايسكريم", "ايس كريم"),
    ("اسكريم", "ايس كريم"),
    ("donuts", "دونات"),
    ("doughnut", "دونات"),
    ("doughnuts", "دونات"),
    ("ستريبس", "ستربس"),
    ("ستريب", "ستربس"),
    ("استربس", "ستربس"),
    ("strips", "ستربس"),
    ("chicken", "دجاج"),
    ("fried", "مقلي"),
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
    ("للسردات", "للسلة"),
    ("للسردة", "للسلة"),
    ("للسرده", "للسلة"),
    ("للسلوى", "للسلة"),
    ("للسلوي", "للسلة"),
    ("السردات", "السلة"),
    ("السردة", "السلة"),
    ("السرده", "السله"),
    ("السالب", "السله"),
    ("تلات اصوات", "تلات اشخاص"),
    ("ثلاث اصوات", "ثلاث اشخاص"),
    ("رقم الجواز", "رقم الجوال"),
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
    # Live 2026-08-26: للسردات / للسردة / للسلوى.
    ("للسردات", "للسلة"),
    ("للسردة", "للسلة"),
    ("للسرده", "للسلة"),
    ("للسلوى", "للسلة"),
    ("للسلوي", "للسلة"),
    ("السردات", "السلة"),
    ("السردة", "السلة"),
    ("السرده", "السلة"),
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
    ("رقم الجواز", "رقم الجوال"),
    ("سحري", "صحي"),
    ("سحرية", "صحية"),
    ("سحريه", "صحيه"),
    ("سخرية", "صحية"),
    ("سخريه", "صحيه"),
    ("اكل سحري", "اكل صحي"),
    ("اكل سخرية", "اكل صحي"),
    ("حاجة سحرية", "حاجة صحية"),
    ("حاجه سحريه", "حاجه صحيه"),
    ("حاجة تضحي", "حاجة صحية"),
    ("حاجه تضحي", "حاجه صحيه"),
    ("آتي لي", "هاتي لي"),
    ("اتيلي", "هاتيلي"),
    ("اتي لي", "هاتي لي"),
    ("من دور", "من دول"),
    ("برغر", "برجر"),
    ("البرغر", "البرجر"),
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


_ASR_TAG_RE = re.compile(r"</?(?:hesitation|noise|unk|unknown)\s*/?>", re.IGNORECASE)


def repair_user_transcript(text: str) -> str:
    """Rewrite ASR slips in the utterance the model actually hears.

    ``repair_asr_query`` folds ة→ه and strips hamza, which is right for catalog
    matching and wrong for the LLM: it would receive a smashed spelling of the
    whole turn. Here we only swap the known mishearings in place.
    """
    out = _ASR_TAG_RE.sub(" ", str(text or ""))
    out = " ".join(out.split())
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
    "واحد": 1, "اثنين": 2, "ثنين": 2, "اتنين": 2, "ثلاث": 3, "ثلاثه": 3,
    "تلات": 3, "تلاته": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5,
    "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8, "ثمانيه": 8,
    "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
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
# "إحنا ثلاثة" / "كلنا 3" is the party size they want, not the size they rejected.
_GROUP_SIZE_RE = re.compile(
    r"(?<!\w)(?:احنا|نحن|كلنا)\s+"
    r"(\d{1,2}|" + "|".join(_COUNT_WORD_VALUES) + r")(?!\w)"
)
# "ليا أنا واتنين صحابي" = me + two friends.
_FRIENDS_COUNT_RE = re.compile(
    r"(?:و|مع|معايا|معي)\s*"
    r"(\d{1,2}|" + "|".join(_COUNT_WORD_VALUES) + r")\s*"
    r"(?:صحابي|اصحابي|اصدقائي|اصدقاء|اصحاب|ربع)(?!\w)"
)
_FRIEND_NOUNS = frozenset({
    "صحابي", "اصحابي", "اصدقائي", "اصدقاء", "اصحاب", "ربع",
    "صاحبتي", "صاحبي",
})

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
    "صاحبتي", "صاحبي", "صحابي", "اصحابي", "اصدقائي",
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
            val = int(bare) if bare.isdigit() else _COUNT_WORD_VALUES[bare]
            nxt = words[j + 1] if j + 1 < len(words) else ""
            nxt_bare = _bare_ar_word(nxt)
            if nxt_bare in _FRIEND_NOUNS and 1 <= val <= 19:
                rest = words[:start] + words[j + 2 :]
                return " ".join(rest), 1 + val
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
    match = _GROUP_SIZE_RE.search(folded)
    if match:
        raw = match.group(1)
        count = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (count if 1 <= count <= 20 else None)
    match = _WITH_ME_COUNT_RE.search(folded)
    if match:
        raw = match.group(1)
        others = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        total = 1 + others
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (total if 2 <= total <= 20 else None)
    match = _FRIENDS_COUNT_RE.search(folded)
    if match:
        raw = match.group(1)
        others = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        total = 1 + others
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (total if 2 <= total <= 20 else None)
    match = _QUERY_DUAL_RE.search(folded)
    if match:
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), 2
    named_rest, named_count = _extract_companion_list(folded)
    if named_count:
        return named_rest, named_count
    match = _QUERY_HEADCOUNT_RE.search(folded)
    if match:
        raw = match.group(1)
        count = int(raw) if raw.isdigit() else _COUNT_WORD_VALUES[raw]
        rest = f"{folded[:match.start()]} {folded[match.end():]}"
        return " ".join(rest.split()), (count if 1 <= count <= 20 else None)
    return folded, None


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
    # main | side | salad | soup | drink | dessert | grocery, from
    # meal_attributes.json. Never guessed at runtime.
    type: str = "main"
    # The restaurant's express-delivery badge in the app.
    fast_delivery: bool = False

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


def _derive_diet_tags(
    meal_json: dict[str, Any], cuisine: str, dish_type: str, attribute_tags: Iterable[str]
) -> frozenset[str]:
    """Tags from the dish's numbers and its recorded type.

    Nutrition tags come from the catalog's own numbers and the thresholds in
    ``CONFIG.rules``; dish-kind tags (drink, dessert, grocery) come from the
    type recorded in ``meal_attributes.json``, never from words in the name.
    """
    rules = CONFIG.rules
    tags: set[str] = set(attribute_tags)
    calories = int(meal_json.get("calories") or 0)
    protein = int(meal_json.get("protein") or 0)
    fat = int(meal_json.get("fat") or 0)
    carbs = int(meal_json.get("carbs") or 0)
    serves = max(1, _parse_serves(str(meal_json.get("name") or ""), str(meal_json.get("description") or "")) or 1)
    cal_per_person = calories / serves
    fat_per_person = fat / serves
    healthy_dish = cuisine == normalize_ar(HEALTHY_CUISINE) or dish_type == "salad"
    if (0 < cal_per_person <= rules.light_calories_max) or healthy_dish:
        tags.add("light")
    elif fat_per_person >= rules.rich_fat_min or cal_per_person >= rules.rich_calories_min:
        tags.add("rich")
    if protein >= rules.high_protein_min:
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
    if dish_type in ("drink", "dessert", "grocery"):
        tags.add(dish_type)
    if 0 < carbs <= rules.keto_carbs_max and dish_type in ("main", "side", "salad", "soup"):
        tags.add("keto")
    if float(meal_json.get("cashbackPercent") or 0) > 0:
        tags.add("cashback")
    return frozenset(tags)


def _build_meal(meal_json: dict[str, Any], restaurant: dict[str, Any], attributes: dict[str, Any]) -> Meal:
    cuisine = normalize_ar(restaurant.get("cuisine"))
    name = str(meal_json.get("name") or "")
    description = str(meal_json.get("description") or "")
    restaurant_tags = tuple(str(t) for t in (restaurant.get("tags") or []))
    includes = tuple(str(i) for i in (meal_json.get("comboIncludes") or []))
    dish_type = str(attributes.get("type") or "main")
    blob = normalize_ar(
        " ".join([name, description, str(restaurant.get("name") or ""), str(restaurant.get("cuisine") or ""), " ".join(restaurant_tags)])
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
        offer_price=(float(meal_json["offerPrice"]) if meal_json.get("offerPrice") is not None else None),
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
        protein_tags=frozenset(attributes.get("protein") or ()),
        diet_tags=_derive_diet_tags(meal_json, cuisine, dish_type, attributes.get("tags") or ()),
        search_blob=blob,
        type=dish_type,
        fast_delivery=bool(restaurant.get("fastDelivery")),
    )


@dataclass(frozen=True)
class Catalog:
    meals: tuple[Meal, ...]
    restaurants: tuple[dict[str, Any], ...]

    @cached_property
    def _meals_by_id(self) -> dict[str, Meal]:
        return {normalize_ar(m.id): m for m in self.meals}

    @cached_property
    def _restaurants_by_id(self) -> dict[str, dict[str, Any]]:
        return {normalize_ar(r.get("id")): r for r in self.restaurants}

    @cached_property
    def index(self):
        """BM25 index over the meals, built once. See ``src.search``."""
        from src.search import build_index

        return build_index(self.meals)

    def by_id(self, meal_id: str) -> Optional[Meal]:
        return self._meals_by_id.get(normalize_ar(meal_id))

    def restaurant_by_id(self, restaurant_id: str) -> Optional[dict[str, Any]]:
        return self._restaurants_by_id.get(normalize_ar(restaurant_id))


def catalog_path() -> Path:
    return Path(CONFIG.catalog_path or DEFAULT_CATALOG_PATH)


def load_attributes(path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Structured attributes per meal id. Empty when the file is missing."""
    target = path or catalog_path().with_name(ATTRIBUTES_FILENAME)
    if not target.exists():
        logger.error("Meal attributes file missing path={}; every dish is treated as a main", target)
        return {}
    with target.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_catalog(path: Optional[str] = None) -> Catalog:
    """Load and index the catalog once per process."""
    source = Path(path) if path else catalog_path()
    with source.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    attributes = load_attributes(source.with_name(ATTRIBUTES_FILENAME))
    restaurants = tuple(raw.get("restaurants") or [])
    meals = tuple(
        _build_meal(meal_json, restaurant, attributes.get(str(meal_json.get("id"))) or {})
        for restaurant in restaurants
        for meal_json in (restaurant.get("meals") or [])
    )
    missing = [m.id for m in meals if m.id not in attributes]
    if missing:
        logger.error(
            "{} meal(s) have no row in {}; run scripts/annotate_catalog.py. First: {}",
            len(missing), ATTRIBUTES_FILENAME, missing[:5],
        )
    logger.info(
        "Catalog loaded path={} restaurants={} meals={} types={}",
        source, len(restaurants), len(meals),
        {t: sum(1 for m in meals if m.type == t) for t in sorted({m.type for m in meals})},
    )
    return Catalog(meals=meals, restaurants=restaurants)


def frontend_catalog_path() -> Optional[Path]:
    """The app's catalog, when it is reachable from here."""
    configured = CONFIG.frontend_catalog_path
    if configured:
        return Path(configured)
    return _SIBLING_FRONTEND_CATALOG if _SIBLING_FRONTEND_CATALOG.exists() else None


def catalog_differences(frontend: Path, agent: Path) -> list[str]:
    """Meal-level differences between two catalog files ("" list = identical).

    Compared field by field rather than by hash, so the log says what drifted
    (a price, an addon, a combo's contents) instead of only that something did.
    Images are ignored: she never speaks them.
    """
    def meals(path: Path) -> dict[str, dict[str, Any]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(m.get("id")): m for r in data.get("restaurants") or [] for m in r.get("meals") or []}

    a, b = meals(frontend), meals(agent)
    diffs: list[str] = []
    for meal_id in sorted(set(a) - set(b)):
        diffs.append(f"{meal_id}: only in the app")
    for meal_id in sorted(set(b) - set(a)):
        diffs.append(f"{meal_id}: only in the agent")
    for meal_id in sorted(set(a) & set(b)):
        for key in sorted(set(a[meal_id]) | set(b[meal_id])):
            if key == "image":
                continue
            if a[meal_id].get(key) != b[meal_id].get(key):
                diffs.append(f"{meal_id}.{key}: app={a[meal_id].get(key)!r} agent={b[meal_id].get(key)!r}")
    return diffs


def check_catalog_sync() -> list[str]:
    """Log whether the agent's catalog still matches the app's. Returns the diffs."""
    frontend = frontend_catalog_path()
    if frontend is None or not frontend.exists():
        logger.info("Frontend catalog not reachable; skipping catalog sync check")
        return []
    diffs = catalog_differences(frontend, catalog_path())
    if diffs:
        logger.error(
            "Catalog drift: {} difference(s) between the app and the agent; run "
            "scripts/sync_catalog.py. First: {}", len(diffs), diffs[:5],
        )
    else:
        logger.info("Catalog matches the app's copy path={}", frontend)
    return diffs


# ─── Search ──────────────────────────────────────────────────────────────────


def resolve_cuisine(cuisine: Optional[str]) -> Optional[str]:
    """Map a spoken kitchen word onto the catalog's cuisine key (مشويات → مشاوي).

    The words live in ``data/search_vocab.json`` (``cuisine_cues``).
    """
    if not cuisine:
        return None
    from src.search import load_vocab

    wanted = _bare_query_token(cuisine)
    if not wanted:
        return None
    for key, cues in load_vocab().cuisine_cues.items():
        variants = {normalize_ar(key), _bare_query_token(key)} | set(cues) | {_bare_query_token(c) for c in cues}
        if wanted in variants:
            return key
    return None


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


def meal_is_healthy(meal: Meal) -> bool:
    """Healthy by cuisine / nutrition. Fries, cola, milkshake, dessert are out."""
    if "rich" in meal.diet_tags:
        return False
    kind = _name_kind(meal)
    if kind == "dessert":
        return False
    folded = normalize_ar(meal.name)
    if any(
        _has_keyword(folded, (w,))
        for w in (
            "فرايز", "بطاطس", "كولا", "cola", "بيبسي", "ميلك شيك",
            "milkshake", "شيك",
        )
    ):
        return False
    if meal.cuisine == HEALTHY_CUISINE:
        return True
    if meal.calories and meal.calories <= LIGHT_DRINK_MAX_CALORIES:
        return True
    if meal.calories and meal.protein:
        return meal.protein / (meal.calories / 100.0) >= HEALTHY_PROTEIN_PER_100KCAL
    return False


def meal_is_light_source(meal: Optional[Meal]) -> bool:
    """True when this dish itself is light — cuisine or diet tags, never a name list."""
    if meal is None:
        return False
    return meal.cuisine == HEALTHY_CUISINE or bool(meal.diet_tags & {"light", "keto"})


def closest_healthy_meal(
    meal: Optional[Meal],
    *,
    catalog: Optional[Catalog] = None,
    exclude_ids: Optional[Iterable[str]] = None,
) -> Optional[Meal]:
    """Nearest healthy-tagged stand-in for a fried / rich dish."""
    if meal is None:
        return None
    cat = catalog or load_catalog()
    skip = {str(x) for x in (exclude_ids or ()) if x}
    skip.add(meal.id)
    same = [
        m for m in cat.meals
        if m.restaurant_id == meal.restaurant_id
        and m.id not in skip
        and meal_is_healthy(m)
        and "grocery" not in m.diet_tags
        and _name_kind(m) == _name_kind(meal)
    ]
    if same:
        return sorted(same, key=lambda m: (not m.is_popular, m.calories or 9999, m.id))[0]
    any_kind = [
        m for m in cat.meals
        if m.id not in skip
        and meal_is_healthy(m)
        and "grocery" not in m.diet_tags
        and _name_kind(m) not in {"drink", "dessert"}
    ]
    if not any_kind:
        return None
    return sorted(any_kind, key=lambda m: (not m.is_popular, m.calories or 9999, m.id))[0]


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


def rating_to_arabic_words(value: float) -> str:
    """Spell a one-decimal catalog rating without model-side conversion."""
    tenths = int(round(float(value) * 10))
    whole, fraction = divmod(tenths, 10)
    spoken = number_to_arabic_words(whole)
    if fraction:
        spoken = f"{spoken} فاصلة {number_to_arabic_words(fraction)}"
    return spoken


def sar_words(value: float) -> str:
    # «واحد ريال» and «اثنين ريال» are not said; «ريال واحد» and «ريالين» are.
    n = int(round(float(value)))
    if n == 1:
        return "ريال واحد"
    if n == 2:
        return "ريالين"
    return f"{number_to_arabic_words(value)} ريال"


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


def search_meals(
    *,
    query: str = "",
    dish_type: Optional[str] = None,
    cuisine: Optional[str] = None,
    max_calories: Optional[int] = None,
    min_protein: Optional[int] = None,
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    spicy: bool = False,
    healthy: bool = False,
    only_offers: bool = False,
    restaurant_id: Optional[str] = None,
    prefer_restaurant_id: Optional[str] = None,
    serves: Optional[int] = None,
    group_budget: Optional[float] = None,
    include_grocery: bool = False,
    offer_kind: Optional[str] = None,
    cashback: bool = False,
    sort: str = "relevance",
    limit: Optional[int] = None,
    catalog: Optional[Catalog] = None,
) -> dict[str, Any]:
    """Search the catalog. See ``src.search`` for how the query is read and ranked."""
    from src.search import SearchRequest, search

    return search(
        SearchRequest(
            query=query or "",
            dish_type=dish_type or None,
            cuisine=cuisine or None,
            restaurant=restaurant_id or None,
            prefer_restaurant=prefer_restaurant_id or None,
            max_calories=max_calories or None,
            min_protein=min_protein or None,
            max_price=max_price or None,
            min_price=min_price or None,
            spicy=bool(spicy),
            healthy=bool(healthy),
            only_offers=bool(only_offers),
            offer_kind=offer_kind or None,
            cashback=bool(cashback),
            serves=serves or None,
            group_budget=group_budget or None,
            include_grocery=bool(include_grocery),
            sort=sort or "relevance",
            limit=limit,
        ),
        catalog or load_catalog(),
    )


# Pairing buckets by recorded dish type. Salads, soups and starters all pair
# as a side.
_PAIRING_BUCKET = {"drink": "drink", "dessert": "dessert", "side": "side", "salad": "side", "soup": "side"}


def _name_kind(meal: Meal) -> Optional[str]:
    """Pairing bucket: drink, dessert, side, or None for a main (or a grocery line)."""
    return _PAIRING_BUCKET.get(meal.type)


_PAIR_ANCHOR_SKIP = frozenset(
    {"حلا", "كومبو", "وجبه", "عرض", "بروتين", "كيتو", "مقرمش", "عائلي"}
)


def _tts_latinizes(word: str) -> bool:
    """True when this stem is a borrowed dish name — pair-pitch must not glue it."""
    if not word:
        return False
    for token in str(word).split():
        key = normalize_key(token)
        if key in LATIN_WORDS or key in LATIN_PHRASES:
            return True
    return False


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
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        "protein_g": meal.protein,
        "protein_say_ar": number_to_arabic_words(meal.protein),
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


# Nutrition thresholds for a healthy-mood pairing. Grams of protein per 100
# kcal cleanly splits this catalog: salads and smoothies sit at 9–10, every
# dessert at 2.2 or below. The calorie floor lets teas and coffees through.
HEALTHY_CUISINE = "صحي"
HEALTHY_PROTEIN_PER_100KCAL = CONFIG.rules.healthy_protein_per_100kcal
LIGHT_DRINK_MAX_CALORIES = CONFIG.rules.light_drink_max_calories
# With nobody's headcount stated, an upgrade is a size step for the same person,
# not a different tier: at most this much dearer than the dish he is looking at.
SOLO_UPGRADE_MAX_PRICE_RATIO = CONFIG.rules.solo_upgrade_max_price_ratio
MEAL_DEAL_MAX_EXTRA_RATIO = CONFIG.rules.meal_deal_max_extra_ratio


# Words that make a name the bigger size of the same dish.
_SIZE_UP_WORDS = ("كبير", "كبيره", "عائلي", "عائليه", "دبل", "double", "ملكي", "جامبو", "لارج", "xl")


_DELIVERY_RANGE_RE = re.compile(r"(\d+)\s*[–\-]\s*(\d+)")


def delivery_minutes(meal: Meal) -> Optional[tuple[int, int]]:
    """The restaurant's delivery window in minutes («30–45 د» → (30, 45)), or None."""
    match = _DELIVERY_RANGE_RE.search(meal.delivery_time.translate(_ARABIC_INDIC_DIGITS))
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (low, high) if 0 < low <= high else None


def delivery_minutes_say_ar(meal: Meal) -> str:
    window = delivery_minutes(meal)
    if window is None:
        return ""
    low, high = window
    return f"من {number_to_arabic_words(low)} إلى {number_to_arabic_words(high)} دقيقة"


def meal_deal_option(meal: Optional[Meal], *, catalog: Optional[Catalog] = None) -> Optional[Meal]:
    """The restaurant's single-person combo built around this same dish, or None.

    The combo's first item must be this dish by name («كبسة دجاج» in «وجبة
    كبسة فردية»), not just the same kind of dish: a salmon roll is not offered
    a California-roll meal. At most ``MEAL_DEAL_MAX_EXTRA_RATIO`` of the
    dish's price more; a combo that costs less is kept, since it is the better
    deal for the caller.
    """
    if meal is None or meal.is_offer or meal.is_combo or meal.type != "main" or int(meal.serves or 1) > 1:
        return None
    dish = set(normalize_ar(meal.name).split())
    if not dish:
        return None
    cat = catalog or load_catalog()
    best: Optional[Meal] = None
    best_key: Optional[tuple[Any, ...]] = None
    for other in cat.meals:
        if other.restaurant_id != meal.restaurant_id or other.id == meal.id:
            continue
        if other.offer_kind != "combo" or int(other.serves or 1) > 1 or not other.combo_includes:
            continue
        main = set(normalize_ar(other.combo_includes[0]).split())
        if not main or not (main <= dish or dish <= main):
            continue
        extra = other.effective_price - meal.effective_price
        if extra > meal.effective_price * MEAL_DEAL_MAX_EXTRA_RATIO:
            continue
        key = (extra, other.id)
        if best_key is None or key < best_key:
            best, best_key = other, key
    return best


def upgrade_option(
    meal: Optional[Meal],
    *,
    catalog: Optional[Catalog] = None,
    party_size: Optional[int] = None,
) -> Optional[Meal]:
    """The same dish in a bigger size at the same restaurant, or None.

    "Bigger" means it feeds more people or its name says so («كبير»،
    «عائلي»، «دبل»، «ملكي») where this dish's name does not. A pricier
    different dish (chicken shawarma → meat shawarma) is not an upgrade.
    Never a size for more people than are eating; with no headcount stated,
    at most ``SOLO_UPGRADE_MAX_PRICE_RATIO`` times this dish's price.
    """
    if meal is None:
        return None
    eaters = max(int(party_size or 0), int(meal.serves or 1), 1)
    cat = catalog or load_catalog()
    skip = _PAIR_ANCHOR_SKIP | {"مع", "من", "في", "على", "ال"} | set(_SIZE_UP_WORDS)
    ordered = normalize_ar(meal.name).split()
    name_tokens = set(ordered)
    # The dish's head word: «شاورما» in «شاورما دجاج», «بيتزا» in «بيتزا خضار».
    head = next((tok for tok in ordered if len(tok) >= 3 and tok not in skip), "")
    if not head:
        return None
    best: Optional[Meal] = None
    best_key: Optional[tuple[Any, ...]] = None
    for other in cat.meals:
        if other.id == meal.id or other.restaurant_id != meal.restaurant_id or other.type != meal.type:
            continue
        other_tokens = set(normalize_ar(other.name).split())
        if head not in other_tokens:
            continue
        bigger_serves = int(other.serves or 1) > int(meal.serves or 1)
        says_bigger = any(w in other_tokens and w not in name_tokens for w in _SIZE_UP_WORDS)
        if not (bigger_serves or says_bigger) or other.effective_price <= meal.effective_price:
            continue
        if int(other.serves or 1) > eaters:
            continue
        if not party_size and other.effective_price > meal.effective_price * SOLO_UPGRADE_MAX_PRICE_RATIO:
            continue
        key = (other.effective_price - meal.effective_price, other.id)
        if best_key is None or key < best_key:
            best, best_key = other, key
    return best


def suggest_pairs(
    meal: Meal,
    *,
    exclude_meal_ids: Optional[Iterable[str]] = None,
    exclude_addon_ids: Optional[Iterable[str]] = None,
    catalog: Optional[Catalog] = None,
    limit: int = 2,
    healthy_mood: Optional[bool] = None,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Context-aware cross-sell: healthy meal → healthy pair, anything else → open.

    The source meal's diet_tags drive the mood. If it carries "light", "keto",
    or "high_protein", the pairing pool is filtered to "light" items first.
    If no light candidates exist, it falls back to the full pool so the user
    always gets a suggestion. Nothing is hardcoded — it reads what the catalog
    already tags.
    """
    cat = catalog or load_catalog()

    def pick(pool: Sequence[Any]) -> Any:
        # Deterministic: the same call offers the same complement every time,
        # while different calls (session ids) rotate through the top few.
        return pool[flow_say._stable_index("pair", meal.id, session_id, mod=len(pool))]

    skip_meals = {str(x) for x in (exclude_meal_ids or ()) if x}
    skip_meals.add(meal.id)
    skip_addons = {str(x) for x in (exclude_addon_ids or ()) if x}

    # Mood normally follows the source meal's own tags, but the caller can
    # override it from the conversation: someone who just said "شي صحي" and
    # settled for a rich seafood platter still wants a light pairing.
    if healthy_mood is None:
        # "high_protein" alone is not a light mood: a 1600-calorie seafood
        # platter carries it, and so did the burger. "rich" is the reliable
        # signal that the customer is not eating light.
        healthy_mood = "rich" not in meal.diet_tags and (
            meal.cuisine == HEALTHY_CUISINE
            or bool(meal.diet_tags & {"light", "keto"})
        )

    source_kind = _name_kind(meal)
    combo = "combo" in meal.diet_tags or "family" in meal.diet_tags
    if source_kind == "drink":
        wanted = ("dessert",)
    elif source_kind == "dessert":
        wanted = ("drink",)
    elif source_kind == "side":
        wanted = ("drink",)
    elif combo:
        wanted = ("dessert", "drink", "side")
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

    def _is_healthy_candidate(m: Meal) -> bool:
        return meal_is_healthy(m)

    def _mood_filter(candidates: list[Meal]) -> list[Meal]:
        """In a healthy mood, keep only candidates the nutrition backs up.

        Returning an empty list is correct: better no pairing at all than a
        pastry offered to someone who just asked for something light.
        """
        if not healthy_mood:
            return candidates
        return [m for m in candidates if _is_healthy_candidate(m)]

    for kind in wanted:
        if len(picks) >= limit:
            break
        # 1. Matching addons from the same meal
        matching_addons = []
        for addon in meal.addons:
            addon_id = str(addon.get("id") or "")
            if not addon_id or addon_id in skip_addons:
                continue
            if _addon_kind(str(addon.get("name") or "")) == kind:
                matching_addons.append(addon)
        if matching_addons and not healthy_mood:
            # An addon row carries no nutrition, so it cannot be vetted against
            # a healthy request — only offer one when the mood is open.
            chosen_addon = pick(matching_addons)
            take(_pair_addon_row(chosen_addon, kind))
            if len(picks) >= limit:
                break

        # 2. Matching meals from the same restaurant (mood-filtered, random for variety)
        matching_rest = _mood_filter([m for m in pool_rest if _name_kind(m) == kind])
        if matching_rest:
            top_rest = matching_rest[:max(3, min(6, len(matching_rest)))]
            chosen = pick(top_rest)
            take(_pair_meal_row(chosen, kind))
            continue
        # 3. Matching meals elsewhere across the catalog (mood-filtered). In a
        # healthy mood the customer's own restaurant often has nothing light,
        # and a pairing from across the catalog beats none at all.
        matching_else = _mood_filter([m for m in pool_else if _name_kind(m) == kind])
        # A rich/combo main stays in its kitchen. Cross-shop "healthy" pairs
        # (pizza + quinoa smoothie) are a different order, not a complement.
        if matching_else and meal_is_light_source(meal) and healthy_mood and not picks:
            top_else = matching_else[:max(5, min(12, len(matching_else)))]
            chosen = pick(top_else)
            take(_pair_meal_row(chosen, kind))

    return picks[:limit]


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
