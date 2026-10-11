"""Meal search: typed filters, then BM25 ranking over the catalog.

The model calls ``search_meals`` with a query string and typed filters. This
module turns that into results in three steps, each one explicit:

1. **Read the query.** Only the model's ``query`` argument is read, never the
   caller's transcript. Headcounts («لثلاثة أشخاص») become ``serves``, words
   in ``data/search_vocab.json`` become filters («خفيف» → healthy, «أرخص» →
   cheapest first) or a dish type («حلا» → desserts), and what remains are dish
   words.
2. **Filter.** Typed filters are hard: a dessert search never returns a burger.
   A filter that empties the result is reported (``exists_but_filtered``,
   ``cheapest_available``), never dropped silently.
3. **Rank.** Dish words are scored with BM25 over three fields (name,
   restaurant, description) with field boosts from ``CONFIG.search``. A result
   must contain every known dish word when any result can; otherwise partial
   matches are returned and flagged. Priors (main dish, light, headcount,
   rating) come after text relevance and are small by comparison. Ordering is
   deterministic: ties break on rating, then id.

Every response says how it matched (``matched_by``): ``name`` (the dish words
are in the top result's name), ``text`` (they are in its description or
restaurant), ``partial`` (only some of them matched), ``category`` (no dish
words, only filters) or ``none`` (nothing in the catalog carries these words;
``alternatives`` holds same-type suggestions, kept apart from ``results`` so a
substitute is never presented as the dish that was asked for).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

from src.catalog_text import normalize_ar
from src.config import CONFIG, BusinessRules, SearchWeights

if TYPE_CHECKING:  # pragma: no cover
    from src.catalog import Catalog, Meal

VOCAB_PATH = Path(__file__).resolve().parent.parent / "data" / "search_vocab.json"

DISH_TYPES = ("main", "side", "salad", "soup", "drink", "dessert", "grocery")
SORTS = (
    "relevance", "price_asc", "price_desc", "calories_asc", "calories_desc",
    "rating_desc", "protein_desc", "fat_desc", "fastest_delivery",
)
OFFER_KINDS = ("combo", "family", "deal")
# A kitchen's own name is a dish word only if at least this share of the
# kitchen's dishes carry it (see ``understand``).
KITCHEN_WORD_MIN_SHARE = 0.10
# Hints that narrow the result set. The others only re-rank or pick a sort.
_FILTER_HINTS = frozenset({"crispy", "keto", "cashback"})


# ─── Vocabulary ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Vocab:
    stopwords: frozenset[str]
    modifiers: frozenset[str]
    hints: dict[str, frozenset[str]]
    type_words: dict[str, frozenset[str]]
    protein_cues: dict[str, frozenset[str]]
    cuisine_cues: dict[str, frozenset[str]]
    open_frame_words: frozenset[str]
    recommend_words: frozenset[str] = frozenset()
    implies_type: dict[str, frozenset[str]] = field(default_factory=dict)
    secondary_markers: frozenset[str] = frozenset()

    @property
    def hint_words(self) -> frozenset[str]:
        return frozenset(w for words in self.hints.values() for w in words)

    def type_of_word(self, word: str) -> Optional[str]:
        for dish_type, words in self.type_words.items():
            if word in words:
                return dish_type
        return None


def _norm_set(words: Iterable[str]) -> frozenset[str]:
    return frozenset(n for n in (normalize_ar(w) for w in words) if n)


@lru_cache(maxsize=1)
def load_vocab(path: str | None = None) -> Vocab:
    raw = json.loads(Path(path or VOCAB_PATH).read_text(encoding="utf-8"))
    return Vocab(
        stopwords=_norm_set(raw.get("stopwords") or ()),
        modifiers=_norm_set(raw.get("modifiers") or ()),
        hints={k: _norm_set(v) for k, v in (raw.get("hints") or {}).items()},
        type_words={k: _norm_set(v) for k, v in (raw.get("type_words") or {}).items()},
        protein_cues={k: _norm_set(v) for k, v in (raw.get("protein_cues") or {}).items()},
        cuisine_cues={k: _norm_set(v) for k, v in (raw.get("cuisine_cues") or {}).items()},
        open_frame_words=_norm_set(raw.get("open_frame_words") or ()),
        recommend_words=_norm_set(raw.get("recommend_words") or ()),
        implies_type={k: _norm_set(v) for k, v in (raw.get("implies_type") or {}).items()},
        secondary_markers=_norm_set(raw.get("secondary_markers") or ()),
    )


# ─── Tokens ──────────────────────────────────────────────────────────────────

_SPLIT = re.compile(r"[^\w؀-ۿ]+")
_PREFIXES = ("وبال", "وال", "بال", "كال", "فال", "لل", "ال")


def words_of(text: str) -> list[str]:
    """Normalised words, punctuation dropped."""
    return [w.strip("؟?!.,،؛:") for w in _SPLIT.split(normalize_ar(text)) if w.strip("؟?!.,،؛:")]


def stem(word: str) -> str:
    """Light Arabic stem, identical on the index and the query side.

    Strips a glued article (البرجر → برجر, بالدجاج → دجاج), a plural «ات» when
    four letters remain (مشويات → مشوي, but دونات stays دونات) and a final «ه»
    from ة (سلطه → سلط, دجاجه → دجاج), keeping at least three letters. It never cuts into a word's root the way substring
    matching did («حلي» inside «الحليب»): matching is on whole stems.
    """
    w = word
    for prefix in _PREFIXES:
        if w.startswith(prefix) and len(w) - len(prefix) >= 3:
            w = w[len(prefix):]
            break
    if len(w) >= 6 and w.endswith("ات"):
        w = w[:-2]
    if len(w) >= 4 and w.endswith("ه"):
        w = w[:-1]
    return w


def stems_of(text: str) -> list[str]:
    return [stem(w) for w in words_of(text) if len(w) >= 2]


# ─── Index ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Doc:
    meal: "Meal"
    fields: dict[str, Counter]
    lengths: dict[str, int]
    all_terms: frozenset[str]
    name_terms: frozenset[str]
    # Name and description only: the dish's own words, not its restaurant's.
    dish_terms: frozenset[str]


@dataclass
class Index:
    docs: tuple[_Doc, ...]
    df: dict[str, int]
    avg_len: dict[str, float]
    by_id: dict[str, _Doc] = field(default_factory=dict)

    @property
    def vocabulary(self) -> frozenset[str]:
        return frozenset(self.df)

    def idf(self, term: str) -> float:
        n = len(self.docs)
        df = self.df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def _meal_fields(meal: "Meal") -> dict[str, list[str]]:
    return {
        "name": stems_of(meal.name),
        "restaurant": stems_of(f"{meal.restaurant_name} {meal.cuisine} {' '.join(meal.restaurant_tags)}"),
        "description": stems_of(f"{meal.description} {' '.join(meal.combo_includes)}"),
    }


def build_index(meals: Iterable["Meal"]) -> Index:
    docs: list[_Doc] = []
    df: Counter = Counter()
    totals: Counter = Counter()
    for meal in meals:
        raw = _meal_fields(meal)
        fields = {name: Counter(terms) for name, terms in raw.items()}
        lengths = {name: len(terms) for name, terms in raw.items()}
        all_terms = frozenset(t for terms in raw.values() for t in terms)
        df.update(all_terms)
        totals.update(lengths)
        docs.append(
            _Doc(meal, fields, lengths, all_terms, frozenset(raw["name"]), frozenset(raw["name"]) | frozenset(raw["description"]))
        )
    n = max(1, len(docs))
    avg = {name: totals[name] / n for name in ("name", "restaurant", "description")}
    index = Index(docs=tuple(docs), df=dict(df), avg_len=avg)
    index.by_id = {d.meal.id: d for d in docs}
    return index


def bm25(index: Index, doc: _Doc, terms: Iterable[str], weights: SearchWeights) -> float:
    """BM25F: field term counts are boosted, summed, then saturated once."""
    boosts = {
        "name": weights.field_name,
        "restaurant": weights.field_restaurant,
        "description": weights.field_description,
    }
    score = 0.0
    for term in terms:
        tf = 0.0
        for name, boost in boosts.items():
            count = doc.fields[name].get(term, 0)
            if not count:
                continue
            avg = index.avg_len[name] or 1.0
            norm = 1.0 - weights.bm25_b + weights.bm25_b * (doc.lengths[name] / avg)
            tf += boost * count / norm
        if tf:
            score += index.idf(term) * tf * (weights.bm25_k1 + 1.0) / (tf + weights.bm25_k1)
    return score


# ─── Request / response ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SearchRequest:
    """What the model asked for. Every field maps to a ``search_meals`` argument."""

    query: str = ""
    dish_type: Optional[str] = None
    cuisine: Optional[str] = None
    restaurant: Optional[str] = None
    prefer_restaurant: Optional[str] = None
    max_calories: Optional[int] = None
    min_protein: Optional[int] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    spicy: bool = False
    healthy: bool = False
    only_offers: bool = False
    offer_kind: Optional[str] = None
    cashback: bool = False
    serves: Optional[int] = None
    # The whole table's budget: a dish fits when as many as feed ``serves``
    # people cost no more (two trays for six at 100 each fit a 250 budget).
    group_budget: Optional[float] = None
    include_grocery: bool = False
    sort: str = "relevance"
    limit: Optional[int] = None


@dataclass
class Understood:
    """The query after reading it: what it filters on and what it names."""

    terms: list[str]
    # Dish words after «مع»: they rank a result up but are not required.
    secondary_terms: list[str]
    unknown_terms: list[str]
    corrected: dict[str, str]
    hints: frozenset[str]
    dish_type: Optional[str]
    protein: Optional[str]
    cuisine: Optional[str]
    serves: Optional[int]
    open_frame: bool
    sort: str
    # He asked her to recommend («وش ترشحين», «جوعان»), whatever else he said.
    asks_recommendation: bool = False
    # The kind of thing named, when it is not sold («بيبسي» → drink): picks
    # which alternatives to offer.
    implied_type: Optional[str] = None


@dataclass
class Filters:
    dish_type: Optional[str] = None
    restaurant_id: Optional[str] = None
    cuisine: Optional[str] = None
    protein: Optional[str] = None
    max_calories: Optional[int] = None
    min_protein: Optional[int] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    spicy: bool = False
    healthy: bool = False
    only_offers: bool = False
    offer_kinds: frozenset[str] = frozenset()
    cashback: bool = False
    tag_filters: frozenset[str] = frozenset()
    group_budget: Optional[float] = None
    group_size: Optional[int] = None
    include_grocery: bool = False
    # Category searches without a dish type are for food: a «وجبة صحية»
    # sorted by calories must not come back as Arabic coffee.
    exclude_types: frozenset[str] = frozenset()

    def applied(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v not in (None, False, frozenset(), "")}
        out.pop("include_grocery", None)
        out.pop("exclude_types", None)
        for key in ("offer_kinds", "tag_filters"):
            if key in out:
                out[key] = sorted(out[key])
        return out


def _fuzzy_term(term: str, vocabulary: Iterable[str], minimum: float = 0.8) -> Optional[str]:
    """Closest indexed stem for a word the catalog does not contain (ASR slips)."""
    if len(term) < 4:
        return None
    best, best_ratio = None, 0.0
    for candidate in vocabulary:
        if abs(len(candidate) - len(term)) > 2 or len(candidate) < 4:
            continue
        ratio = SequenceMatcher(None, term, candidate).ratio()
        if ratio > best_ratio or (ratio == best_ratio and best is not None and candidate < best):
            best, best_ratio = candidate, ratio
    return best if best_ratio >= minimum else None


def understand(req: SearchRequest, index: Index, vocab: Vocab) -> Understood:
    from src.catalog import _extract_headcount, repair_asr_query, resolve_cuisine

    text = repair_asr_query(req.query or "") or normalize_ar(req.query or "")
    text, serves_from_query = _extract_headcount(text)
    words = words_of(text)

    def in_set(word: str, pool: frozenset[str]) -> bool:
        return word in pool or stem(word) in {stem(p) for p in pool}

    # Multi-word hints («قليل السعرات») are matched on the whole text.
    hints = set()
    for tag, pool in vocab.hints.items():
        if any((" " in p and p in text) or (" " not in p and in_set(w, frozenset({p}))) for p in pool for w in words):
            hints.add(tag)
    protein_hits = [k for k, pool in vocab.protein_cues.items() if any(in_set(w, pool) for w in words) or any(" " in p and p in text for p in pool)]
    protein = protein_hits[0] if len(protein_hits) == 1 else None

    dish_type = req.dish_type if req.dish_type in DISH_TYPES else None
    hint_pool = vocab.hint_words
    primary_words: list[str] = []
    secondary_words: list[str] = []
    type_word_types: list[str] = []
    secondary = False
    for word in words:
        if word in vocab.secondary_markers:
            secondary = True
            continue
        if word in vocab.stopwords or word in vocab.modifiers or in_set(word, hint_pool):
            continue
        word_type = vocab.type_of_word(word) or vocab.type_of_word(stem(word))
        if word_type and not secondary:
            type_word_types.append(word_type)
            continue
        (secondary_words if secondary else primary_words).append(word)

    cuisine = resolve_cuisine(req.cuisine) or (req.cuisine or None)
    vocabulary = index.vocabulary
    corrected: dict[str, str] = {}
    unknown: list[str] = []

    def resolve_terms(pool: list[str]) -> list[str]:
        nonlocal cuisine
        out: list[str] = []
        for word in pool:
            term = stem(word)
            if term in vocabulary:
                out.append(term)
                continue
            kitchen = resolve_cuisine(word)
            if kitchen and not cuisine:
                # «أكلة مشويات»: a kitchen, not a missing dish.
                cuisine = kitchen
                continue
            fixed = _fuzzy_term(term, vocabulary)
            if fixed:
                corrected[word] = fixed
                out.append(fixed)
            else:
                unknown.append(word)
        return list(dict.fromkeys(out))

    terms = resolve_terms(primary_words)
    secondary_terms = [t for t in resolve_terms(secondary_words) if t not in terms]
    if cuisine and terms:
        # «فرايد تشيكن» with cuisine «فرايد تشيكن»: the words name the kitchen
        # already filtered on, not a dish inside it — few of its dishes carry
        # them. «برجر» with cuisine «برجر» is different: 40% of burger-kitchen
        # dishes are named برجر, so the word still picks dishes.
        kitchen = set(stems_of(cuisine)) | {stem(c) for c in vocab.cuisine_cues.get(cuisine, ())}
        kitchen_terms = [t for t in terms if t in kitchen]
        if kitchen_terms:
            pool = [d for d in index.docs if normalize_ar(cuisine) in normalize_ar(d.meal.cuisine)]
            named = sum(1 for d in pool if all(t in d.dish_terms for t in kitchen_terms))
            if pool and named / len(pool) < KITCHEN_WORD_MIN_SHARE:
                terms = [t for t in terms if t not in kitchen_terms]
    if type_word_types:
        if dish_type is None and not terms:
            # «سلطة», «حلا»: the type word is the whole request.
            dish_type = type_word_types[0]
        elif dish_type is None or terms:
            # «سلطة دجاج»: the type word is part of the dish name.
            terms = list(dict.fromkeys([stem(w) for w in words if (vocab.type_of_word(w) or vocab.type_of_word(stem(w))) and stem(w) in vocabulary] + terms))
    if not terms and secondary_terms:
        terms, secondary_terms = secondary_terms, []
    implied_type = None
    for kind, pool in vocab.implies_type.items():
        if any(in_set(w, pool) for w in words) or any(" " in p and p in text for p in pool):
            implied_type = kind
            break

    sort = req.sort if req.sort in SORTS else "relevance"
    if sort == "relevance":
        if "high_protein" in hints:
            sort = "protein_desc"
        elif "rich" in hints:
            sort = "fat_desc"
        elif "cheap" in hints:
            sort = "price_asc"
        elif "expensive" in hints:
            sort = "price_desc"
    open_frame = bool(set(words) & vocab.open_frame_words)
    asks_recommendation = bool(set(words) & vocab.recommend_words)
    return Understood(
        terms=terms,
        secondary_terms=secondary_terms,
        unknown_terms=unknown,
        corrected=corrected,
        hints=frozenset(hints),
        dish_type=dish_type,
        protein=protein,
        cuisine=cuisine,
        serves=req.serves or serves_from_query,
        open_frame=open_frame,
        sort=sort,
        asks_recommendation=asks_recommendation,
        implied_type=implied_type,
    )


def _filters(req: SearchRequest, u: Understood, rules: BusinessRules, catalog: "Catalog") -> tuple[Filters, list[str]]:
    from src.catalog import resolve_restaurant

    ignored: list[str] = []
    restaurant_id = None
    if req.restaurant:
        restaurant_id = resolve_restaurant(req.restaurant, catalog)
        if restaurant_id is None:
            ignored.append("restaurant")
    kinds = {k.strip().lower() for k in re.split(r"[,+|/\s]+", req.offer_kind or "") if k.strip().lower() in OFFER_KINDS}
    kinds |= {k for k in ("combo", "family", "deal") if k in u.hints}
    healthy = bool(req.healthy or "light" in u.hints)
    max_calories = req.max_calories or None
    if healthy and max_calories is None:
        max_calories = rules.health_max_kcal_per_person
    min_protein = req.min_protein or None
    if "high_protein" in u.hints and min_protein is None:
        min_protein = rules.high_protein_min
    cuisine = u.cuisine
    if cuisine and not any(normalize_ar(cuisine) in normalize_ar(m.cuisine) for m in catalog.meals):
        ignored.append("cuisine")
        cuisine = None
    tag_filters = {t for t in _FILTER_HINTS if t in u.hints}
    return Filters(
        dish_type=u.dish_type,
        restaurant_id=restaurant_id,
        cuisine=cuisine,
        protein=u.protein,
        max_calories=max_calories,
        min_protein=min_protein,
        max_price=req.max_price or None,
        min_price=req.min_price or None,
        spicy=bool(req.spicy or "spicy_available" in u.hints),
        healthy=healthy,
        only_offers=bool(req.only_offers or kinds),
        offer_kinds=frozenset(kinds),
        cashback=bool(req.cashback or "cashback" in u.hints),
        tag_filters=frozenset(tag_filters - {"cashback"}),
        group_budget=req.group_budget or None,
        group_size=u.serves if req.group_budget else None,
        include_grocery=bool(req.include_grocery or u.dish_type == "grocery"),
        exclude_types=(
            frozenset({"drink", "dessert", "side", "soup"})
            if not (u.dish_type or u.terms or cuisine or restaurant_id)
            else frozenset()
        ),
    ), ignored


def quantity_for(meal: "Meal", people: Optional[int]) -> int:
    """How many of this dish feed ``people``."""
    return max(1, math.ceil((people or 1) / (meal.serves or 1)))


def passes(meal: "Meal", f: Filters) -> bool:
    if not f.include_grocery and meal.type == "grocery":
        return False
    if f.dish_type and meal.type != f.dish_type:
        return False
    if meal.type in f.exclude_types:
        return False
    if f.restaurant_id and meal.restaurant_id != f.restaurant_id:
        return False
    if f.cuisine and normalize_ar(f.cuisine) not in normalize_ar(meal.cuisine):
        return False
    if f.protein and f.protein not in meal.protein_tags:
        return False
    if f.max_calories is not None:
        per_person = meal.calories / max(1, meal.serves or 1)
        if not (0 < meal.calories <= f.max_calories or 0 < per_person <= f.max_calories):
            return False
    if f.healthy and "rich" in meal.diet_tags and "light" not in meal.diet_tags:
        return False
    if f.min_protein is not None and meal.protein < f.min_protein:
        return False
    if f.max_price is not None and meal.effective_price > f.max_price:
        return False
    if f.group_budget is not None and quantity_for(meal, f.group_size) * meal.effective_price > f.group_budget:
        return False
    if f.min_price is not None and meal.effective_price < f.min_price:
        return False
    if f.spicy and not meal.spicy_option:
        return False
    if f.only_offers and not meal.is_offer:
        return False
    if f.offer_kinds:
        kind = meal.offer_kind or ("combo" if meal.is_combo else "")
        if kind not in f.offer_kinds:
            return False
    if f.cashback and not meal.cashback_percent:
        return False
    for tag in f.tag_filters:
        if tag not in meal.diet_tags:
            return False
    return True


@dataclass
class _Scored:
    doc: _Doc
    text: float
    score: float
    covers_all: bool
    name_all: bool
    dish_all: bool = False


def _priors(meal: "Meal", f: Filters, u: Understood, req: SearchRequest, w: SearchWeights, prefer_id: Optional[str]) -> float:
    score = w.rating * meal.rating + (w.popular if meal.is_popular else 0.0)
    if not f.dish_type and meal.type == "main":
        score += w.main_dish
    if f.healthy and "light" in meal.diet_tags:
        score += w.light_match
    if u.serves and u.serves > 1:
        if meal.serves == u.serves:
            score += w.serves_exact
        elif meal.serves and meal.serves > u.serves:
            score += max(0.0, w.serves_larger - w.serves_overshoot * (meal.serves - u.serves))
        else:
            # A single dish (or an unstated size) for a group: they can order
            # several, but a platter that feeds them ranks first.
            score += w.serves_smaller
    elif u.serves == 1 and meal.serves and meal.serves > 1:
        score += w.serves_smaller
    if prefer_id and meal.restaurant_id == prefer_id:
        score += w.open_restaurant
    return score


def _per_person(meal: "Meal", value: float) -> float:
    return value / max(1, meal.serves or 1)


# Nutrition sorts compare one person's share: a platter for six is not the
# highest-protein dish because it feeds six.
# Without a group, compare whole dishes: a platter for six is not "the
# lightest meal" for one caller.
# Protein and fat always compare one person's share.
_ABSOLUTE_SORT_KEYS = {
    "calories_asc": lambda s: (s.doc.meal.calories or 10**6,),
    "calories_desc": lambda s: (-(s.doc.meal.calories or 0),),
}

_SORT_KEYS = {
    "calories_asc": lambda s: (_per_person(s.doc.meal, s.doc.meal.calories) or 10**6,),
    "calories_desc": lambda s: (-_per_person(s.doc.meal, s.doc.meal.calories),),
    "price_asc": lambda s: (s.doc.meal.effective_price,),
    "price_desc": lambda s: (-s.doc.meal.effective_price,),
    "rating_desc": lambda s: (-s.doc.meal.rating,),
    "protein_desc": lambda s: (-_per_person(s.doc.meal, s.doc.meal.protein),),
    "fat_desc": lambda s: (-_per_person(s.doc.meal, s.doc.meal.fat),),
    "fastest_delivery": lambda s: (_delivery_rank(s.doc.meal),),
}


def _delivery_rank(meal: "Meal") -> tuple[int, int, int]:
    """Express restaurants first, then the shortest delivery window."""
    from src.catalog import delivery_minutes

    window = delivery_minutes(meal) or (10**3, 10**3)
    return (0 if meal.fast_delivery else 1, window[1], window[0])


def _order(rows: list[_Scored], sort: str, per_person: bool = False) -> list[_Scored]:
    head = _SORT_KEYS.get(sort, lambda s: ())
    if not per_person and sort in _ABSOLUTE_SORT_KEYS:
        head = _ABSOLUTE_SORT_KEYS[sort]
    return sorted(rows, key=lambda s: (*head(s), -round(s.score, 9), -s.doc.meal.rating, s.doc.meal.id))


def row(meal: "Meal", *, u: Optional[Understood] = None, f: Optional[Filters] = None, match: str = "") -> dict[str, Any]:
    """One result, compact: what she needs to name, price and pick it."""
    from src.catalog import number_to_arabic_words, sar_words, speakable_name

    out: dict[str, Any] = {
        "meal_id": meal.id,
        "name": meal.name,
        "name_say_ar": speakable_name(meal.name),
        "restaurant_say_ar": speakable_name(meal.restaurant_name),
        "type": meal.type,
        "price_sar": round(meal.effective_price, 2),
        "price_say_ar": sar_words(meal.effective_price),
        "calories": meal.calories,
        "calories_say_ar": number_to_arabic_words(meal.calories),
    }
    if meal.serves:
        out["serves_people"] = meal.serves
    if u is not None and u.serves and u.serves > (meal.serves or 1):
        # One is not enough for the group: how many feed them all, and for how much.
        out["quantity_for_group"] = quantity_for(meal, u.serves)
        out["group_total_say_ar"] = sar_words(out["quantity_for_group"] * meal.effective_price)
    if meal.is_offer:
        out["offer_kind"] = meal.offer_kind or "deal"
    if meal.has_discount:
        out["has_discount"] = True
    if meal.cashback_percent:
        out["cashback_percent"] = meal.cashback_percent
    if u is not None and u.sort == "fastest_delivery":
        from src.catalog import delivery_minutes_say_ar

        out["delivery_minutes_say_ar"] = delivery_minutes_say_ar(meal)
        if meal.fast_delivery:
            out["fast_delivery"] = True
    wants_protein = f is not None and (f.min_protein is not None) or (u is not None and u.sort == "protein_desc")
    if wants_protein:
        out["protein_g"] = meal.protein
        out["protein_say_ar"] = number_to_arabic_words(meal.protein)
    if match:
        out["match"] = match
    return out


def brief_row(meal: "Meal") -> dict[str, Any]:
    """Just enough to name, price and open a dish."""
    from src.catalog import sar_words, speakable_name

    out = {"meal_id": meal.id, "name_say_ar": speakable_name(meal.name), "price_say_ar": sar_words(meal.effective_price)}
    if meal.serves:
        out["serves_people"] = meal.serves
    return out


def _alternatives(catalog: "Catalog", dish_type: Optional[str], prefer_id: Optional[str], limit: int) -> list["Meal"]:
    kind = dish_type or "main"
    pool = [m for m in catalog.meals if m.type == kind]
    pool.sort(key=lambda m: (bool(prefer_id) and m.restaurant_id != prefer_id, not m.is_popular, -m.rating, m.id))
    return pool[:limit]


_PROTEIN_AR = {"meat": "لحم", "chicken": "دجاج", "seafood": "بحري", "vegetarian": "نباتي"}


def _taste_known(f: Filters) -> bool:
    """Whether the request already says what kind of food he wants.

    A budget, a headcount, an offer or cashback says how much, not what
    («عزيمة» asks for a family offer, and the model adds combo on its own);
    everything here is a taste, and a search that has one is narrow enough to
    recommend from.
    """
    return bool(
        f.cuisine or f.restaurant_id or f.protein or f.healthy or f.spicy
        or f.tag_filters or f.max_calories or f.min_protein
        or (f.dish_type and f.dish_type != "main")
    )


def _top(values: Iterable[str], n: int = 3) -> list[str]:
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    return [v for v, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _taste_options(meals: list["Meal"]) -> list[str]:
    """Two kinds of food to offer when asking his taste, from what the results hold.

    Meat and chicken is how people choose; seafood or «نباتي» only fill a gap.
    Falls back to the two commonest cuisines.
    """
    present = {t for m in meals for t in m.protein_tags}
    proteins = [p for p in ("meat", "chicken", "seafood", "vegetarian") if p in present][:2]
    if len(proteins) == 2:
        return [_PROTEIN_AR[p] for p in proteins]
    cuisines = _top((m.cuisine for m in meals), n=2)
    return cuisines if len(cuisines) == 2 else []


def _discovery_facts(meals: list["Meal"], u: Understood, f: Filters, total: int, rules: BusinessRules) -> dict[str, Any]:
    """What search_meals needs to run the taste / people / budget questions.

    ``taste_given``: the request names a dish or a taste. ``asks_recommendation``:
    he asked her to recommend. ``open_request``: he asked for food without
    naming a dish. ``too_open``: no dish, no taste and many matches, so a pick
    would be a guess. The tool pops all of it; the model
    only ever sees the question it builds.
    """
    taste_given = bool(u.terms) or _taste_known(f)
    facts: dict[str, Any] = {
        "taste_given": taste_given,
        "asks_recommendation": u.asks_recommendation,
        # «أبي شي صحي», «أبي عشا»: food asked for, no dish named.
        "open_request": u.open_frame and not u.terms,
        "too_open": not taste_given and total >= rules.ambiguity_min_matches,
    }
    if not taste_given:
        facts["taste_options_say_ar"] = _taste_options(meals)
    return facts


def search(
    req: SearchRequest,
    catalog: "Catalog",
    *,
    weights: SearchWeights | None = None,
    rules: BusinessRules | None = None,
    vocab: Vocab | None = None,
) -> dict[str, Any]:
    from src.catalog import resolve_restaurant

    weights = weights or CONFIG.search
    rules = rules or CONFIG.rules
    vocab = vocab or load_vocab()
    index = catalog.index
    u = understand(req, index, vocab)
    f, ignored = _filters(req, u, rules, catalog)
    limit = max(1, min(int(req.limit or rules.search_default_limit), rules.search_max_limit))
    prefer_id = resolve_restaurant(req.prefer_restaurant, catalog) if req.prefer_restaurant else None

    result: dict[str, Any] = {"matched_by": "category", "total_matches": 0, "results": []}
    if u.terms:
        result["query_terms"] = u.terms
    if u.corrected:
        result["corrected_terms"] = u.corrected
    if u.dish_type:
        result["dish_type"] = u.dish_type
    if u.serves:
        result["serves_requested"] = u.serves
    applied = f.applied()
    if applied:
        result["filters"] = applied
    if ignored:
        result["ignored_filters"] = ignored
    if u.sort != "relevance":
        result["sort"] = u.sort

    # Words nothing in the catalog carries, and nothing else to go on: a dish
    # we do not sell. An open request («اقترحي لي شي») is a category search.
    has_other_signal = bool(u.dish_type or u.hints or u.serves or f.applied())
    if u.unknown_terms and not u.terms and not u.open_frame and not has_other_signal:
        term = " ".join(u.unknown_terms)
        result["matched_by"] = "none"
        result["not_found"] = term
        alts = _alternatives(catalog, u.dish_type or u.implied_type, prefer_id, 3)
        if alts:
            result["alternatives"] = [row(m, u=u, f=f) for m in alts]
        return result
    if u.unknown_terms:
        result["ignored_terms"] = u.unknown_terms

    candidates = [d for d in index.docs if passes(d.meal, f)]
    scored = _score(candidates, u, f, req, weights, prefer_id, index)
    if f.group_budget is not None and u.terms and not any(s.dish_all for s in scored):
        # Mashawi for ten does not fit 300 riyals. What he asked for, marked
        # over budget, beats a cheap dish that only shares a word with it.
        loose = replace(f, group_budget=None)
        loose_scored = _score([d for d in index.docs if passes(d.meal, loose)], u, loose, req, weights, prefer_id, index)
        if any(s.dish_all for s in loose_scored):
            f, scored = loose, loose_scored
            result["over_budget"] = True

    if u.terms and scored:
        # Every dish word in the dish's own name or description first; only
        # when nothing qualifies do restaurant-name matches count.
        covering = [s for s in scored if s.dish_all] or [s for s in scored if s.covers_all]
        if covering:
            scored = covering
        elif _covering_anywhere(index, u, f):
            # «كبة مقلية» among offers: the dish exists, just not as an
            # offer. Fried shrimp deals are not a partial answer to that.
            scored = []
        else:
            present = {t for s in scored for t in u.terms if t in s.doc.all_terms}
            result["missing_terms"] = [t for t in u.terms if t not in present] or u.terms[1:]

    if not scored:
        _explain_empty(result, req, u, f, catalog, index, weights, prefer_id)
        result["discovery"] = _discovery_facts([], u, f, 0, rules)
        return result

    ordered = _order(scored, u.sort, per_person=bool(u.serves and u.serves > 1))
    if u.serves and u.serves > 1:
        ordered = _serves_first(ordered, u.serves, result, f, catalog, limit, weights, prefer_id)
    top = ordered[:limit]
    if u.terms:
        best = top[0]
        if best.name_all:
            result["matched_by"] = "name"
        elif best.covers_all:
            result["matched_by"] = "text"
        else:
            result["matched_by"] = "partial"
    result["total_matches"] = len(ordered)
    result["results"] = [
        row(s.doc.meal, u=u, f=f, match=("name" if s.name_all else "text" if s.covers_all else "partial") if u.terms else "")
        for s in top
    ]
    result["discovery"] = _discovery_facts([s.doc.meal for s in ordered[:60]], u, f, len(ordered), rules)
    return result


def _score(docs: list[_Doc], u: Understood, f: Filters, req: SearchRequest, w: SearchWeights, prefer_id: Optional[str], index: Index) -> list[_Scored]:
    rows: list[_Scored] = []
    texts = [
        (bm25(index, d, u.terms, w) + 0.5 * bm25(index, d, u.secondary_terms, w)) if u.terms else 0.0
        for d in docs
    ]
    top_text = max(texts, default=0.0) or 1.0
    for doc, text in zip(docs, texts):
        if u.terms and not any(t in doc.all_terms for t in u.terms):
            continue
        covers_all = all(t in doc.all_terms for t in u.terms)
        name_all = bool(u.terms) and all(t in doc.name_terms for t in u.terms)
        dish_all = bool(u.terms) and all(t in doc.dish_terms for t in u.terms)
        score = w.text * (text / top_text) + _priors(doc.meal, f, u, req, w, prefer_id)
        rows.append(_Scored(doc, text, score, covers_all, name_all, dish_all))
    return rows


def _serves_first(ordered: list[_Scored], serves: int, result: dict[str, Any], f: Filters, catalog: "Catalog", limit: int, w: SearchWeights, prefer_id: Optional[str]) -> list[_Scored]:
    exact = [s for s in ordered if s.doc.meal.serves == serves]
    if exact:
        result["serves_exact_available"] = True
        return exact + [s for s in ordered if s.doc.meal.serves != serves]
    result["serves_exact_available"] = False
    larger = [s.doc.meal for s in ordered if s.doc.meal.serves and s.doc.meal.serves > serves]
    if larger:
        nearest = min(larger, key=lambda m: (m.serves, m.effective_price, m.id))
        result["nearest_larger"] = brief_row(nearest)
    offers = [
        m for m in catalog.meals
        if m.serves == serves and m.is_offer and passes(m, replace(f, only_offers=False, offer_kinds=frozenset(), dish_type=f.dish_type, protein=None, cuisine=None))
    ]
    offers.sort(key=lambda m: (not m.is_popular, -m.rating, m.id))
    if offers:
        result["offers_for_serves"] = [brief_row(m) for m in offers[:3]]
    if not larger:
        # Nothing feeds them all: the biggest trays come closest (stable, so
        # relevance still orders dishes of the same size).
        return sorted(ordered, key=lambda s: -(s.doc.meal.serves or 1))
    return ordered


def _covering_anywhere(index: Index, u: Understood, f: Filters) -> list[_Doc]:
    """Dishes anywhere in the catalog that carry every dish word."""
    return [
        d for d in index.docs
        if all(t in d.dish_terms for t in u.terms) and (f.include_grocery or d.meal.type != "grocery")
    ]


def _explain_empty(result: dict[str, Any], req: SearchRequest, u: Understood, f: Filters, catalog: "Catalog", index: Index, w: SearchWeights, prefer_id: Optional[str]) -> None:
    """Nothing passed the filters. Say which filter did it, with what exists."""
    result["matched_by"] = "none" if u.terms else "category"
    relaxed_price = replace(f, max_price=None, min_price=None, group_budget=None)
    if f.max_price is not None or f.min_price is not None or f.group_budget is not None:
        pool = [d for d in index.docs if passes(d.meal, relaxed_price)]
        pool = _score(pool, u, relaxed_price, req, w, prefer_id, index) if u.terms else [
            _Scored(d, 0.0, 0.0, True, False) for d in pool
        ]
        if pool:
            if f.max_price is not None or f.group_budget is not None:
                cheapest = min(pool, key=lambda s: (s.doc.meal.effective_price, s.doc.meal.id)).doc.meal
                result["cheapest_available"] = row(cheapest)
            if f.min_price is not None:
                priciest = max(pool, key=lambda s: (s.doc.meal.effective_price, s.doc.meal.id)).doc.meal
                result["priciest_available"] = row(priciest)
            return
    if u.terms:
        anywhere = _covering_anywhere(index, u, f)
        if anywhere:
            example = min(anywhere, key=lambda d: (d.meal.effective_price, d.meal.id)).meal
            result["exists_but_filtered"] = {"count": len(anywhere), "example": row(example)}
            return
        result["not_found"] = " ".join(u.terms)
        alts = _alternatives(catalog, f.dish_type or u.implied_type, prefer_id, 3)
        if alts:
            result["alternatives"] = [row(m) for m in alts]
        return
    result["filters_too_strict"] = True
