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

# Thresholds used for derived tags and for the "why this meal" reasons. They are
# named because they appear in both filtering and explanation, and drifting them
# apart would make سلمى justify a pick with a rule it did not actually use.
LIGHT_CALORIES_MAX = 400
HIGH_PROTEIN_MIN = 30
RICH_CALORIES_MIN = 650
RICH_FAT_MIN = 30
FAMILY_KEYWORDS = ("عائلي", "عائليه", "يكفي", "كبير")

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
    "offer": ("عرض", "عروض", "خصم", "رخيص", "ارخص"),
    "family": ("عائله", "عائلي", "عائليه", "عزيمه", "ضيوف", "جماعه"),
    "dessert": ("حلا", "حلو", "حلي", "حلويات", "تحليه", "ديزرت", "سكر", "حادق"),
    "drink": ("مشروب", "عصير", "شراب", "بارد"),
}

# Hints that name a kind of food rather than a property of one. These narrow
# the result set; the rest (light, rich, high_protein, offer, family) only re-rank.
_CATEGORY_HINTS = frozenset({"dessert", "drink"})

_STOPWORDS = {
    "ابغى", "ابي", "اريد", "وجبه", "اكل", "اكله", "حاجه", "طعام",
    "شي", "شيء", "من", "في", "على", "مع",
    "او", "و", "لي", "لك", "الي", "هذا", "هذي", "بس", "كثير", "مره", "زين",
}

# Adjectives that ride next to a hint ("بروتين عالي") and must not be treated
# as a dish name. Live failure: "رشحلي أكل بروتين عالي" tokenized to
# ["بروتين", "عالي"], "عالي" matched no meal, search returned query_not_found,
# and سلمى stalled instead of ranking high-protein dishes.
_HINT_MODIFIERS = frozenset({
    "عالي", "عاليه", "منخفض", "منخفضه", "قليل", "قليله", "مرتفع", "مرتفعه",
    "كثير", "كثيره", "مليان", "مليانه", "مديان", "مديانه",
})


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
    spicy: Optional[bool] = None
    only_offers: bool = False
    restaurant_id: Optional[str] = None
    # Soft preference, not a filter: the restaurant whose page is open. A hard
    # filter here would answer "ما عندنا بيتزا" to someone standing in a grill
    # house, so this only reorders.
    prefer_restaurant_id: Optional[str] = None
    include_grocery: bool = False
    implied: frozenset[str] = frozenset()

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
                self.spicy is not None,
                self.only_offers,
                self.restaurant_id,
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
    if f.spicy is True and not meal.spicy_option:
        return False
    if f.only_offers and not meal.is_offer:
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
            return {
                "ask_about": "protein_type",
                "suggested_question_ar": "تبي لحم ولا دجاج ولا شي بحري؟",
            }
    if f.max_price is None:
        prices = [m.effective_price for m in results]
        if prices and (max(prices) - min(prices)) >= 30:
            return {
                "ask_about": "max_price",
                "suggested_question_ar": "كم ميزانيتك تقريبا؟",
            }
    if f.cuisine is None:
        cuisines = {m.cuisine for m in results}
        if len(cuisines) >= 3:
            return {
                "ask_about": "cuisine",
                "suggested_question_ar": "ودك بمطبخ معين، مثل سعودي أو شامي أو هندي؟",
            }
    if f.max_calories is None and f.min_protein is None:
        cals = [m.calories for m in results if m.calories]
        if cals and (max(cals) - min(cals)) >= 300:
            return {
                "ask_about": "max_calories",
                "suggested_question_ar": "تبيه خفيف ولا دسم؟",
            }
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
    n = int(round(value))
    if n < 0:
        return str(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] if unit == 0 else f"{_ONES[unit]} و{_TENS[tens]}"
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = _HUNDREDS[hundreds]
        return head if rest == 0 else f"{head} و{number_to_arabic_words(rest)}"
    thousands, rest = divmod(n, 1000)
    if thousands == 1:
        head = "ألف"
    elif thousands == 2:
        head = "ألفين"
    else:
        head = f"{_ONES[thousands]} آلاف"
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
        "price_speech_rule_ar": (
            "انطقي price_say_ar أو price_sentence_ar حرف بحرف. "
            "ممنوع تحولين price_sar بنفسك."
        ),
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
    if not meal.has_discount:
        facts["price_claim_rule_ar"] = (
            "ما فيه خصم ولا نسبة على هذا الصنف. السعر المعروض هو سعره العادي. "
            "ممنوع تقولين \"خصم\" أو \"وفّرت\" أو نسبة مئوية."
        )
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
    return {
        "serves_people": None,
        "serves_rule_ar": "ما مسجل عندنا يكفي كم شخص. لا تقولين رقم من عندك.",
    }


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
    spicy: Optional[bool] = None,
    only_offers: bool = False,
    restaurant_id: Optional[str] = None,
    prefer_restaurant_id: Optional[str] = None,
    serves: Optional[int] = None,
    include_grocery: bool = False,
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
        spicy=spicy,
        only_offers=bool(only_offers),
        restaurant_id=restaurant_id or None,
        prefer_restaurant_id=prefer_restaurant,
        include_grocery=bool(include_grocery),
        implied=implied,
    )

    matched = [m for m in cat.meals if _matches(m, filters)]

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
    dropped_guessed_filters: list[str] = []
    if tokens and not _name_matches(matched, tokens):
        relaxed = replace(
            filters, cuisine=None, protein_type=None, implied=filters.implied - _CATEGORY_HINTS
        )
        rescued = _name_matches([m for m in cat.meals if _matches(m, relaxed)], tokens)
        if rescued:
            dropped_guessed_filters = [
                label
                for label, value in (("cuisine", filters.cuisine), ("protein_type", filters.protein_type))
                if value
            ] or ["category"]
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
        result["intent_note_ar"] = (
            "المستخدم يبي أكل دسم (دهون/سعرات عالية)، مو صنف اسمه دسمة. "
            "رشحي أول نتيجة. ممنوع تقولين ما عندنا دسمة."
        )

    if ignored_restaurant:
        result["ignored_restaurant"] = ignored_restaurant
        result["note_ar"] = (
            "ما عرفت المطعم اللي حددتيه، فبحثت في كل المطاعم. استخدمي "
            "restaurant_id من نتيجة سابقة أو اسم المطعم بالعربي."
        )

    if serves_wanted:
        result["serves_requested"] = serves_wanted
        exact = [m for m in matched if m.serves == serves_wanted]
        if exact:
            result["serves_exact_available"] = True
            result["serves_pick_rule_ar"] = (
                f"فيه وجبة تكفي {serves_wanted} أشخاص بالضبط. رشحي أول نتيجة "
                f"(serves_people={serves_wanted}). ممنوع ترشحين وجبة لعدد أكبر "
                "وتبررين إنها تكفي وزيادة. إذا المستخدم أعاد عدد الأشخاص، "
                "ابحثي من جديد بنفس العدد."
            )
        if not any(m.serves >= serves_wanted for m in matched):
            # The requested headcount has no matching bundle. Saying so — and
            # offering multiples of the ordinary dish — is the answer the user
            # actually asked for; recommending a single-person combo instead is
            # the pizza-for-three failure from the live session.
            result["no_meal_serves_requested"] = True
            result["serves_advice_ar"] = (
                f"ما فيه وجبة مسجل إنها تكفي {serves_wanted} أشخاص من هذا "
                "الصنف. قولي بصراحة إن ما فيه عرض عائلي بهذا الحجم حاليا، "
                "واقترحي عليه ياخذ أكثر من وحدة من الصنف العادي (مثلا "
                "اثنتين أو ثلاث). لا ترشحين وجبة فردية وكأنها تكفيهم."
            )

    if dropped_guessed_filters:
        result["dropped_filters"] = dropped_guessed_filters
        result["note_ar"] = (
            "الصنف اللي سماه المستخدم موجود، بس تصنيفك له كان غلط، فتجاهلت "
            "الفلتر وجبت الصنف نفسه. رشحي أول نتيجة."
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
            result["no_results_advice"] = (
                f"الصنف \"{spoken_term}\" موجود في الكتالوج لكن مافيه ولا واحد "
                "يطابق الشروط الحالية. **ممنوع تقولين \"ما عندنا\"** — هذي جملة "
                "غلط وتخلي المستخدم يشوف الصنف قدامه على الشاشة. قولي جملة "
                "say_to_user_ar كما هي، وبعدها اعرضي عليه الصنف نفسه بسعره "
                "العادي من example_* أو اسأليه يخفف الشرط."
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
            result["no_results_advice"] = (
                f"ما فيه أي صنف اسمه \"{spoken_term}\". قولي الجملة اللي في "
                "say_to_user_ar بالضبط، ولا تسمين أي وجبة ثانية بهذا الاسم أبدا. "
                "بعدها اسأليه وش يبي بداله، أو ابحثي من جديد بنوع عام مثل دجاج "
                "أو مقرمش وسميها باسمها الحقيقي."
            )
        else:
            # Tell the model what to relax rather than letting it guess or invent.
            result["no_results_advice"] = (
                "ما فيه نتيجة بهذي الشروط. خففي شرط واحد فقط "
                "(السعرات أو السعر أو نوع البروتين) وجربي مرة ثانية."
            )
        return result

    hint = _next_question(matched, filters, total, len(top), named_dish)
    if hint:
        result["ambiguous"] = hint
    return result


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
    # Full menu rides along so سلمى can answer "وش عندهم؟" / "فيه مناقيش؟"
    # from the restaurant open itself. popular_meals alone hid مناقيش زعتر
    # and she told the user it did not exist while the page showed it.
    menu = sorted(meals, key=lambda m: (not m.is_popular, -m.rating, m.id))

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
        "menu": [
            {
                "meal_id": m.id,
                "name": m.name,
                "name_say_ar": speakable_name(m.name),
                "price_sar": round(m.effective_price, 2),
                "price_say_ar": sar_words(m.effective_price),
            }
            for m in menu
        ],
        "page_path": f"/restaurants/{restaurant_id}",
    }


# Pronunciation / tashkeel enforcement lives in src/spoken_lexicon.py.
# Re-exported here so existing imports (agent, tests, audit) keep working:
# PRONUNCIATION_FIXES, sanitize_tashkeel, fix_pronunciation, clean_for_speech,
# speakable_name.


def catalog_index(catalog: Optional[Catalog] = None) -> str:
    """A ~1KB "what exists" summary for the system prompt.

    Without it the model guesses restaurant names before its first search. With
    the whole 128KB catalog inlined instead, every turn would carry ~40k tokens
    and it would *still* invent meal ids — the search tool is what grounds it.
    """
    cat = catalog or load_catalog()
    lines = []
    for restaurant in cat.restaurants:
        meals = restaurant.get("meals") or []
        cuisine = restaurant.get("cuisine")
        lines.append(
            f"- {restaurant.get('name')} ({cuisine}) — {len(meals)} صنف، "
            f"توصيل {restaurant.get('deliveryTime')}"
        )
    return "\n".join(lines)
