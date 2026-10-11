"""Short conversations against the real LLM (Coda-V2). Skipped unless RUN_LIVE_TESTS=1.

Each scenario checks what happened to the screen and the cart, never the
exact words, so a correct but differently phrased reply still passes.
"""

from __future__ import annotations

import re

import pytest
from livekit.agents import AgentSession
from livekit.plugins import openai

from fakes import make_shop
from src import catalog
from src.llm_backend import resolve_backend
from src.prompts.agent_prompt import compose_instructions
from src.voice_agent.assistant import Assistant

pytestmark = pytest.mark.live
CAT = catalog.load_catalog()


async def converse(*lines: str):
    """Run the caller's lines; return the shop and each turn's (calls, reply)."""
    shop = make_shop()
    agent = Assistant(instructions=compose_instructions(catalog_index=catalog.catalog_index()), shop=shop)
    turns = []
    async with openai.LLM(**resolve_backend().openai_kwargs()) as model, AgentSession(llm=model) as session:
        await session.start(agent)
        for line in lines:
            result = await session.run(user_input=line)
            calls = [(e.item.name, e.item.arguments) for e in result.events if e.type == "function_call"]
            reply = " ".join(e.item.text_content for e in result.events if e.type == "message" and e.item.role == "assistant")
            turns.append((calls, reply))
    for i, (calls, reply) in enumerate(turns):
        print(f"[{i}] {lines[i]}\n    calls={calls}\n    reply={reply}")
    return shop, turns


def strip_marks(text: str) -> str:
    return re.sub(r"[ً-ْٰ]", "", text or "")


def opened(shop) -> list[str]:
    return [p["mealId"] for m, p in shop.ui.calls if m == "luqma.showMeal"]


async def test_a_dish_we_do_not_sell_is_not_relabelled():
    shop, turns = await converse("ابي ناجتس")
    calls, reply = turns[0]
    assert any(name == "search_meals" for name, _ in calls)
    assert reply.strip()
    assert shop.ui.cart == []
    # Whatever she opened instead is not called ناجتس.
    assert all("ناجتس" not in CAT.by_id(m).name for m in opened(shop))


async def test_rejecting_the_dish_opens_a_different_one():
    shop, turns = await converse("ابي برجر", "لا ما أبيه، شف غيره")
    first = opened(shop)[0]
    assert len(opened(shop)) >= 2 and opened(shop)[-1] != first
    assert shop.ui.cart == []


async def test_a_group_order_passes_the_headcount():
    shop, turns = await converse("احنا اربعة ونبي شاورما")
    searches = [args for name, args in turns[0][0] if name == "search_meals"]
    assert searches and any('"serves": 4' in a or "لاربعة" in a or "اربعة" in a for a in searches)
    assert shop.state.party_size == 4 or any("اربع" in a for a in searches)
    top = CAT.by_id(opened(shop)[0])
    assert "شاورما" in top.name


async def test_a_healthy_request_opens_a_light_dish():
    # An open request: people and budget are asked first (his taste is given).
    shop, turns = await converse("ابي شي صحي وخفيف", "لي بس", "حدود أربعين")
    top = CAT.by_id(opened(shop)[-1])
    assert "light" in top.diet_tags or catalog.meal_is_healthy(top), top.name


async def test_asking_the_total_reads_the_cart():
    shop, turns = await converse("ابي سلطة دجاج", "ايه أبيها بدون إضافات", "كم الحساب الحين؟")
    assert len(shop.ui.cart) == 1
    names = [name for name, _ in turns[2][0]]
    assert "get_cart" in names or "checkout" not in names
    assert shop.ui.orders == []


async def test_a_dish_request_after_small_talk_is_searched_not_invented():
    # Live 2026-09-24: after small talk, «آكل كبسة» got «لقيت لك كبسة حاشي مع
    # الرز البشاور بتسعة وأربعين ريال» with no search; the dish does not exist.
    shop, turns = await converse("أخبارك يا سلمى", "الأول قولي ايه اللي تقدري تساعديني فيه؟", "لا بص انا كنت عايز آكل كبسة")
    calls, reply = turns[2]
    assert any(name in {"search_meals", "show_meal"} for name, _ in calls), calls
    assert "حاشي" not in reply and "بشاور" not in reply
    for m in opened(shop):
        assert "كبسة" in CAT.by_id(m).name


# ─── Selling (the sales playbook) ────────────────────────────────────────────

async def test_the_meal_deal_is_offered_and_can_be_taken():
    shop, turns = await converse("أبي شاورما دجاج من مذاق الشام", "ايه خلها وجبة كاملة")
    assert "وجب" in strip_marks(turns[0][1])  # she offered the combo in words
    accepted = [args for name, args in turns[1][0] if name == "respond_to_offer"]
    assert any('"upgrade:' in a for a in accepted), turns[1][0]
    assert "madhaq-alsham-shawarma" not in [line["mealId"] for line in shop.ui.cart]


async def test_a_budget_is_passed_and_respected():
    # An open request: taste, then people (the budget is already said).
    shop, turns = await converse("أبي غدا وميزانيتي ثلاثين ريال بس", "دجاج", "لي بس")
    assert shop.state.budget == 30
    assert CAT.by_id(opened(shop)[-1]).effective_price <= 30


async def test_a_hurried_caller_gets_the_fastest_kitchens():
    shop, turns = await converse("أبي برجر وأنا مستعجل مرة")
    searches = [args for name, args in turns[0][0] if name == "search_meals"]
    assert any('"fastest_delivery"' in a for a in searches), searches


async def test_a_complaint_gets_no_sales_pitch():
    shop, turns = await converse("طلبي اللي قبل وصل بارد، والله زعلان")
    calls, reply = turns[0]
    assert not any(name in {"search_meals", "show_meal"} for name, _ in calls), calls
    assert "ريال" not in reply and reply.strip()


async def test_an_open_request_gets_three_questions_before_a_dish():
    """Owner's ask 2026-09-27: taste (two options or something else), people, budget."""
    shop, turns = await converse("جوعان، وش ترشحين لي؟", "دجاج", "لشخصين", "حدود خمسين ريال")
    for i, topic in enumerate(("ولا", "شخص|اشخاص|أشخاص|كم", "ميزاني|حدود|سعر")):
        calls, reply = turns[i]
        assert not any(name == "show_meal" for name, _ in calls), (i, calls)
        assert "؟" in reply and re.search(topic, strip_marks(reply)), (i, reply)
    assert shop.state.party_size == 2 and shop.state.budget == 50
    dish = CAT.by_id(opened(shop)[-1])
    assert "chicken" in dish.protein_tags and dish.effective_price <= 50


async def test_after_the_recommended_dish_goes_in_she_offers_a_complement():
    """Live 2026-09-27: budget 20, kabsa + garlic in the cart, and no complement ever came."""
    shop, turns = await converse("اقترحي علي شي", "لحم", "لي بس", "حدود عشرين ريال", "ايه تمام", "ايه مع صوص ثوم")
    assert shop.ui.cart, turns
    assert shop.state.paired_meal_ids, "a complement was offered after the add"
    complement = CAT.by_id(next(iter(shop.state.complement_ids)))
    assert any(strip_marks(complement.name)[:3] in strip_marks(reply) for _, reply in turns[-2:]), turns[-2:]


async def test_a_grill_with_no_drinks_still_gets_a_complement():
    """Live 2026-09-27: «أرز باللحم» with garlic and pickles, then straight to «أثبت لك الطلب؟»."""
    shop, turns = await converse(
        "أبي أرز باللحم من لحم وفحم", "تمام يا ريت", "تمام ماشي وحطلي كمان مخلل وصوص ثوم", "ايه", "ايه"
    )
    assert "lahm-wa-fahm-10" in shop.state.paired_meal_ids, turns
    assert any("كولا" in strip_marks(reply) for _, reply in turns[2:4]), turns
    assert any("cola" in line["addonIds"] for line in shop.ui.cart), turns


async def test_saying_yes_to_a_dish_puts_it_in_the_cart():
    # Live 2026-09-27: three yeses to the kunafa, «نزلتها لك» each time, empty cart.
    shop, turns = await converse("أبي كنافة من حلويات الرياض", "طيب ماشي يا ريت", "ماشي")
    assert "halawiyat-riyadh-kunafa" in [line["mealId"] for line in shop.ui.cart], turns
