"""Finding dishes and putting one on the caller's screen."""

from __future__ import annotations

from typing import Any

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog, offers
from src.config import CONFIG
from src.search import DISH_TYPES, OFFER_KINDS, SORTS, quantity_for
from src.session_state import BUDGET, DISCOVERY_QUESTIONS, PEOPLE, PITCH, TASTE, UPGRADE, Discovery, Offer
from src.speech_claims import said_number

# search_meals arguments that say what he wants to eat; the people and budget
# answers narrow a search with these, they never replace them.
_TASTE_ARGS = (
    "query", "dish_type", "cuisine", "restaurant_id", "healthy", "spicy", "only_offers",
    "offer_kind", "cashback", "max_calories", "min_protein",
)
# «لحالي», «لي بس»: one person. Normalised (ة→ه, أ→ا).
_JUST_ME = ("لحالي", "لي بس", "بس انا", "انا بس", "لنفسي", "شخص واحد", "واحد بس")


def _empty_summary(result: dict[str, Any]) -> str:
    """What to say when a search found nothing, from what it did find nearby.

    The fallback line when the model says nothing: live eval 2026-09-27, an
    invented ``healthy`` filter emptied «سلطة سيزر» and she stayed silent.
    """
    near = (result.get("exists_but_filtered") or {}).get("example") or result.get("cheapest_available")
    if near:
        return f"عندي {near['name_say_ar']} بـ {near['price_say_ar']}، تبيني أعرضه لك؟"
    alternatives = result.get("alternatives") or []
    if alternatives:
        return f"هذا مو موجود عندنا، بس عندي {alternatives[0]['name_say_ar']} بـ {alternatives[0]['price_say_ar']}، يناسبك؟"
    return "ما لقيت شي بهالطلب، تبي أدور لك على شي ثاني؟"


def _pitch_summary(dish: dict[str, Any], step_up: dict[str, Any] | None) -> str:
    """The pitch in one line; with a step up open, as its two-choice question.

    Coda-V2 tends to echo this line. A plain «تبيها؟» here made it skip the
    meal deal in three live runs out of four.
    """
    line = f"هذي {dish['name_say_ar']} بـ {dish['price_say_ar']}"
    if not step_up:
        return f"{line}، تبيها؟"
    if step_up.get("same_price"):
        cost = "بنفس السعر"
    elif step_up.get("costs_less_say_ar"):
        cost = f"بأقل بـ {step_up['costs_less_say_ar']}"
    else:
        cost = f"بـ {step_up['costs_more_say_ar']} زيادة"
    return f"{line}، تبيها لحالها ولا {step_up['name_say_ar']} {cost}؟"


class CatalogTools:
    """``search_meals`` reads; ``show_meal`` opens a dish and pitches it."""

    @function_tool()
    async def search_meals(
        self,
        context: RunContext,
        query: str = "",
        dish_type: str = "",
        cuisine: str = "",
        restaurant: str = "",
        max_price: float = 0,
        min_price: float = 0,
        max_calories: int = 0,
        min_protein: int = 0,
        healthy: bool = False,
        spicy: bool = False,
        only_offers: bool = False,
        offer_kind: str = "",
        cashback: bool = False,
        serves: int = 0,
        sort: str = "relevance",
        limit: int = 5,
        open_top: bool = False,
        recommend: bool = False,
        pick_for_me: bool = False,
    ) -> dict[str, Any]:
        """Find dishes in the catalog. Read-only unless open_top is true.

        Call this whenever the caller asks for food, a drink, a dessert, a
        restaurant's dishes, or a cheaper/lighter/bigger option. Never name a
        dish or a price that did not come from this tool or show_meal.

        Put the caller's dish words in ``query`` as they said them («شاورما
        لحم», «سلطة», «شي خفيف», «لثلاثة أشخاص»); the tool reads headcounts,
        «خفيف», «أرخص», «حلا» and similar words itself. Use the typed
        arguments when the caller was explicit.

        Args:
            query: The caller's dish words, in Arabic. Empty for a pure filter search.
            dish_type: "main", "side", "salad", "soup", "drink", "dessert" or "grocery". Empty = any.
            cuisine: A kitchen the caller named (e.g. "برجر", "مشاوي", "صحي"). Empty otherwise.
            restaurant: Only this restaurant's dishes (Arabic name as in the restaurant list). Empty otherwise.
            max_price: Riyals; 0 = no limit. Also remembered as his budget: no bigger size or complement above it is offered.
            min_price: Riyals; 0 = no limit.
            max_calories: Per person; 0 = no limit.
            min_protein: Grams; 0 = no limit.
            healthy: True when the caller asked for healthy or light food.
            spicy: True only when the caller asked for spicy.
            only_offers: True for deals, combos and family offers.
            offer_kind: "combo", "family" or "deal" (comma-separated allowed). Empty otherwise.
            cashback: True for cashback offers.
            serves: How many people are eating, when the caller said it. 0 = not said.
            sort: "relevance", "price_asc", "price_desc", "calories_asc", "calories_desc", "rating_desc", "protein_desc", "fat_desc" or "fastest_delivery" (he is in a hurry; rows then carry delivery_minutes_say_ar).
            limit: Results to return, 1 to 8.
            open_top: True to open the first result on the caller's screen and
                pitch it in the same step — use it when the first result is
                clearly what the caller asked for. False to see the options first.
            recommend: True when he asks you to suggest or recommend
                («اقترحي علي», «وش ترشحين», «وش عندك», «جوعان», «محتار», «وش
                أحسن شي») instead of naming the dish he wants. The tool then
                asks his taste, people and budget before recommending.
            pick_for_me: True only when he explicitly left the choice to you
                («أي شي»، «اختاري أنتِ»، «على ذوقك»): no more questions come
                back, recommend from the results. «اقترحي علي» or «وش ترشحين»
                asks for a recommendation and is not this.

        Returns:
            ``matched_by`` says how the query matched: "name", "text", "partial"
            (some dish words missing: say so), "category", or "none" (the dish
            is not sold: say so and offer ``alternatives``; never present an
            alternative as the dish he asked for). ``results`` are compact rows
            with spoken name and price. When open_top is true, ``opened`` holds
            what show_meal returns. ``narrowing_question`` means an open
            request is being narrowed down (his taste, then how many people,
            then his budget): nothing is opened; ask him that one question
            (``about``; ``options_say_ar`` for taste) and search again with his
            answer only. The tool remembers the earlier answers and opens the
            recommendation once the questions are done.
        """
        dish_type = (dish_type or "").strip().lower()
        if dish_type and dish_type not in DISH_TYPES:
            raise ToolError(f"dish_type must be one of {', '.join(DISH_TYPES)} or empty")
        sort = (sort or "relevance").strip().lower()
        if sort not in SORTS:
            raise ToolError(f"sort must be one of {', '.join(SORTS)}")
        kinds = [k.strip().lower() for k in (offer_kind or "").replace("+", ",").split(",") if k.strip()]
        if any(k not in OFFER_KINDS for k in kinds):
            raise ToolError(f"offer_kind must be one or more of {', '.join(OFFER_KINDS)}")

        typed = (dish_type, cuisine, restaurant, max_price, min_price, max_calories, min_protein, healthy,
                 spicy, only_offers, kinds, cashback, serves, sort != "relevance", recommend, pick_for_me)
        if not (query or "").strip() and not any(typed):
            # Coda-V2 has sent this call cut off after a few arguments; an empty
            # search would read as "recommend something" and start the questions.
            raise ToolError("Empty search: put the caller's words in query (and serves, max_price... if he said them).")

        state = self.state
        # Facts the caller gave, recorded where they arrived.
        if serves and serves > 0:
            state.party_size = int(serves)
        if healthy:
            state.health_conscious = True
        if max_price and max_price > 0:
            state.budget = float(max_price)

        args: dict[str, Any] = dict(
            query=query or "",
            dish_type=dish_type or None,
            cuisine=(cuisine or "").strip() or None,
            restaurant_id=(restaurant or "").strip() or None,
            prefer_restaurant_id=state.last_restaurant_id or None,
            max_price=max_price or None,
            min_price=min_price or None,
            max_calories=max_calories or None,
            min_protein=min_protein or None,
            healthy=bool(healthy),
            spicy=bool(spicy),
            only_offers=bool(only_offers),
            offer_kind=",".join(kinds) or None,
            cashback=bool(cashback),
            serves=int(serves) if serves and serves > 0 else None,
            sort=sort,
            limit=limit,
        )
        result = catalog.search_meals(**args)
        if result.get("serves_requested") and not serves:
            state.party_size = int(result["serves_requested"])
        result, finished = self._discover(result, args, recommend=recommend, pick_for_me=pick_for_me)
        if "narrowing_question" in result:
            open_top = False
        elif finished:
            # The questions are done: what she searched for is her recommendation.
            open_top = True
        rows = result.get("results") or []
        self.shop.ledger.record_search(rows)
        state.last_search_ids = [r["meal_id"] for r in rows]
        logger.info(
            "Tool search_meals query={!r} type={!r} cuisine={!r} serves={} healthy={} sort={} -> matched_by={} total={} top={}",
            query, dish_type, cuisine, serves, healthy, sort, result.get("matched_by"),
            result.get("total_matches"), [r["meal_id"] for r in rows[:3]],
        )
        if rows and "narrowing_question" not in result:
            first = rows[0]
            result["summary_ar"] = f"لقيت {first['name_say_ar']} بـ {first['price_say_ar']}"
        elif not rows and "narrowing_question" not in result:
            result["summary_ar"] = _empty_summary(result)
        if open_top and rows:
            meal = catalog.load_catalog().by_id(rows[0]["meal_id"])
            if meal is not None:
                result["opened"] = await self._open_meal(context, meal)
                result["summary_ar"] = result["opened"].get("summary_ar", result.get("summary_ar"))
                # The opened dish carries its own facts; keep the rest short.
                result["results"] = [
                    {k: r[k] for k in ("meal_id", "name_say_ar", "price_say_ar", "type") if k in r}
                    for r in rows[1:4]
                ]
        return result

    def _discover(self, result: dict[str, Any], args: dict[str, Any], *, recommend: bool, pick_for_me: bool) -> tuple[dict[str, Any], bool]:
        """Taste, then people, then budget, on an open request; see ``Discovery``.

        Returns the result to hand back and whether a discovery just finished
        (its last answer came in, so the search is re-run with all of them).
        A request that names a dish without asking for a recommendation, or a
        re-search after a dish was pitched («أرخص»), starts nothing.
        """
        state = self.state
        facts = result.pop("discovery", None) or {"taste_given": result.get("matched_by") == "none"}
        d = state.discovery
        if d is None:
            # Asked outright, or an open request before anything was pitched
            # (after a pitch, «شي أرخص» re-searches; it starts nothing).
            open_request = recommend or facts.get("asks_recommendation") or (
                (facts.get("too_open") or facts.get("open_request")) and not state.pitched_meal_ids
            )
            if pick_for_me or not open_request:
                return result, False
            d = state.discovery = Discovery()

        turn = self.shop.ledger.key
        answering = d.pending if d.pending and (not turn or turn != d.asked_in) else ""
        if answering:
            d.answered.add(answering)
            d.pending = ""
            self._read_answer(answering, args)
        # A number answering the people or budget question («أربعة», «ثلاث مية»)
        # is not a dish, whatever it matches («ثلاث» finds "Triple Stack").
        # «مشاوي لحم» said in answer to the budget question still is.
        taste_given = bool(facts.get("taste_given")) and not (
            answering in (PEOPLE, BUDGET) and said_number(args.get("query") or "") is not None
        )
        # «على ذوقك» hands her the choice; it names nothing to search for.
        taste_given = taste_given and not (pick_for_me and result.get("matched_by") == "none")
        if taste_given:
            d.taste = {k: args[k] for k in _TASTE_ARGS if args.get(k)}
            d.answered.add(TASTE)
        if state.party_size:
            d.answered.add(PEOPLE)
        if state.budget:
            d.answered.add(BUDGET)

        ask = next((q for q in DISCOVERY_QUESTIONS if q not in d.answered), None)
        if pick_for_me or len(d.asked) >= CONFIG.rules.max_discovery_questions and ask not in d.asked:
            ask = None
        if ask is not None:
            if ask not in d.asked:
                d.asked.append(ask)
            d.pending, d.asked_in = ask, turn
            question: dict[str, Any] = {"about": ask, "number": d.asked.index(ask) + 1, "of": len(DISCOVERY_QUESTIONS)}
            if ask == TASTE:
                options = list(facts.get("taste_options_say_ar") or [])[:2] or ["لحم", "دجاج"]
                question["options_say_ar"] = options
                question["or_something_else"] = True
                summary = f"تحب {options[0]} ولا {options[1]}، ولا عندك شي ثاني في بالك؟"
            elif ask == PEOPLE:
                summary = "الطلب لكم شخص؟"
            else:
                summary = "وكم الميزانية اللي تناسبك تقريبا؟"
            # Nothing to pitch yet: a dish picked from an open search is a guess.
            return {"narrowing_question": question, "results": [], "summary_ar": summary}, False

        state.discovery = None
        final = dict(args)
        if not taste_given:
            for key in _TASTE_ARGS:
                final[key] = d.taste.get(key) if key != "query" else d.taste.get("query", "")
        final["serves"] = final.get("serves") or state.party_size or None
        final["max_price"] = final.get("max_price") or state.budget or None
        if state.budget and (final["serves"] or 1) > 1 and final["max_price"] == state.budget:
            # His budget is for the whole table, not for one dish.
            final["max_price"], final["group_budget"] = None, state.budget
        if final != args:
            result = catalog.search_meals(**final)
            result.pop("discovery", None)
        if not result.get("results") and (final.get("offer_kind") or final.get("only_offers")):
            # He answered taste, people and budget; an offer filter he never
            # asked for («عزيمة» read as a family offer) must not empty the pick.
            result = catalog.search_meals(**{**final, "offer_kind": None, "only_offers": False})
            result.pop("discovery", None)
        logger.info("Discovery done: taste={} people={} budget={}", d.taste, state.party_size, state.budget)
        return result, True

    def _read_answer(self, question: str, args: dict[str, Any]) -> None:
        """His answer to the people or budget question, when the model did not pass it.

        The model is told to pass ``serves`` / ``max_price``; this only reads a
        plain number («أربعة», «خمسين») or «لحالي» out of the query as a backstop.
        """
        state = self.state
        text = catalog.normalize_ar(args.get("query") or "")
        number = said_number(text)
        if question == PEOPLE and not state.party_size:
            if any(p in text for p in _JUST_ME):
                state.party_size = 1
            elif number and 1 <= number <= 20:
                state.party_size = number
        elif question == BUDGET and not state.budget:
            if number and number >= 5:
                state.budget = float(number)

    @function_tool()
    async def show_meal(self, context: RunContext, meal_id: str) -> dict[str, Any]:
        """Open one dish on the caller's screen and get what to pitch.

        Call it when the caller picks a dish from results, asks to see one, or
        rejects the open dish and you move to another result. It opens a
        ``pitch`` offer, and sometimes an ``upgrade`` offer: ``bigger_size``
        (a bigger size for the people eating) or ``meal_deal`` (the same dish
        as a combo, with what it adds and costs_more / costs_less / same_price).
        Pitch the dish in one or two sentences — name, price, one reason that
        fits him — then ask one question. Do not offer extras yet.

        Args:
            meal_id: A meal_id from search_meals results (or from an offer).
        """
        meal = catalog.load_catalog().by_id(meal_id)
        if meal is None:
            raise ToolError(f"No dish with meal_id {meal_id!r}. Use a meal_id from search_meals results.")
        return await self._open_meal(context, meal)

    async def _open_meal(self, context: Any, meal: catalog.Meal) -> dict[str, Any]:
        state = self.state
        outcome = self.screen(await self.ui.show_meal(meal_id=meal.id, path=meal.page_path))
        logger.info("Tool show_meal meal_id={} ok={} reason={}", meal.id, outcome.ok, outcome.reason)
        if not outcome.ok:
            return {
                "ok": False,
                "error": "screen_did_not_respond",
                "meal_id": meal.id,
                "summary_ar": "الشاشة ما استجابت، خلني أحاول مرة ثانية",
            }
        state.last_meal_id = meal.id
        state.last_restaurant_id = meal.restaurant_id
        state.ui_path = meal.page_path
        state.pitched_meal_ids.add(meal.id)

        facts = offers.meal_facts(meal, healthy=state.health_conscious)
        if state.party_size and state.party_size > (meal.serves or 1):
            # One is not enough for the group: how many feed them all, and for how much.
            facts["quantity_for_group"] = quantity_for(meal, state.party_size)
            facts["group_total_say_ar"] = catalog.sar_words(facts["quantity_for_group"] * meal.effective_price)
        opened: list[Offer] = [Offer(PITCH, meal.id)]
        result: dict[str, Any] = {"ok": True, "dish": facts}
        # One step up per dish, and none once he has turned enough down.
        if meal.id not in state.upgrade_offered_ids and not state.cross_sell_fatigued:
            bigger = offers.upgrade_for(meal, party_size=state.party_size, budget=state.budget)
            deal = None
            if bigger is None and (state.party_size or 1) <= 1:
                deal = offers.meal_deal_for(meal, budget=state.budget)
            if bigger is not None:
                state.upgrade_offered_ids.add(meal.id)
                opened.append(Offer(UPGRADE, bigger.id, base_meal_id=meal.id))
                result["bigger_size"] = offers.upgrade_facts(bigger, meal)
            elif deal is not None:
                state.upgrade_offered_ids.add(meal.id)
                opened.append(Offer(UPGRADE, deal.id, base_meal_id=meal.id))
                result["meal_deal"] = offers.meal_deal_facts(deal, meal)
        if state.health_conscious:
            lighter = offers.lighter_alternative(meal)
            if lighter is not None:
                result["lighter_alternative"] = offers.meal_facts(lighter, healthy=True)
        addons = offers.addon_facts(meal)
        if addons:
            result["extras_available"] = addons[:4]
        state.open(*opened)
        result["open_offers"] = state.open_facts()
        if outcome.data and outcome.data.get("mocked"):
            result["mocked"] = True
        result["summary_ar"] = _pitch_summary(facts, result.get("meal_deal") or result.get("bigger_size"))
        return result
