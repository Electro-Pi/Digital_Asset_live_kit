"""Facts she sells from: a dish, its extras, a bigger size, a complement.

Pure functions over the catalog. They return facts (names, spoken prices,
short catalog-true selling points), never finished sentences: phrasing is the
model's job and lives in the prompt. Numbers come pre-spelled (``*_say_ar``)
because a wrong spoken price is a wrong order.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from src import catalog
from src.catalog import Meal, number_to_arabic_words, rating_to_arabic_words, sar_words, speakable_name
from src.config import CONFIG

_KIND_AR = {"main": "طبق رئيسي", "side": "مقبلات", "salad": "سلطة", "soup": "شوربة", "drink": "مشروب", "dessert": "حلا"}


def selling_points(meal: Meal, *, healthy: bool = False) -> list[str]:
    """Up to four short, catalog-true reasons to pick this dish."""
    points: list[str] = []
    if meal.has_discount:
        points.append(f"عليها خصم، بدل {sar_words(meal.price)}")
    if meal.cashback_percent:
        points.append(f"عليها كاش باك {number_to_arabic_words(meal.cashback_percent)} بالمية")
    if meal.combo_includes:
        items = [speakable_name(i) for i in meal.combo_includes]
        points.append("فيها " + "، ".join(items[:4]))
    if meal.serves and meal.serves > 1:
        points.append(f"تكفي {number_to_arabic_words(meal.serves)} أشخاص")
    if meal.is_popular:
        points.append("من أكثر الأصناف طلب")
    if meal.rating >= 4.7:
        points.append(f"تقييمها {rating_to_arabic_words(meal.rating)}")
    if (healthy or "light" in meal.diet_tags) and meal.calories:
        points.append(f"{number_to_arabic_words(meal.calories)} سعرة")
    if "high_protein" in meal.diet_tags:
        points.append(f"فيها {number_to_arabic_words(meal.protein)} جرام بروتين")
    if meal.spicy_option:
        points.append("تجي حارة لو تحبها")
    return points[:4]


def addon_facts(meal: Meal) -> list[dict[str, Any]]:
    return [
        {
            "addon_id": str(a.get("id") or ""),
            "name_say_ar": speakable_name(str(a.get("name") or "")),
            "price_say_ar": sar_words(float(a.get("price") or 0)),
        }
        for a in meal.addons
        if a.get("id")
    ]


def meal_facts(meal: Meal, *, healthy: bool = False) -> dict[str, Any]:
    """Everything she may say about one dish."""
    facts: dict[str, Any] = {
        "meal_id": meal.id,
        "name_say_ar": speakable_name(meal.name),
        "restaurant_say_ar": speakable_name(meal.restaurant_name),
        "type": meal.type,
        "price_sar": round(meal.effective_price, 2),
        "price_say_ar": sar_words(meal.effective_price),
        "description": meal.description,
        "calories_say_ar": number_to_arabic_words(meal.calories),
        "protein_say_ar": number_to_arabic_words(meal.protein),
        "selling_points": selling_points(meal, healthy=healthy),
    }
    if meal.serves:
        facts["serves_people"] = meal.serves
    if meal.is_offer:
        facts["offer_kind"] = meal.offer_kind or "deal"
    if meal.spicy_option:
        facts["spicy_available"] = True
    delivery = catalog.delivery_minutes_say_ar(meal)
    if delivery:
        facts["delivery_minutes_say_ar"] = delivery
    if meal.fast_delivery:
        facts["fast_delivery"] = True
    return facts


def within_budget(price: float, budget: Optional[float]) -> bool:
    """Whether an upsell at this price fits what he said he would spend."""
    return not budget or price <= budget


def meal_deal_for(meal: Meal, *, budget: Optional[float] = None) -> Optional[Meal]:
    """The combo built around this dish, when it fits the budget."""
    deal = catalog.meal_deal_option(meal)
    if deal is None or not within_budget(deal.effective_price, budget):
        return None
    return deal


def meal_deal_facts(deal: Meal, base: Meal) -> dict[str, Any]:
    """The combo as a step up from the dish: what it adds and what it costs."""
    difference = deal.effective_price - base.effective_price
    facts: dict[str, Any] = {
        "meal_id": deal.id,
        "name_say_ar": speakable_name(deal.name),
        "price_say_ar": sar_words(deal.effective_price),
        "includes_say_ar": [speakable_name(i) for i in deal.combo_includes],
    }
    if difference > 0:
        facts["costs_more_say_ar"] = sar_words(difference)
    elif difference < 0:
        facts["costs_less_say_ar"] = sar_words(-difference)
    else:
        facts["same_price"] = True
    return facts


def upgrade_for(meal: Meal, *, party_size: Optional[int], budget: Optional[float] = None) -> Optional[Meal]:
    bigger = catalog.upgrade_option(meal, party_size=party_size)
    if bigger is None or not within_budget(bigger.effective_price, budget):
        return None
    return bigger


def upgrade_facts(bigger: Meal, base: Meal) -> dict[str, Any]:
    extra = max(0.0, bigger.effective_price - base.effective_price)
    facts = {
        "meal_id": bigger.id,
        "name_say_ar": speakable_name(bigger.name),
        "price_say_ar": sar_words(bigger.effective_price),
        "costs_more_say_ar": sar_words(extra),
    }
    if bigger.serves:
        facts["serves_people"] = bigger.serves
    return facts


def pair_for(
    meal: Meal,
    *,
    in_cart: Iterable[str] = (),
    exclude_addon_ids: Iterable[str] = (),
    healthy: bool = False,
    hour: int = 12,
    session_id: str = "",
    room_left: Optional[float] = None,
    budget: Optional[float] = None,
) -> Optional[Meal]:
    """One complement worth offering after this dish goes in, or None.

    A complement is another dish: the dish's own add-ons were the extras
    offer. Live 2026-09-27: a grill combo's only "complements" were its cola
    and garlic sauce add-ons, so none was offered and the soup never came up.

    ``room_left`` is what is left of his budget after the cart. One that fits
    is preferred; if none does, the cheapest small one (up to half his budget,
    at least 10 riyals) is still offered, and the caller says it goes over. A
    budget of 20 with an 18-riyal kabsa in the cart otherwise ended all
    cross-selling. None means no budget was given.
    """
    own_addons = {str(a.get("id") or "") for a in meal.addons}
    loaded = catalog.load_catalog()
    skip = set(in_cart)
    options: list[dict[str, Any]] = []
    # A complement costs less than the dish it goes with (فتوش with a
    # shawarma, not a mezze platter dearer than the sandwich): ask again,
    # without the dear ones, a few times before settling.
    for _ in range(4):
        found = [
            o for o in catalog.suggest_pairs(
                meal,
                exclude_meal_ids=skip,
                exclude_addon_ids=set(exclude_addon_ids) | own_addons,
                limit=3,
                healthy_mood=True if healthy else None,
                session_id=session_id,
            )
            if o.get("meal_id") and loaded.by_id(str(o["meal_id"])) is not None
        ]
        if not found:
            break
        options = options or found
        cheaper = [o for o in found if loaded.by_id(str(o["meal_id"])).effective_price <= meal.effective_price]
        if cheaper:
            options = cheaper
            break
        skip |= {str(o["meal_id"]) for o in found}
    if room_left is not None:
        price = {str(o["meal_id"]): m.effective_price for o in options if (m := loaded.by_id(str(o["meal_id"]))) is not None}
        fits = [o for o in options if price.get(str(o["meal_id"]), float("inf")) <= room_left]
        if not fits and budget:
            small = [o for o in options if price.get(str(o["meal_id"]), float("inf")) <= max(10.0, 0.5 * budget)]
            fits = sorted(small, key=lambda o: price[str(o["meal_id"])])[:1]
        options = fits
    if not options:
        return None
    options.sort(key=lambda o: _time_of_day_rank(o, hour))
    return loaded.by_id(str(options[0]["meal_id"]))


_WATER = {"ماء", "مويه", "مياه"}


def pair_addon_for(
    meal: Meal,
    *,
    taken_addon_ids: Iterable[str] = (),
    healthy: bool = False,
    room_left: Optional[float] = None,
    budget: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """A drink or salad from the dish's own add-ons, when its kitchen has no other.

    Live 2026-09-27: «أرز باللحم» from a grill that sells only mains got no
    complement at all, though its cola was right there. Sauces and pickles
    are extras, not a complement, so only a drink or a salad counts (water
    for a salad); a healthy caller gets no fizzy drink. Same soft budget as ``pair_for``.
    """
    taken = {str(a) for a in taken_addon_ids}
    cap = max(10.0, 0.5 * budget) if budget else None
    # More salad is not a complement to a salad.
    salad_dish = meal.type == "salad" or "سلط" in catalog.normalize_ar(meal.name)
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for addon in meal.addons:
        addon_id = str(addon.get("id") or "")
        name = str(addon.get("name") or "")
        price = float(addon.get("price") or 0)
        if not addon_id or addon_id in taken or price <= 0:
            continue
        if room_left is not None and price > room_left and (cap is None or price > cap):
            continue
        folded = catalog.normalize_ar(name)
        if folded in _WATER:
            ranked.append((0, price, addon))
        elif "سلط" in folded and not salad_dish:
            ranked.append((1, price, addon))
        elif catalog._addon_kind(name) == "drink" and not healthy:
            ranked.append((0, price, addon))
    if not ranked:
        return None
    return min(ranked, key=lambda r: (r[0], r[1]))[2]


def pair_addon_facts(addon: dict[str, Any], added: Meal) -> dict[str, Any]:
    name = str(addon.get("name") or "")
    return {
        "addon_id": str(addon.get("id") or ""),
        "name_say_ar": speakable_name(name),
        "kind_ar": _KIND_AR["salad"] if "سلط" in catalog.normalize_ar(name) else _KIND_AR["drink"],
        "price_say_ar": sar_words(float(addon.get("price") or 0)),
        "goes_with_say_ar": speakable_name(added.name),
        "added_to_same_line": True,
    }


def _time_of_day_rank(option: dict[str, Any], hour: int) -> int:
    """Late: something sweet or a drink. Lunch: a side. Afternoon: a drink."""
    kind = str(option.get("kind") or "")
    if hour >= 21 or hour < 5:
        preferred = {"dessert", "drink"}
    elif 11 <= hour < 15:
        preferred = {"side"}
    elif 15 <= hour < 18:
        preferred = {"drink"}
    else:
        preferred = set()
    return 0 if kind in preferred else 1


def pair_facts(pair: Meal, added: Meal) -> dict[str, Any]:
    facts = {
        "meal_id": pair.id,
        "name_say_ar": speakable_name(pair.name),
        "type": pair.type,
        "kind_ar": _KIND_AR.get(pair.type, ""),
        "price_say_ar": sar_words(pair.effective_price),
        "description": pair.description,
        "goes_with_say_ar": speakable_name(added.name),
    }
    if pair.is_popular:
        facts["popular"] = True
    if "light" in pair.diet_tags and pair.calories:
        facts["calories_say_ar"] = number_to_arabic_words(pair.calories)
    return facts


def lighter_alternative(meal: Meal) -> Optional[Meal]:
    """A lighter dish close to this one, for a caller who asked for healthy food."""
    if catalog.meal_is_healthy(meal):
        return None
    return catalog.closest_healthy_meal(meal)


def rules_max_quantity() -> int:
    return CONFIG.rules.max_line_quantity
