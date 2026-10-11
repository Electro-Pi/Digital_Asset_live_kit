"""A whole order through a real LiveKit AgentSession, text only.

The LLM is scripted (``ScriptedLLM``): it answers each request from the chat
context, the way a model that follows the prompt would. Everything else is
production code — the session, the LLM node, tool execution, the offer flow,
the checkout task and its gates — against a fake browser. ``test_live_flow.py``
runs the same conversation against the real Coda model.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from livekit.agents import AgentSession, llm

from fakes import ScriptedLLM, Step, make_shop
from src.chat_view import items_since_last_user, last_user_text, parse_tool_output
from src.voice_agent.assistant import Assistant

SALAD = "protein-box-2"


def tool_names(tools: list[Any]) -> set[str]:
    return {getattr(getattr(t, "info", None), "name", None) for t in tools}


def outputs(ctx: llm.ChatContext) -> list[dict[str, Any]]:
    return [parse_tool_output(i.output) for i in ctx.items if i.type == "function_call_output"]


def outputs_this_turn(ctx: llm.ChatContext) -> list[dict[str, Any]]:
    return [parse_tool_output(i.output) for i in items_since_last_user(ctx) if getattr(i, "type", "") == "function_call_output"]


def latest_offer(ctx: llm.ChatContext, kind: str) -> str:
    for out in reversed(outputs(ctx)):
        offers = list(out.get("open_offers") or []) + list((out.get("opened") or {}).get("open_offers") or [])
        if out.get("next_offer"):
            offers.append(out["next_offer"])
        for offer in offers:
            if offer.get("kind") == kind:
                return offer["offer_id"]
    raise AssertionError(f"no {kind} offer in context")


def shop_model(ctx: llm.ChatContext, tools: list[Any]) -> Step:
    """What a prompt-following model does at each point of this call."""
    names = tool_names(tools)
    user = last_user_text(ctx)
    done = outputs_this_turn(ctx)
    if "place_order" in names:  # the checkout task
        if "اسمي" in user and not done:
            phone = re.search(r"05\d{8}", user).group(0)
            return Step(calls=[("set_customer_details", {"name": "محمد", "phone": phone, "address": "حي النرجس، الرياض"})])
        if user.startswith("ايه اعتمد") and not done:
            return Step(calls=[("place_order", {"summary_confirmed": True})])
        return Step(text="أَبْشِر، عَطْني اسْمَك وَجَوالَك وَعِنْوانَك.")
    if user == "ابي سلطة دجاج":
        if not done:
            return Step(calls=[("search_meals", {"query": "سلطة دجاج", "open_top": True})])
        dish = done[-1]["opened"]["dish"]
        return Step(text=f"هَذِي {dish['name_say_ar']} بـ {dish['price_say_ar']}، خَفيفَة وَتِشْبِع. تِبيها؟")
    if user == "ايه مع بيض مسلوق":
        if not done:
            return Step(calls=[("respond_to_offer", {"offer_id": latest_offer(ctx, "pitch"), "accepted": True, "addon_ids": ["بيض مسلوق"]})])
        nxt = done[-1]["next_offer"]
        return Step(text=f"تَم، وَش رايَك بـ {nxt['name_say_ar']} بـ {nxt['price_say_ar']} مَعْها؟")
    if user == "لا شكرا":
        if not done:
            return Step(calls=[("respond_to_offer", {"offer_id": latest_offer(ctx, "pair"), "accepted": False})])
        return Step(text="تَمام، نِثَبِّت الطَّلَب؟")
    if user == "ايه ثبت":
        if not done:
            return Step(calls=[("respond_to_offer", {"offer_id": "checkout", "accepted": True})])
        return Step(text="")
    return Step(text="مَعْذِرَة، ما فَهَمْت.")


@pytest.fixture
async def session_and_agent():
    shop = make_shop()
    agent = Assistant(instructions="test", shop=shop)
    async with AgentSession(llm=ScriptedLLM(shop_model)) as session:
        await session.start(agent)
        yield session, agent


async def test_a_whole_order(session_and_agent):
    session, agent = session_and_agent
    shop = agent.shop

    r1 = await session.run(user_input="ابي سلطة دجاج")
    r1.expect.next_event().is_function_call(name="search_meals")
    r1.expect.next_event().is_function_call_output()
    msg = r1.expect.next_event().is_message(role="assistant")
    assert "سَلَطَة دِجَاج" in msg.event().item.text_content or "سلطة دجاج" in msg.event().item.text_content
    assert shop.ui.path.startswith("/meals/")
    assert shop.state.open_offers[0].kind == "pitch"

    r2 = await session.run(user_input="ايه مع بيض مسلوق")
    r2.expect.next_event().is_function_call(name="respond_to_offer")
    r2.expect.next_event().is_function_call_output()
    r2.expect.next_event().is_message(role="assistant")
    assert len(shop.ui.cart) == 1 and shop.ui.cart[0]["addonIds"] == ["boiled-egg"]
    assert shop.state.open_offers[0].kind == "pair"

    r3 = await session.run(user_input="لا شكرا")
    r3.expect.next_event().is_function_call(name="respond_to_offer")
    assert shop.state.open_offers[0].id == "checkout"
    assert len(shop.ui.cart) == 1

    # Checkout: the tool hands the session to the checkout task, which asks
    # for the details itself.
    await session.run(user_input="ايه ثبت")
    assert shop.ui.path == "/checkout"
    assert shop.ui.orders == []

    await session.run(user_input="اسمي محمد وجوالي 0551234567 وعنواني حي النرجس الرياض")
    assert shop.state.summary_read is True
    assert shop.ui.orders == []  # nothing placed before his yes

    await session.run(user_input="ايه اعتمد")
    assert len(shop.ui.orders) == 1
    order = shop.ui.orders[0]
    assert order["name"] == "محمد" and order["phone"] == "0551234567"
    assert shop.state.order_placed is True
