"""The caller's saved dishes (المحفوظات)."""

from __future__ import annotations

from typing import Any

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog
from src.catalog import sar_words, speakable_name

ACTIONS = ("save", "remove", "list", "clear")


class SavedTools:
    @function_tool()
    async def saved_meals(self, context: RunContext, action: str, meal_id: str = "") -> dict[str, Any]:
        """Save a dish for later, remove it, list the saved dishes, or clear them all.

        Args:
            action: "save", "remove", "list" or "clear".
            meal_id: With "save" or "remove": a meal_id from search results.
        """
        action = (action or "").strip().lower()
        if action not in ACTIONS:
            raise ToolError(f"action must be one of {', '.join(ACTIONS)}")
        cat = catalog.load_catalog()
        if action in ("save", "remove"):
            meal = cat.by_id(meal_id)
            if meal is None:
                if cat.restaurant_by_id(meal_id) is not None:
                    raise ToolError(f"{meal_id!r} is a restaurant; only dishes can be saved")
                raise ToolError(f"No dish with meal_id {meal_id!r}; search first")
            self.no_interruptions(context)
            outcome = self.screen(await self.ui.set_saved(meal_id=meal.id, saved=action == "save"))
            logger.info("Tool saved_meals {} meal_id={} ok={} reason={}", action, meal.id, outcome.ok, outcome.reason)
            if not outcome.ok:
                return {"ok": False, "error": "screen_did_not_respond"}
            verb = "حفظت" if action == "save" else "شلت"
            return {"ok": True, "action": action, "name_say_ar": speakable_name(meal.name),
                    "summary_ar": f"{verb} {speakable_name(meal.name)}"}
        if action == "clear":
            self.no_interruptions(context)
            outcome = self.screen(await self.ui.clear_saved())
            logger.info("Tool saved_meals clear ok={} reason={}", outcome.ok, outcome.reason)
            if not outcome.ok:
                return {"ok": False, "error": "screen_did_not_respond"}
            return {"ok": True, "action": "clear", "summary_ar": "فضيت المحفوظات"}
        outcome = await self.ui.get_saved()
        logger.info("Tool saved_meals list ok={} reason={}", outcome.ok, outcome.reason)
        if not outcome.ok:
            return {"ok": False, "error": "screen_did_not_respond"}
        ids = [str(x) for x in ((outcome.data or {}).get("mealIds") or []) if x]
        items = [
            {"meal_id": m.id, "name_say_ar": speakable_name(m.name), "price_say_ar": sar_words(m.effective_price)}
            for m in (cat.by_id(i) for i in ids)
            if m is not None
        ]
        return {"ok": True, "action": "list", "items": items, "count": len(items),
                "summary_ar": "المحفوظات فاضية" if not items else f"عندك {len(items)} محفوظة"}
