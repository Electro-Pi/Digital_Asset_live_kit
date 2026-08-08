"""In-memory search over the static Jahez restaurant/meal catalog.

The catalog is 11 restaurants / 110 meals / ~128KB, so it is loaded once per
process and filtered with plain Python. There is deliberately no service, no
database and no network hop here: a round trip to fetch a 128KB dict would cost
latency on a live voice call and buy nothing. When the catalog outgrows the
process (its own write path, its own scaling), the bodies of these functions
change — the tools that call them do not.

Three things in here are less obvious than they look:

* **Text is normalized before matching.** The لقمة prompt makes the LLM write
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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

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

# Cuisines that are not "a meal" in the sense لقمة recommends.
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
    "chicken": ("دجاج", "دجاجه", "تشيكن", "فراخ", "مظبي"),
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

_DRINK_KEYWORDS = (
    "قهوه", "عصير", "ميلك شيك", "سموذي", "لاسي", "لبن", "مياه", "حليب", "شاي",
)

_DESSERT_CUISINES = {"حلويات"}

# Thresholds used for derived tags and for the "why this meal" reasons. They are
# named because they appear in both filtering and explanation, and drifting them
# apart would make لقمة justify a pick with a rule it did not actually use.
LIGHT_CALORIES_MAX = 400
HIGH_PROTEIN_MIN = 30
FAMILY_KEYWORDS = ("عائلي", "عائليه", "يكفي", "كبير")


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Match a keyword only at the start of a word, allowing Arabic prefixes.

    Plain substring matching mis-tags food: "ريش" (ribs) is inside "جريش", so
    the Saudi wheat dish came back as meat. A bare ``\\b`` is too strict in the
    other direction, because Arabic glues its articles and prepositions on —
    "باللحمة" and "بالروبيان" have to keep matching لحم and روبيان. So the
    keyword may be preceded by the definite article and/or a single-letter
    prefix, but nothing else. Suffixes stay free (لحمة still matches لحم).
    """
    return re.compile(rf"(?<!\w)[وبفلك]?(?:ال)?{re.escape(keyword)}")


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
    def page_path(self) -> str:
        """Frontend route for this meal (app/meals/[id]/page.tsx)."""
        return f"/meals/{self.id}"


def _derive_protein_tags(blob: str) -> frozenset[str]:
    tags = {
        tag
        for tag, keywords in _PROTEIN_KEYWORDS.items()
        if _has_keyword(blob, keywords)
    }
    if not tags and _has_keyword(blob, _VEGETARIAN_KEYWORDS):
        tags.add("vegetarian")
    return frozenset(tags)


def _derive_diet_tags(meal_json: dict[str, Any], cuisine: str, meal_blob: str) -> frozenset[str]:
    tags: set[str] = set()
    calories = int(meal_json.get("calories") or 0)
    protein = int(meal_json.get("protein") or 0)

    if 0 < calories <= LIGHT_CALORIES_MAX:
        tags.add("light")
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
    if cuisine in _DESSERT_CUISINES:
        tags.add("dessert")
    if cuisine in GROCERY_CUISINES:
        tags.add("grocery")
    if _has_keyword(meal_blob, _DRINK_KEYWORDS):
        tags.add("drink")
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
    meal_blob = normalize_ar(f"{name} {description}")
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
        protein_tags=_derive_protein_tags(meal_blob),
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
    "high_protein": ("بروتين", "بروتينات", "مشبع"),
    "spicy_available": ("حار", "حاره", "سبايسي", "حريف"),
    "offer": ("عرض", "عروض", "خصم", "رخيص", "ارخص"),
    "family": ("عائله", "عائلي", "عزيمه", "ضيوف", "جماعه"),
    "dessert": ("حلا", "حلو", "حلويات", "سكر"),
}

_STOPWORDS = {
    "ابغى", "ابي", "اريد", "وجبه", "اكل", "شي", "شيء", "من", "في", "على", "مع",
    "او", "و", "لي", "لك", "الي", "هذا", "هذي", "بس", "كثير", "مره", "زين",
}


def resolve_restaurant(value: Optional[str], catalog: Optional[Catalog] = None) -> Optional[str]:
    """Resolve an Arabic name or a (possibly mangled) id to a real restaurant id.

    This exists because of a live failure. The model was given a restaurant
    filter but never given any real ids, so it invented them from the names it
    could see: ``aseel-burger-id`` for ``aseel-burger``, ``mazaq_alsham_id`` for
    ``madhaq-alsham``. The filter then matched nothing, search returned zero,
    and لقمة told the user there was a burger it could not open — the search had
    silently excluded every meal.

    So: accept what the model actually has (the Arabic name), tolerate the id
    shapes it invents, and return ``None`` rather than a wrong match. The caller
    drops an unresolvable filter instead of returning an empty result.
    """
    if not value:
        return None
    cat = catalog or load_catalog()

    raw = normalize_ar(value).replace("_", "-").strip()
    trimmed = raw[:-3].rstrip("-") if raw.endswith("-id") else raw

    for candidate in (raw, trimmed):
        for restaurant in cat.restaurants:
            if normalize_ar(restaurant.get("id")) == candidate:
                return str(restaurant["id"])

    # Arabic name, which is what the prompt's index actually shows the model.
    for restaurant in cat.restaurants:
        name = normalize_ar(restaurant.get("name"))
        if name and (name == raw or raw in name or name in raw):
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
    return frozenset(
        tag
        for tag, words in _QUERY_HINTS.items()
        if any(w in query_norm for w in words)
    )


def _tokens(query_norm: str) -> list[str]:
    return [
        t for t in re.split(r"[^\w؀-ۿ]+", query_norm)
        if len(t) > 1 and t not in _STOPWORDS
    ]


def _score(meal: Meal, tokens: Iterable[str], implied: frozenset[str]) -> float:
    score = 0.0
    name_norm = normalize_ar(meal.name)
    desc_norm = normalize_ar(meal.description)
    cuisine_norm = normalize_ar(meal.cuisine)
    restaurant_norm = normalize_ar(meal.restaurant_name)

    for token in tokens:
        if token in name_norm:
            score += 3.0
        elif token in cuisine_norm or token in restaurant_norm:
            score += 2.0
        elif token in desc_norm:
            score += 1.0
        elif token in meal.search_blob:
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
    return True


_PROTEIN_LABEL_AR = {
    "meat": "لحم",
    "chicken": "دجاج",
    "seafood": "بحري",
    "vegetarian": "نباتي",
}


def _why(meal: Meal, f: SearchFilters) -> list[str]:
    """Grounded reasons for this pick — numbers only, no adjectives invented.

    These exist so لقمة explains a choice from catalog facts instead of making
    up a rationale. Each entry is a short Arabic phrase the model can speak.
    """
    reasons: list[str] = []
    wants_light = f.max_calories is not None or "light" in f.implied
    wants_protein = f.min_protein is not None or "high_protein" in f.implied

    if wants_light and meal.calories:
        reasons.append(f"سعراته {meal.calories} فقط")
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
AMBIGUITY_MIN_MATCHES = 8


def _next_question(results: list[Meal], f: SearchFilters, total: int, shown: int) -> Optional[dict[str, str]]:
    """Which single unset filter would cut the result set down the most.

    Returned as a hint, not a script: the point is that لقمة asks a question
    because the answer is genuinely ambiguous, not because a checklist says the
    third question comes now. ``None`` means just recommend.
    """
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


def meal_summary(meal: Meal, f: Optional[SearchFilters] = None) -> dict[str, Any]:
    """Compact result row. Deliberately small — this goes into an LLM turn."""
    row: dict[str, Any] = {
        "meal_id": meal.id,
        "name": meal.name,
        "restaurant": meal.restaurant_name,
        "restaurant_id": meal.restaurant_id,
        "cuisine": meal.cuisine,
        "price_sar": round(meal.effective_price, 2),
        "price_say_ar": sar_words(meal.effective_price),
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        "protein_g": meal.protein,
        "rating": meal.rating,
        "spicy_available": meal.spicy_option,
        "is_offer": meal.is_offer,
        "kind": sorted(meal.protein_tags) or ["unspecified"],
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
    include_grocery: bool = False,
    sort: str = "relevance",
    limit: int = 3,
    catalog: Optional[Catalog] = None,
) -> dict[str, Any]:
    """Filter + rank meals. Returns a small dict shaped for a voice turn.

    Every filter is optional on purpose. Searching with one filter and reporting
    ``total_matches`` is what lets the agent ask a question *because* the answer
    is ambiguous, instead of interrogating the user before its first search.
    """
    cat = catalog or load_catalog()
    query_norm = normalize_ar(query)
    implied = _implied_tags(query_norm)

    # An unrecognised restaurant must never silently empty the result set — that
    # is exactly how "there is a burger but I cannot open it" happened. Drop the
    # filter, search everything else, and report what was ignored.
    ignored_restaurant: Optional[str] = None
    if restaurant_id:
        resolved = resolve_restaurant(restaurant_id, cat)
        if resolved is None:
            ignored_restaurant = restaurant_id
        restaurant_id = resolved

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
        include_grocery=bool(include_grocery),
        implied=implied,
    )

    matched = [m for m in cat.meals if _matches(m, filters)]

    tokens = _tokens(query_norm)
    if tokens:
        # Free text should narrow, but only when it actually hits something —
        # a query of "شي خفيف" is all stopwords/hints and must not zero out.
        scored = [(m, _score(m, tokens, implied)) for m in matched]
        hits = [(m, s) for m, s in scored if s >= 1.0]
        if hits:
            scored = hits
    else:
        scored = [(m, _score(m, [], implied)) for m in matched]

    matched = [m for m, _ in scored]
    limit = max(1, min(int(limit or 3), 5))

    sort_keys = {
        "relevance": lambda pair: (-pair[1], pair[0].id),
        "calories_asc": lambda pair: (pair[0].calories or 10**6, pair[0].id),
        "price_asc": lambda pair: (pair[0].effective_price, pair[0].id),
        "rating_desc": lambda pair: (-pair[0].rating, pair[0].id),
        "protein_desc": lambda pair: (-pair[0].protein, pair[0].id),
    }
    scored.sort(key=sort_keys.get(sort, sort_keys["relevance"]))

    top = [m for m, _ in scored[:limit]]
    total = len(matched)

    result: dict[str, Any] = {
        "total_matches": total,
        "showing": len(top),
        "results": [meal_summary(m, filters) for m in top],
    }

    if ignored_restaurant:
        result["ignored_restaurant"] = ignored_restaurant
        result["note_ar"] = (
            "ما عرفت المطعم اللي حددتيه، فبحثت في كل المطاعم. استخدمي "
            "restaurant_id من نتيجة سابقة أو اسم المطعم بالعربي."
        )

    if total == 0:
        # Tell the model what to relax rather than letting it guess or invent.
        result["no_results_advice"] = (
            "ما فيه نتيجة بهذي الشروط. خففي شرط واحد فقط "
            "(السعرات أو السعر أو نوع البروتين) وجربي مرة ثانية."
        )
        return result

    hint = _next_question(matched, filters, total, len(top))
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
        "description": meal.description,
        "restaurant": meal.restaurant_name,
        "restaurant_id": meal.restaurant_id,
        "cuisine": meal.cuisine,
        "price_sar": round(meal.effective_price, 2),
        "price_say_ar": sar_words(meal.effective_price),
        "base_price_sar": round(meal.price, 2),
        "is_offer": meal.is_offer,
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        "protein_g": meal.protein,
        "carbs_g": meal.carbs,
        "fat_g": meal.fat,
        "rating": meal.rating,
        "spicy_available": meal.spicy_option,
        "kind": sorted(meal.protein_tags) or ["unspecified"],
        "addons": [
            {
                "addon_id": a.get("id"),
                "name": a.get("name"),
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
