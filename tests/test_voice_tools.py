"""The shopping agent's tools, called directly against a fake browser."""

from __future__ import annotations

import pytest
from livekit.agents import ToolError

from fakes import FakeContext, FakeUI, make_shop
from src import catalog
from src.config import CONFIG
from src.session_state import CHECKOUT, Offer
from src.voice_agent.assistant import Assistant

CAT = catalog.load_catalog()
SALAD = "protein-box-2"  # سلطة دجاج مشوي, has extras and a drink complement
SHAWARMA = "sham-alaseel-3"  # شاورما دجاج, a family platter exists for 4


def agent(**ui):
    return Assistant(instructions="test", shop=make_shop(**ui))


def ui_of(a: Assistant) -> FakeUI:
    return a.shop.ui


def test_the_agent_has_eight_tools():
    names = sorted(t.info.name for t in agent().tools)
    assert names == sorted([
        "search_meals", "show_meal", "respond_to_offer", "get_cart",
        "update_cart", "open_page", "saved_meals", "checkout",
    ])


# ─── search_meals ────────────────────────────────────────────────────────────

async def test_search_is_read_only():
    a = agent()
    result = await a.search_meals(FakeContext(), query="سلطة")
    assert result["results"]
    assert ui_of(a).calls == []  # the screen did not move
    assert a.state.open_offers == []  # nothing is on offer yet


async def test_search_open_top_opens_and_pitches_the_first_result():
    a = agent()
    result = await a.search_meals(FakeContext(), query="سلطة سيزر", open_top=True)
    top = result["opened"]["dish"]["meal_id"]
    assert "سيزر" in CAT.by_id(top).name
    assert ui_of(a).methods() == ["luqma.showMeal"]
    assert a.state.find(f"pitch:{top}") is not None
    # The other results stay short and never repeat the opened dish.
    assert all(r["meal_id"] != top and set(r) <= {"meal_id", "name_say_ar", "price_say_ar", "type"} for r in result["results"])


async def test_search_records_what_the_caller_said():
    a = agent()
    await a.search_meals(FakeContext(), query="بيتزا", serves=4, healthy=True)
    assert a.state.party_size == 4
    assert a.state.health_conscious is True


@pytest.mark.parametrize("kwargs", [{"dish_type": "pizza"}, {"sort": "cheapest"}, {"offer_kind": "free"}])
async def test_search_rejects_values_outside_the_schema(kwargs):
    with pytest.raises(ToolError):
        await agent().search_meals(FakeContext(), query="برجر", **kwargs)


async def test_an_open_request_hands_back_a_question_not_a_dish():
    a = agent()
    result = await a.search_meals(FakeContext(), query="اقترحي علي شي", open_top=True)
    question = result["narrowing_question"]
    assert question["about"] == "taste" and question["or_something_else"] is True
    assert len(question["options_say_ar"]) == 2 and "دجاج" in question["options_say_ar"]
    assert result["results"] == [] and "opened" not in result
    assert ui_of(a).calls == []  # nothing opened on a guess


async def test_she_asks_taste_then_people_then_budget_and_recommends_on_all_three():
    a = agent()
    asked = []
    for kwargs in ({"query": "جوعان وش ترشحين"}, {"query": "دجاج"}, {"query": "لشخصين", "serves": 2}):
        result = await a.search_meals(FakeContext(), open_top=True, **kwargs)
        asked.append(result["narrowing_question"]["about"])
    assert asked == ["taste", "people", "budget"]
    last = await a.search_meals(FakeContext(), query="حدود خمسين", max_price=50)
    assert "narrowing_question" not in last
    dish = last["opened"]["dish"]  # opened without open_top: the questions are done
    meal = catalog.load_catalog().by_id(dish["meal_id"])
    assert "chicken" in meal.protein_tags and meal.effective_price <= 50
    assert (a.state.party_size, a.state.budget, a.state.discovery) == (2, 50, None)


async def test_a_question_is_not_asked_twice_in_one_turn():
    a = agent()
    a.shop.ledger.begin("turn-1")
    first = await a.search_meals(FakeContext(), query="اقترحي علي شي")
    again = await a.search_meals(FakeContext(), query="اقترحي علي شي")
    assert first["narrowing_question"] == again["narrowing_question"]
    a.shop.ledger.begin("turn-2")
    assert (await a.search_meals(FakeContext(), query="لحم"))["narrowing_question"]["about"] == "people"


async def test_what_he_already_said_is_not_asked():
    a = agent()
    result = await a.search_meals(FakeContext(), query="احنا ستة وش ترشحين", serves=6)
    assert result["narrowing_question"]["about"] == "taste"
    result = await a.search_meals(FakeContext(), query="لحم")
    assert result["narrowing_question"]["about"] == "budget"


@pytest.mark.parametrize("answer,party", [("أربعة", 4), ("لحالي", 1), ("٣", 3)])
async def test_a_plain_headcount_answer_is_read_when_serves_is_not_passed(answer, party):
    a = agent()
    await a.search_meals(FakeContext(), query="اقترحي علي شي")
    await a.search_meals(FakeContext(), query="دجاج")
    await a.search_meals(FakeContext(), query=answer)
    assert a.state.party_size == party


async def test_a_budget_answer_narrows_the_taste_instead_of_replacing_it():
    """«ثلاث» finds "Triple Stack"; an answer about money must not become the dish."""
    a = agent()
    for query in ("اقترحي علي شي", "مشاوي", "أربعة"):
        await a.search_meals(FakeContext(), query=query)
    result = await a.search_meals(FakeContext(), query="ثلاث مية ريال")
    assert a.state.budget == 300
    assert "مشاوي" in catalog.load_catalog().by_id(result["opened"]["dish"]["meal_id"]).name


async def test_no_budget_still_finishes_the_questions():
    a = agent()
    for query in ("اقترحي علي شي", "لحم", "أربعة"):
        await a.search_meals(FakeContext(), query=query)
    result = await a.search_meals(FakeContext(), query="مو مهم")
    assert "narrowing_question" not in result and result["opened"]["ok"] is True
    assert a.state.budget is None


async def test_leaving_the_choice_to_her_skips_the_questions():
    a = agent()
    result = await a.search_meals(FakeContext(), query="أي شي", pick_for_me=True, open_top=True)
    assert "narrowing_question" not in result and result["opened"]["ok"] is True
    assert a.state.discovery is None
    await a.search_meals(FakeContext(), query="اقترحي علي شي")
    result = await a.search_meals(FakeContext(), query="على ذوقك", pick_for_me=True)
    assert "narrowing_question" not in result and result["opened"]["ok"] is True


@pytest.mark.parametrize("kwargs", [{"query": "دجاج"}, {"query": "أبي برجر"}, {"query": "سلطة سيزر"}, {"query": "", "cuisine": "برجر"}])
async def test_a_named_dish_or_taste_gets_a_recommendation_not_a_question(kwargs):
    result = await agent().search_meals(FakeContext(), **kwargs)
    assert "narrowing_question" not in result and result["results"]


async def test_the_recommend_flag_starts_the_questions_when_the_query_lost_the_words():
    """Coda sent «دجاج» for «اقترحي علي شي دجاج»: the flag carries the ask."""
    a = agent()
    result = await a.search_meals(FakeContext(), query="دجاج", recommend=True)
    assert result["narrowing_question"]["about"] == "people"


async def test_a_group_hears_how_many_they_need():
    a = agent()
    for query in ("اقترحي علي شي", "شاورما"):
        await a.search_meals(FakeContext(), query=query)
    await a.search_meals(FakeContext(), query="عشرة", serves=10)
    result = await a.search_meals(FakeContext(), query="مو مهم")
    dish = result["opened"]["dish"]
    meal = catalog.load_catalog().by_id(dish["meal_id"])
    assert dish["quantity_for_group"] == -(-10 // (meal.serves or 1))


async def test_a_group_budget_is_for_the_whole_table():
    a = agent()
    for kwargs in ({"query": "وش ترشحين"}, {"query": "لحم"}, {"query": "عشرة", "serves": 10}):
        await a.search_meals(FakeContext(), **kwargs)
    result = await a.search_meals(FakeContext(), query="حدود ثلاث مية", max_price=300)
    meal = catalog.load_catalog().by_id(result["opened"]["dish"]["meal_id"])
    need = -(-10 // (meal.serves or 1))
    assert need * meal.effective_price <= 300


async def test_an_offer_filter_he_never_asked_for_does_not_empty_the_pick():
    a = agent()
    await a.search_meals(FakeContext(), query="عزيمة وش عندك", serves=10, offer_kind="family,combo", recommend=True)
    await a.search_meals(FakeContext(), query="مشاوي لحم", serves=10, offer_kind="family,combo")
    result = await a.search_meals(FakeContext(), query="مشاوي لحم", serves=10, offer_kind="family,combo", max_price=300)
    assert result["opened"]["ok"] is True
    assert "مشاوي" in catalog.load_catalog().by_id(result["opened"]["dish"]["meal_id"]).name
    assert result["over_budget"] is True  # ten mashawi cost more than 300, and she says so


async def test_an_empty_search_is_refused_not_read_as_an_open_request():
    with pytest.raises(ToolError):
        await agent().search_meals(FakeContext(), limit=8, max_price=0)


async def test_an_open_food_request_with_a_taste_asks_people_and_budget():
    a = agent()
    result = await a.search_meals(FakeContext(), query="أبي شي صحي للدوام", healthy=True)
    assert result["narrowing_question"]["about"] == "people"


async def test_an_empty_search_still_leaves_a_line_to_say():
    """An invented healthy filter emptied «سلطة سيزر» and she went silent."""
    result = await agent().search_meals(FakeContext(), query="سلطة سيزر", cuisine="صحي", healthy=True, open_top=True)
    assert result["results"] == [] and "سيزر" in result["summary_ar"]


async def test_the_pitch_line_carries_the_meal_deal_choice():
    result = await agent().search_meals(FakeContext(), query="شاورما دجاج", restaurant="مذاق الشام", open_top=True)
    assert "meal_deal" in result["opened"]
    assert "لحالها ولا" in result["opened"]["summary_ar"] and "زيادة" in result["opened"]["summary_ar"]


@pytest.mark.parametrize("meal_id", ["bayt-alshawaya-offer-1", "madhaq-alsham-shawarma"])
def test_a_complement_is_another_dish_cheaper_than_the_main(meal_id):
    """Live 2026-09-27: the grill combo's only candidates were its own add-ons, so none was offered."""
    from src import offers

    meal = CAT.by_id(meal_id)
    pair = offers.pair_for(meal)
    assert pair is not None and pair.id != meal.id and pair.effective_price <= meal.effective_price


def test_a_tight_budget_still_gets_a_small_complement():
    """Budget 20, an 18-riyal kabsa plus garlic: the laban used to be dropped and selling stopped."""
    from src import offers

    pair = offers.pair_for(CAT.by_id("kabsa-dar-mutabbaq"), room_left=0, budget=20)
    assert pair is not None and pair.effective_price <= 10


async def test_a_cheaper_re_search_after_a_pitch_starts_no_questions():
    a = agent()
    await a.search_meals(FakeContext(), query="مشكل مشاوي", open_top=True)
    result = await a.search_meals(FakeContext(), query="", max_price=40, dish_type="main")
    assert "narrowing_question" not in result


async def test_search_reports_a_missing_dish_without_substituting():
    result = await agent().search_meals(FakeContext(), query="ناجتس", open_top=True)
    assert result["matched_by"] == "none" and result["results"] == []
    assert "opened" not in result


# ─── show_meal ───────────────────────────────────────────────────────────────

async def test_show_meal_opens_a_pitch_with_facts_not_scripts():
    a = agent()
    result = await a.show_meal(FakeContext(), meal_id=SALAD)
    assert result["ok"] is True
    dish = result["dish"]
    assert dish["name_say_ar"] and dish["price_say_ar"] and dish["selling_points"]
    assert result["open_offers"][0] == {"offer_id": f"pitch:{SALAD}", "kind": "pitch", "meal_id": SALAD}
    assert "instruction" not in result and "say_to_user_ar" not in result
    assert "session" not in result


async def test_show_meal_unknown_id_is_an_actionable_error():
    with pytest.raises(ToolError, match="search_meals"):
        await agent().show_meal(FakeContext(), meal_id="no-such-dish")


async def test_group_gets_an_upgrade_offer_once():
    a = agent()
    a.state.party_size = 4
    first = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    kinds = [o["kind"] for o in first["open_offers"]]
    assert kinds == ["pitch", "upgrade"]
    assert first["bigger_size"]["serves_people"] == 4
    again = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    assert [o["kind"] for o in again["open_offers"]] == ["pitch"]


async def test_solo_caller_gets_the_meal_deal_not_a_bigger_size():
    result = await agent().show_meal(FakeContext(), meal_id=SHAWARMA)
    assert "bigger_size" not in result
    deal = result["meal_deal"]
    assert [o["kind"] for o in result["open_offers"]] == ["pitch", "upgrade"]
    assert CAT.by_id(deal["meal_id"]).combo_includes[0] == CAT.by_id(SHAWARMA).name
    assert deal["includes_say_ar"] and deal["costs_more_say_ar"] == "ريال واحد"


async def test_accepting_the_meal_deal_swaps_the_dish_for_the_combo():
    a = agent()
    shown = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    deal_id = shown["meal_deal"]["meal_id"]
    result = await a.respond_to_offer(FakeContext(), offer_id=f"upgrade:{deal_id}", accepted=True, no_extras=True)
    assert result["ok"] is True
    assert [line["mealId"] for line in ui_of(a).cart] == [deal_id]


async def test_no_step_up_above_the_budget():
    a = agent()
    await a.search_meals(FakeContext(), query="شاورما دجاج", max_price=25)
    assert a.state.budget == 25
    result = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    assert "meal_deal" not in result and "bigger_size" not in result
    assert [o["kind"] for o in result["open_offers"]] == ["pitch"]


async def test_no_step_up_after_he_has_declined_enough():
    a = agent()
    a.state.declined_offers = CONFIG.rules.max_declined_offers
    result = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    assert [o["kind"] for o in result["open_offers"]] == ["pitch"]


async def test_the_complement_respects_the_budget():
    a = agent()
    a.state.budget = CAT.by_id(SALAD).effective_price
    await a.show_meal(FakeContext(), meal_id=SALAD)
    added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    # The budget is spent: only something small, and she says it goes over.
    nxt = added["next_offer"]
    assert nxt["kind"] == "pair" and nxt["over_budget_by_say_ar"]
    assert nxt.get("addon_id") != "extra-salad"  # more salad is no complement to a salad


async def test_a_hurried_caller_sees_the_fastest_kitchens_first():
    result = await agent().search_meals(FakeContext(), query="برجر", sort="fastest_delivery")
    rows = result["results"]
    assert rows and all(r["delivery_minutes_say_ar"] for r in rows)
    fast = [bool(r.get("fast_delivery")) for r in rows]
    assert fast == sorted(fast, reverse=True)  # express kitchens lead


async def test_screen_failure_is_reported_not_faked():
    a = agent(fail={"luqma.showMeal"})
    result = await a.show_meal(FakeContext(), meal_id=SALAD)
    assert result["ok"] is False and result["error"] == "screen_did_not_respond"
    assert a.state.open_offers == []


async def test_mock_mode_marks_results(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr("src.voice_agent.base.CONFIG", replace(CONFIG, ui=replace(CONFIG.ui, mock_on_failure=True)))
    a = agent(fail={"luqma.showMeal"})
    result = await a.show_meal(FakeContext(), meal_id=SALAD)
    assert result["ok"] is True and result["mocked"] is True


# ─── respond_to_offer ────────────────────────────────────────────────────────

async def test_answer_to_an_offer_that_is_not_open_is_refused():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    result = await a.respond_to_offer(FakeContext(), offer_id="pitch:someone-else", accepted=True)
    assert result["ok"] is False and result["error"] == "offer_not_open"
    assert result["open_offers"][0]["offer_id"] == f"pitch:{SALAD}"
    assert ui_of(a).cart == []


async def test_yes_to_a_dish_with_extras_asks_about_extras_first():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    assert result["next_offer"]["offer_id"] == f"extras:{SALAD}"
    assert result["next_offer"]["extras_available"]
    assert ui_of(a).cart == []


async def test_yes_with_named_extras_adds_immediately_and_uninterruptibly():
    a = agent()
    ctx = FakeContext()
    await a.show_meal(ctx, meal_id=SALAD)
    result = await a.respond_to_offer(ctx, offer_id=f"pitch:{SALAD}", accepted=True, addon_ids=["boiled-egg"], quantity=2)
    assert result["ok"] and result["added"]["quantity"] == 2
    assert ui_of(a).cart == [{"mealId": SALAD, "quantity": 2, "addonIds": ["boiled-egg"], "spicy": False}]
    assert ctx.uninterruptible >= 1


async def test_extras_can_be_named_by_their_arabic_name():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"extras:{SALAD}", accepted=True, addon_ids=["بيض مسلوق"])
    assert result["ok"] and ui_of(a).cart[0]["addonIds"] == ["boiled-egg"]


async def test_unknown_extras_are_refused_with_the_real_list():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"extras:{SALAD}", accepted=True, addon_ids=["كافيار"])
    assert result["ok"] is False and result["error"] == "unknown_extras"
    assert ui_of(a).cart == []


async def test_no_to_extras_adds_the_dish_plain_and_offers_a_complement():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, quantity=3)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"extras:{SALAD}", accepted=False)
    assert ui_of(a).cart == [{"mealId": SALAD, "quantity": 3, "addonIds": [], "spicy": False}]
    nxt = result["next_offer"]
    assert nxt["kind"] == "pair" and nxt["name_say_ar"] and nxt["price_say_ar"]
    assert nxt["goes_with_say_ar"]
    # The complement is offered in words: the screen stays on the salad.
    assert ui_of(a).path == CAT.by_id(SALAD).page_path


async def test_saying_yes_twice_adds_once():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    first = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    second = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    assert first["ok"] and second["ok"] is False
    assert len(ui_of(a).cart) == 1


async def test_yes_to_the_complement_adds_it_without_another_pitch():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    pair_id = added["next_offer"]["offer_id"]
    result = await a.respond_to_offer(FakeContext(), offer_id=pair_id, accepted=True)
    assert result["ok"] and len(ui_of(a).cart) == 2
    assert result["next_offer"]["kind"] == "checkout"  # a complement gets none of its own


async def test_a_second_dish_in_the_call_gets_its_own_complement():
    """Live 2026-09-27: after the first dish she stopped suggesting anything."""
    a = agent()
    for meal_id in (SALAD, "madhaq-alsham-shawarma"):
        await a.show_meal(FakeContext(), meal_id=meal_id)
        added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{meal_id}", accepted=True, no_extras=True)
        assert added["next_offer"]["kind"] == "pair", meal_id


def test_every_main_dish_has_a_complement():
    """Live 2026-09-27: «أرز باللحم» from a grill with no drinks got none; its cola add-on is one."""
    from src import offers

    lonely = [
        m.id for m in CAT.meals
        if m.type == "main"
        and offers.pair_for(m, in_cart={m.id}) is None
        and offers.pair_addon_for(m) is None
    ]
    assert lonely == []


async def test_a_kitchen_without_drinks_offers_the_dishes_own_drink_on_its_line():
    a = agent()
    a.state.budget = 50
    meal_id = "lahm-wa-fahm-10"  # أرز باللحم; the grill sells only mains
    await a.show_meal(FakeContext(), meal_id=meal_id)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{meal_id}", accepted=True)
    added = await a.respond_to_offer(
        FakeContext(), offer_id=f"extras:{meal_id}", accepted=True, addon_ids=["garlic-sauce", "pickles"]
    )
    nxt = added["next_offer"]
    assert nxt["kind"] == "pair" and nxt["addon_id"] == "cola" and nxt["price_say_ar"]
    assert nxt["over_budget_by_say_ar"]  # 44 + 3 + 3 fills the 50; she says the cola goes over
    result = await a.respond_to_offer(FakeContext(), offer_id=nxt["offer_id"], accepted=True)
    assert result["ok"] and result["next_offer"]["kind"] == "checkout"
    assert ui_of(a).cart == [
        {"mealId": meal_id, "quantity": 1, "addonIds": ["garlic-sauce", "pickles", "cola"], "spicy": False}
    ]


async def test_no_to_the_complement_moves_to_checkout_and_counts_the_decline():
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    result = await a.respond_to_offer(FakeContext(), offer_id=added["next_offer"]["offer_id"], accepted=False)
    assert result["next_offer"]["offer_id"] == "checkout"
    assert a.state.declined_offers == 1


async def test_declining_the_dish_returns_other_results():
    a = agent()
    await a.search_meals(FakeContext(), query="سلطة", open_top=True)
    first = a.state.open_offers[0]
    result = await a.respond_to_offer(FakeContext(), offer_id=first.id, accepted=False)
    assert result["other_results"] and all(r["meal_id"] != first.meal_id for r in result["other_results"])
    assert a.state.open_offers == []


async def test_upgrade_yes_takes_the_bigger_dish():
    a = agent()
    a.state.party_size = 4
    shown = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    upgrade = next(o for o in shown["open_offers"] if o["kind"] == "upgrade")
    result = await a.respond_to_offer(FakeContext(), offer_id=upgrade["offer_id"], accepted=True, no_extras=True)
    assert ui_of(a).cart[0]["mealId"] == upgrade["meal_id"]
    assert result["ok"]


async def test_upgrade_no_keeps_the_dish_on_offer():
    a = agent()
    a.state.party_size = 4
    shown = await a.show_meal(FakeContext(), meal_id=SHAWARMA)
    upgrade = next(o for o in shown["open_offers"] if o["kind"] == "upgrade")
    result = await a.respond_to_offer(FakeContext(), offer_id=upgrade["offer_id"], accepted=False)
    assert [o["offer_id"] for o in result["open_offers"]] == [f"pitch:{SHAWARMA}"]


async def test_cross_selling_stops_after_enough_declines():
    a = agent()
    a.state.declined_offers = CONFIG.rules.max_declined_offers
    await a.show_meal(FakeContext(), meal_id=SALAD)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    assert result["next_offer"]["kind"] == "checkout"


async def test_checkout_offer_no_keeps_shopping():
    a = agent()
    a.state.open(Offer(CHECKOUT))
    result = await a.respond_to_offer(FakeContext(), offer_id="checkout", accepted=False)
    assert result["keep_shopping"] is True and a.state.open_offers == []


# ─── cart ────────────────────────────────────────────────────────────────────

async def _with_salad_in_cart(a: Assistant) -> None:
    await a.show_meal(FakeContext(), meal_id=SALAD)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, addon_ids=["water"])


async def test_get_cart_reads_the_browser_totals():
    a = agent()
    await _with_salad_in_cart(a)
    cart = await a.get_cart(FakeContext())
    meal = CAT.by_id(SALAD)
    water = next(float(x["price"]) for x in meal.addons if x["id"] == "water")
    assert cart["subtotal_sar"] == pytest.approx(meal.effective_price + water)
    assert cart["items"][0]["addons_say_ar"]


async def test_update_cart_changes_and_removes_lines():
    a = agent()
    await _with_salad_in_cart(a)
    ctx = FakeContext()
    more = await a.update_cart(ctx, meal_id=SALAD, quantity=3)
    assert more["ok"] and ui_of(a).cart[0]["quantity"] == 3 and ctx.uninterruptible == 1
    gone = await a.update_cart(FakeContext(), meal_id=SALAD, quantity=0)
    assert gone["removed"] and ui_of(a).cart == []


async def test_update_cart_on_a_dish_not_in_the_cart_says_so():
    result = await agent().update_cart(FakeContext(), meal_id=SALAD, quantity=2)
    assert result["ok"] is False and result["error"] == "not_in_cart"


@pytest.mark.parametrize("qty", [-1, CONFIG.rules.max_line_quantity + 1])
async def test_update_cart_bounds(qty):
    with pytest.raises(ToolError):
        await agent().update_cart(FakeContext(), meal_id=SALAD, quantity=qty)


async def test_cart_change_unreads_the_summary():
    a = agent()
    await _with_salad_in_cart(a)
    a.state.summary_read = True
    await a.update_cart(FakeContext(), meal_id=SALAD, quantity=2)
    assert a.state.summary_read is False


# ─── open_page and saved_meals ───────────────────────────────────────────────

async def test_open_restaurant_page():
    a = agent()
    result = await a.open_page(FakeContext(), page="restaurant", restaurant="أصيل برجر")
    assert result["ok"] and ui_of(a).path == "/restaurants/aseel-burger"
    assert result["restaurant"]["popular_dishes"]
    assert a.state.last_restaurant_id == "aseel-burger"


async def test_open_offers_with_a_kind_filters_and_lists():
    a = agent()
    result = await a.open_page(FakeContext(), page="offers", offer_kind="family")
    assert result["ok"] and "kind=family" in ui_of(a).path
    assert result["offers"] and all(r.get("offer_kind") == "family" for r in result["offers"])


async def test_open_page_accepts_arabic_aliases_and_rejects_nonsense():
    a = agent()
    assert (await a.open_page(FakeContext(), page="السلة"))["page"] == "cart"
    with pytest.raises(ToolError):
        await a.open_page(FakeContext(), page="kitchen-sink")


async def test_saved_meals_round_trip():
    a = agent()
    ctx = FakeContext()
    assert (await a.saved_meals(ctx, action="save", meal_id=SALAD))["ok"]
    listed = await a.saved_meals(FakeContext(), action="list")
    assert [i["meal_id"] for i in listed["items"]] == [SALAD]
    assert (await a.saved_meals(FakeContext(), action="clear"))["ok"]
    assert (await a.saved_meals(FakeContext(), action="list"))["items"] == []
    assert ctx.uninterruptible == 1


async def test_saving_a_restaurant_is_an_actionable_error():
    with pytest.raises(ToolError, match="restaurant"):
        await agent().saved_meals(FakeContext(), action="save", meal_id="aseel-burger")


async def test_saying_yes_to_finish_works_while_another_offer_is_open():
    """Live 2026-09-24: the complement was declined in words, never recorded;
    «ايه ثبت» must still reach checkout."""
    from src.voice_agent import checkout as checkout_module
    from test_checkout import _StubTask

    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    assert added["next_offer"]["kind"] == "pair"
    original = checkout_module.CheckoutTask
    checkout_module.CheckoutTask = _StubTask
    try:
        result = await a.respond_to_offer(FakeContext(), offer_id="checkout", accepted=True)
    finally:
        checkout_module.CheckoutTask = original
    assert ui_of(a).path == "/checkout" and result.output["order_placed"] is True


async def test_finishing_with_an_empty_cart_keeps_the_open_offer():
    # Live 2026-09-27: «نثبت؟» on an empty cart closed the kunafa's pitch, so
    # every later «ايه» to it came back offer_not_open.
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    result = await a.respond_to_offer(FakeContext(), offer_id="checkout", accepted=True)
    assert result["ok"] is False and result["error"] == "cart_empty"
    assert a.state.find(f"pitch:{SALAD}") is not None
    added = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True, no_extras=True)
    assert added["ok"] is True and [line["mealId"] for line in ui_of(a).cart] == [SALAD]


async def test_a_closed_offer_says_how_to_reopen_the_dish():
    result = await agent().respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    assert result["error"] == "offer_not_open"
    assert f"show_meal(meal_id='{SALAD}')" in result["fix"]


async def test_answering_an_answered_pitch_points_at_the_open_extras_offer():
    # Live 2026-09-27: «ماشي» to «نزيدها صوص؟» came back as the kunafa's pitch again.
    a = agent()
    await a.show_meal(FakeContext(), meal_id=SALAD)
    await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    result = await a.respond_to_offer(FakeContext(), offer_id=f"pitch:{SALAD}", accepted=True)
    assert result["error"] == "offer_not_open"
    assert f"'extras:{SALAD}'" in result["fix"] and "show_meal" not in result["fix"]
