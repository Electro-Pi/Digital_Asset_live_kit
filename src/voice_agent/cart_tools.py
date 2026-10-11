"""The cart: reading it, changing a line, and the one place a dish is added.

The browser owns the cart; every number here comes back from it
(``luqma.getCart``), never from memory.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Optional

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog, offers
from src.catalog import Meal, number_to_arabic_words, sar_words, speakable_name
from src.config import CONFIG
from src.search import stem
from src.session_state import CHECKOUT, PAIR, Offer


def match_addon(requested: str, meal: Meal) -> Optional[str]:
    """This meal's addon id for what the model passed: the id, the Arabic name,
    or the ASR's rendering of the name ("Sauce Tomb" for «صوص ثوم»).

    Matching is scoped to this meal's handful of addons, so loose matching is
    safe: there is nothing else "sauce" could mean here. Unrelated words score
    well below the bar and match nothing.
    """
    want = catalog.normalize_ar(requested)
    if not want:
        return None
    want_tokens = set(want.split())
    best_id: Optional[str] = None
    best_score = 0.0
    for addon in meal.addons:
        addon_id = str(addon.get("id") or "")
        name = catalog.normalize_ar(addon.get("name"))
        id_text = catalog.normalize_ar(addon_id.replace("-", " ").replace("_", " "))
        if want in (addon_id.lower(), name, id_text):
            return addon_id
        name_bare = {stem(t) for t in name.split()}
        candidate = set(name.split()) | set(id_text.split()) | name_bare
        wanted = want_tokens | {stem(t) for t in want_tokens}
        overlap = len(wanted & candidate) / min(len(want_tokens), len(set(name.split()) | set(id_text.split()))) if candidate else 0.0
        fuzz = max(
            SequenceMatcher(None, want, name).ratio() if name else 0.0,
            SequenceMatcher(None, want, id_text).ratio() if id_text else 0.0,
        )
        score = max(fuzz, min(overlap, 1.0))
        if score > best_score:
            best_id, best_score = addon_id, score
    return best_id if best_score >= 0.5 else None


class CartTools:
    """``get_cart`` and ``update_cart``; ``_add_line`` is used by respond_to_offer."""

    async def _cart_snapshot(self) -> dict[str, Any]:
        outcome = await self.ui.get_cart()
        if not outcome.ok:
            return {"ok": False, "error": outcome.reason or "cart_unavailable"}
        loaded = catalog.load_catalog()
        lines: list[dict[str, Any]] = []
        subtotal = 0.0
        for item in (outcome.data or {}).get("items") or []:
            meal = loaded.by_id(str(item.get("mealId") or ""))
            qty = int(item.get("quantity") or 1)
            unit = float(item.get("unitPrice") or 0.0)
            subtotal += unit * qty
            addon_ids = [str(a) for a in (item.get("addonIds") or []) if a]
            by_id = {str(a.get("id") or ""): a for a in (meal.addons if meal else ())}
            line: dict[str, Any] = {
                "meal_id": str(item.get("mealId") or ""),
                "name_say_ar": speakable_name(meal.name) if meal else str(item.get("mealId") or ""),
                "quantity": qty,
                "quantity_say_ar": number_to_arabic_words(qty),
                "line_total_say_ar": sar_words(unit * qty),
                "addon_ids": addon_ids,
                "addons_say_ar": [speakable_name(str(by_id[a].get("name") or a)) for a in addon_ids if a in by_id],
                "spicy": bool(item.get("spicy")),
            }
            if meal:
                line.update(calories=meal.calories, protein_g=meal.protein, carbs_g=meal.carbs, fat_g=meal.fat)
            lines.append(line)
        count = sum(line["quantity"] for line in lines)
        self.state.cart_item_count = count
        snap = {
            "ok": True,
            "items": lines,
            "item_count": count,
            "subtotal_sar": round(subtotal, 2),
            "subtotal_say_ar": sar_words(subtotal),
            "is_empty": not lines,
        }
        nutrition = catalog.cart_nutrition(lines)
        snap["calories_total_say_ar"] = nutrition["calories_total_say_ar"]
        snap["protein_total_say_ar"] = nutrition["protein_total_say_ar"]
        for line in lines:
            for key in ("calories", "protein_g", "carbs_g", "fat_g"):
                line.pop(key, None)
        return snap

    @function_tool()
    async def get_cart(self, context: RunContext) -> dict[str, Any]:
        """Read the cart: lines, spoken totals, and calorie and protein totals.

        Call it when the caller asks what is in the cart, the total, or the
        cart's calories. Speak only the numbers it returns.
        """
        snap = await self._cart_snapshot()
        logger.info("Tool get_cart ok={} items={}", snap.get("ok"), snap.get("item_count"))
        if snap.get("ok"):
            snap["summary_ar"] = f"المجموع {snap['subtotal_say_ar']}"
        return snap

    @function_tool()
    async def update_cart(self, context: RunContext, meal_id: str, quantity: int) -> dict[str, Any]:
        """Change how many of a dish already in the cart; 0 removes it.

        Use it for «شيل الـ…», «خلها اثنين», «زد واحد». To add a dish that is
        not in the cart yet, open it with show_meal and accept its offer.

        Args:
            meal_id: The meal_id of a cart line (from get_cart).
            quantity: The new total for that line, 0 to remove it.
        """
        maximum = CONFIG.rules.max_line_quantity
        if quantity < 0 or quantity > maximum:
            raise ToolError(f"quantity must be between 0 and {maximum}; ask the caller to confirm the count")
        if self.state.order_placed:
            return {"ok": False, "error": "order_already_placed"}
        snap = await self._cart_snapshot()
        if not snap.get("ok"):
            return {"ok": False, "error": "cart_unavailable"}
        line = next((l for l in snap["items"] if l["meal_id"] == meal_id), None)
        if line is None:
            return {
                "ok": False,
                "error": "not_in_cart",
                "cart_meal_ids": [l["meal_id"] for l in snap["items"]],
            }
        self.no_interruptions(context)
        outcome = self.screen(
            await self.ui.set_cart_quantity(
                meal_id=meal_id, quantity=quantity, spicy=line["spicy"], addon_ids=line["addon_ids"]
            )
        )
        logger.info("Tool update_cart meal_id={} qty={} ok={} reason={}", meal_id, quantity, outcome.ok, outcome.reason)
        if not outcome.ok:
            return {"ok": False, "error": "screen_did_not_respond"}
        if quantity == 0:
            self.state.note_removed(meal_id)
        # The summary she read no longer matches the cart.
        self.state.summary_read = False
        after = await self._cart_snapshot()
        return {
            "ok": True,
            "meal_id": meal_id,
            "name_say_ar": line["name_say_ar"],
            "quantity": quantity,
            "removed": quantity == 0,
            "cart": after,
            "summary_ar": f"صار المجموع {after.get('subtotal_say_ar', '')}" if after.get("ok") else "",
        }

    async def _add_line(
        self,
        context: Any,
        meal: Meal,
        *,
        quantity: int = 1,
        addon_ids: Optional[list[str]] = None,
        spicy: bool = False,
    ) -> dict[str, Any]:
        """Put one line in the cart, then decide what to offer next."""
        maximum = CONFIG.rules.max_line_quantity
        quantity = max(1, int(quantity or 1))
        if quantity > maximum:
            raise ToolError(f"quantity must be at most {maximum}; ask the caller to confirm the count")
        accepted, unknown = [], []
        for requested in addon_ids or []:
            match = match_addon(requested, meal)
            if match and match not in accepted:
                accepted.append(match)
            elif not match:
                unknown.append(str(requested))
        if unknown:
            return {
                "ok": False,
                "error": "unknown_extras",
                "unknown": unknown,
                "extras_available": offers.addon_facts(meal),
            }
        spicy = bool(spicy and meal.spicy_option)

        self.no_interruptions(context)
        outcome = self.screen(
            await self.ui.add_to_cart(meal_id=meal.id, quantity=quantity, spicy=spicy, addon_ids=accepted)
        )
        logger.info("Cart add meal_id={} qty={} addons={} spicy={} ok={} reason={}",
                    meal.id, quantity, accepted, spicy, outcome.ok, outcome.reason)
        if not outcome.ok:
            return {"ok": False, "error": "screen_did_not_respond", "meal_id": meal.id}
        self.state.note_added(meal.id)
        self.state.meal_options[meal.id] = {"addon_ids": accepted, "spicy": spicy, "quantity": quantity}
        self.state.summary_read = False
        by_id = {str(a.get("id") or ""): a for a in meal.addons}
        extras_total = sum(float(by_id[a].get("price") or 0) for a in accepted)
        cart = await self._cart_snapshot()
        result: dict[str, Any] = {
            "ok": True,
            "added": {
                "meal_id": meal.id,
                "name_say_ar": speakable_name(meal.name),
                "quantity": quantity,
                "extras_say_ar": [speakable_name(str(by_id[a].get("name") or "")) for a in accepted],
                "spicy": spicy,
                "line_total_say_ar": sar_words((meal.effective_price + extras_total) * quantity),
            },
        }
        if cart.get("ok"):
            result["cart_total_say_ar"] = cart["subtotal_say_ar"]
            result["cart_item_count"] = cart["item_count"]
        result["next_offer"] = self._offer_after_add(meal, accepted, cart)
        result["summary_ar"] = f"زدت {speakable_name(meal.name)}"
        return result

    async def _add_to_line(self, context: Any, meal: Meal, addon_id: str) -> dict[str, Any]:
        """Put one of the dish's add-ons on its cart line: the line is re-added with it."""
        snap = await self._cart_snapshot()
        line = next((l for l in snap.get("items") or [] if l["meal_id"] == meal.id), None)
        if line is None:
            return {"ok": False, "error": "not_in_cart", "meal_id": meal.id}
        addon_ids = list(line["addon_ids"])
        if addon_id not in addon_ids:
            addon_ids.append(addon_id)
        self.no_interruptions(context)
        removed = self.screen(await self.ui.set_cart_quantity(
            meal_id=meal.id, quantity=0, spicy=line["spicy"], addon_ids=line["addon_ids"],
        ))
        outcome = removed if not removed.ok else self.screen(await self.ui.add_to_cart(
            meal_id=meal.id, quantity=line["quantity"], spicy=line["spicy"], addon_ids=addon_ids,
        ))
        logger.info("Cart extra on line meal_id={} addon={} ok={} reason={}", meal.id, addon_id, outcome.ok, outcome.reason)
        if not outcome.ok:
            return {"ok": False, "error": "screen_did_not_respond", "meal_id": meal.id}
        self.state.meal_options[meal.id] = {"addon_ids": addon_ids, "spicy": line["spicy"], "quantity": line["quantity"]}
        self.state.summary_read = False
        name = next((str(a.get("name") or "") for a in meal.addons if str(a.get("id") or "") == addon_id), addon_id)
        cart = await self._cart_snapshot()
        offer = Offer(CHECKOUT)
        self.state.open(offer)
        result: dict[str, Any] = {
            "ok": True,
            "added": {"extra_say_ar": speakable_name(name), "with_say_ar": speakable_name(meal.name)},
            "next_offer": {**offer.as_fact(), "cart_total_say_ar": cart.get("subtotal_say_ar", "")},
            "summary_ar": f"زدت {speakable_name(name)} مع {speakable_name(meal.name)}",
        }
        if cart.get("ok"):
            result["cart_total_say_ar"] = cart["subtotal_say_ar"]
        return result

    def _offer_after_add(self, meal: Meal, addon_ids: list[str], cart: dict[str, Any]) -> dict[str, Any]:
        """One complement per dish he chose, unless he has declined enough; else checkout."""
        state = self.state
        in_cart = {line["meal_id"] for line in cart.get("items") or []} | set(state.added_meal_ids)
        # Each dish he picks gets its complement; a complement gets none of its own.
        fresh = meal.id not in state.paired_meal_ids and meal.id not in state.complement_ids
        if fresh and not state.cross_sell_fatigued:
            room_left = None
            if state.budget and cart.get("ok"):
                room_left = max(0.0, state.budget - float(cart.get("subtotal_sar") or 0))
            pair = offers.pair_for(
                meal,
                in_cart=in_cart,
                exclude_addon_ids=addon_ids,
                healthy=state.health_conscious,
                hour=self.shop.hour,
                session_id=state.session_id,
                room_left=room_left,
                budget=state.budget,
            )
            if pair is not None:
                state.paired_meal_ids.add(meal.id)
                state.complement_ids.add(pair.id)
                offer = Offer(PAIR, pair.id)
                state.open(offer)
                facts = {**offer.as_fact(), **offers.pair_facts(pair, meal)}
                if room_left is not None and pair.effective_price > room_left:
                    # Offered anyway, small; she says honestly it goes over.
                    facts["over_budget_by_say_ar"] = sar_words(pair.effective_price - room_left)
                return facts
            addon = offers.pair_addon_for(
                meal, taken_addon_ids=addon_ids, healthy=state.health_conscious,
                room_left=room_left, budget=state.budget,
            )
            if addon is not None:
                state.paired_meal_ids.add(meal.id)
                offer = Offer(PAIR, meal.id, addon_id=str(addon["id"]))
                state.open(offer)
                facts = {**offer.as_fact(), **offers.pair_addon_facts(addon, meal)}
                price = float(addon.get("price") or 0)
                if room_left is not None and price > room_left:
                    facts["over_budget_by_say_ar"] = sar_words(price - room_left)
                return facts
        offer = Offer(CHECKOUT)
        state.open(offer)
        return {**offer.as_fact(), "cart_total_say_ar": cart.get("subtotal_say_ar", "")}
