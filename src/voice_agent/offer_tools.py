"""The caller's yes or no to something she offered.

Every question she asks is an offer with an id, returned by the tool that
opened it (``show_meal``, ``respond_to_offer``). The model answers with that
id, so a «ايه» can only ever accept the offer the model names — and the code
checks the offer is really open. This replaces the old ``confirm(affirmative)``,
whose meaning depended on server state the model could not see.
"""

from __future__ import annotations

from typing import Any, Optional

from livekit.agents import RunContext, function_tool
from loguru import logger

from src import catalog, offers
from src.catalog import speakable_name
from src.search import row as result_row
from src.session_state import CHECKOUT, EXTRAS, PAIR, PITCH, UPGRADE, Offer

# Declining these counts towards cross-sell fatigue; extras and checkout do not.
_SALES_OFFERS = {PITCH, UPGRADE, PAIR}


class OfferTools:
    @function_tool()
    async def respond_to_offer(
        self,
        context: RunContext,
        offer_id: str,
        accepted: bool,
        addon_ids: Optional[list[str]] = None,
        no_extras: bool = False,
        quantity: int = 1,
        spicy: bool = False,
    ) -> dict[str, Any]:
        """Record the caller's answer to an open offer, and act on it.

        Call it only after the caller answered the offer: yes («ايه»، «تمام»،
        «هاتها»، «اعتمد») or no («لا»، «ما أبي»، «غيره»). Pass the offer_id
        exactly as a tool returned it in ``open_offers`` or ``next_offer``.

        What each answer does:
        - pitch:<dish> yes → the dish goes in (or, when it has extras and he
          named none, an ``extras`` offer opens: recommend one or two). no →
          other results come back; show_meal the next one.
        - extras:<dish> yes with addon_ids → added with them; no, or
          no_extras=true → added plain.
        - upgrade:<dish> yes → the bigger size or the meal deal replaces the
          pitched dish; no → the pitched dish stays on offer.
        - pair:<dish> yes → the complement goes in; no → move to checkout.
        - checkout yes → checkout starts (also when another offer is still
          open); no → keep shopping.

        Args:
            offer_id: The id of the open offer he answered, e.g. "pitch:shawarma-1".
            accepted: True for yes, False for no.
            addon_ids: Extras he chose, as addon_id values (or their names). Only with a yes.
            no_extras: True when he accepted the dish and said he wants no extras.
            quantity: How many of the dish, when he said a number. Default 1.
            spicy: True only when he asked for it spicy.
        """
        state = self.state
        offer = state.find(offer_id)
        if offer is None and str(offer_id).strip() == CHECKOUT and accepted:
            # Finishing is always on the table once something is in the cart,
            # whatever else was last offered (a complement he declined in
            # words, say). The checkout tool checks the cart, and closes the
            # open offers only once it really starts: live 2026-09-27 an empty
            # cart used to take the kunafa's open pitch down with it.
            return await self._run_checkout(context)
        if offer is None:
            logger.warning("respond_to_offer: {} is not open; open={}", offer_id, state.open_facts())
            error: dict[str, Any] = {
                "ok": False,
                "error": "offer_not_open",
                "fix": "Answer one of open_offers by its offer_id, or show_meal the dish he wants.",
                "offer_id": offer_id,
                "open_offers": state.open_facts(),
            }
            # The dish he meant is in the id: point at what is open for it now
            # (his «ماشي» after «نزيد صوص؟» answers the extras offer, not the
            # pitch he already accepted), or say how to put it back on offer.
            meal_id = str(offer_id).partition(":")[2]
            same_dish = [o for o in state.open_offers if meal_id and o.meal_id == meal_id]
            if same_dish:
                current = same_dish[0]
                error["fix"] = (
                    f"{offer_id} was already answered. The open question about this dish is {current.id!r}: "
                    f"answer that with respond_to_offer"
                    + (" (addon_ids for the extras he agreed to, or no_extras=true)." if current.kind == EXTRAS else ".")
                )
            elif meal_id and catalog.load_catalog().by_id(meal_id) is not None:
                error["fix"] = (
                    f"This offer was closed. Call show_meal(meal_id={meal_id!r}) now to put the dish back on offer, "
                    "then answer the new offer with respond_to_offer."
                )
            return error
        logger.info("Tool respond_to_offer offer={} accepted={} addons={} no_extras={} qty={}",
                    offer.id, accepted, addon_ids, no_extras, quantity)
        if not accepted and offer.kind in _SALES_OFFERS:
            state.declined_offers += 1

        if offer.kind == PITCH:
            return await self._answer_pitch(context, offer, accepted, addon_ids, no_extras, quantity, spicy)
        if offer.kind == EXTRAS:
            meal = catalog.load_catalog().by_id(offer.meal_id)
            wants_extras = accepted and not no_extras
            if wants_extras and not addon_ids:
                return {
                    "ok": False,
                    "error": "which_extras",
                    "fix": "Pass the extras he named in addon_ids, or no_extras=true if he wants none.",
                    "open_offers": state.open_facts(),
                    "extras_available": offers.addon_facts(meal),
                }
            state.close()
            chosen = addon_ids if wants_extras else []
            return await self._add_line(
                context, meal,
                quantity=quantity if quantity and quantity != 1 else offer.quantity,
                addon_ids=chosen,
                spicy=spicy or offer.spicy,
            )
        if offer.kind == UPGRADE:
            base = catalog.load_catalog().by_id(offer.base_meal_id)
            if not accepted:
                state.close(UPGRADE)
                return {"ok": True, "kept": offers.meal_facts(base), "open_offers": state.open_facts(),
                        "summary_ar": f"تمام، نخليها {speakable_name(base.name)}"}
            bigger = catalog.load_catalog().by_id(offer.meal_id)
            state.close()
            screen = self.screen(await self.ui.show_meal(meal_id=bigger.id, path=bigger.page_path))
            if screen.ok:
                state.last_meal_id = bigger.id
            return await self._accept_dish(context, bigger, addon_ids, no_extras, quantity, spicy)
        if offer.kind == PAIR:
            state.close()
            if not accepted:
                offer_next = Offer(CHECKOUT)
                state.open(offer_next)
                return {"ok": True, "declined": True, "next_offer": offer_next.as_fact(),
                        "summary_ar": "تمام، نكمل الطلب؟"}
            if offer.addon_id:
                return await self._add_to_line(context, catalog.load_catalog().by_id(offer.meal_id), offer.addon_id)
            pair = catalog.load_catalog().by_id(offer.meal_id)
            screen = self.screen(await self.ui.show_meal(meal_id=pair.id, path=pair.page_path))
            if screen.ok:
                state.last_meal_id = pair.id
            # He said yes to it in words already: no second pitch, no extras round.
            return await self._add_line(context, pair, quantity=quantity, addon_ids=addon_ids or [], spicy=spicy)
        if offer.kind == CHECKOUT:
            if not accepted:
                state.close()
                return {"ok": True, "keep_shopping": True, "summary_ar": "أبشر، وش تبي بعد؟"}
            return await self._run_checkout(context)
        return {"ok": False, "error": "unknown_offer_kind", "offer_id": offer.id}

    async def _answer_pitch(self, context, offer, accepted, addon_ids, no_extras, quantity, spicy) -> dict[str, Any]:
        state = self.state
        meal = catalog.load_catalog().by_id(offer.meal_id)
        if not accepted:
            state.close()
            others = [
                result_row(m)
                for m in (catalog.load_catalog().by_id(i) for i in state.last_search_ids)
                if m is not None and m.id != meal.id
            ][:3]
            return {"ok": True, "declined": speakable_name(meal.name), "other_results": others,
                    "summary_ar": "تمام، أشوف لك غيرها"}
        state.close()
        return await self._accept_dish(context, meal, addon_ids, no_extras, quantity, spicy)

    async def _accept_dish(self, context, meal, addon_ids, no_extras, quantity, spicy) -> dict[str, Any]:
        """He wants this dish: ask about extras once, or add it now."""
        if addon_ids or no_extras or not meal.addons:
            return await self._add_line(context, meal, quantity=quantity, addon_ids=addon_ids or [], spicy=spicy)
        offer = Offer(EXTRAS, meal.id, quantity=max(1, int(quantity or 1)), spicy=bool(spicy))
        self.state.open(offer)
        return {
            "ok": True,
            "accepted": speakable_name(meal.name),
            "next_offer": {**offer.as_fact(), "extras_available": offers.addon_facts(meal)},
            "summary_ar": f"تبي إضافات مع {speakable_name(meal.name)}؟",
        }
