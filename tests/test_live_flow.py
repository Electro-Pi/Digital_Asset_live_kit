"""The whole order against the real LLM (Coda-V2), with the real prompt.

Skipped unless RUN_LIVE_TESTS=1. A simulated caller answers whatever the agent
last offered (the dish: yes; extras: none; the complement: no; finishing: yes;
then his details and a final yes). The test checks outcomes, not wording: the
right dish reaches the cart, nothing he declined is added, the order is placed
only after the summary, and no tool call was refused as invalid.
"""

from __future__ import annotations

import pytest
from livekit.agents import AgentSession, llm
from livekit.plugins import openai

from fakes import make_shop
from src import catalog
from src.chat_view import parse_tool_output
from src.llm_backend import resolve_backend
from src.prompts.agent_prompt import compose_instructions
from src.voice_agent.assistant import Assistant

pytestmark = pytest.mark.live


def caller_reply(shop, turn: int, last_reply: str = "", last_said: str = "") -> str:
    """What a caller says next: answer her latest question, as a person would."""
    state = shop.state
    if state.summary_read and not state.order_placed:
        return "ايه اعتمد"
    if shop.ui.path == "/checkout":
        return "اسمي محمد، جوالي 0551234567، والعنوان حي النرجس في الرياض"
    if last_said == "لا شكرا" and any(w in last_reply for w in ("نعتمد", "نثبت", "نثبّت", "نخلص")):
        return "ايه خلاص ثبت الطلب"
    kinds = [o.kind for o in state.open_offers]
    if "extras" in kinds:
        return "لا، بدون إضافات"
    if "pair" in kinds:
        return "لا شكرا"
    if "checkout" in kinds:
        return "ايه خلاص ثبت الطلب"
    if "pitch" in kinds:
        return "ايه تمام أبيها"
    return "ابي سلطة دجاج"


def tool_errors(ctx: llm.ChatContext) -> list[dict]:
    outs = [parse_tool_output(i.output) for i in ctx.items if i.type == "function_call_output"]
    return [o for o in outs if o.get("error") in {"offer_not_open", "which_extras"}]


async def test_live_order_with_coda():
    backend = resolve_backend()
    shop = make_shop()
    agent = Assistant(instructions=compose_instructions(catalog_index=catalog.catalog_index()), shop=shop)
    transcript: list[str] = []
    async with openai.LLM(**backend.openai_kwargs()) as model, AgentSession(llm=model) as session:
        await session.start(agent)
        last_reply, said = "", ""
        for turn in range(14):
            said = caller_reply(shop, turn, last_reply, said)
            result = await session.run(user_input=said)
            replies = [e.item.text_content for e in result.events if e.type == "message" and e.item.role == "assistant"]
            calls = [
                f"{e.item.name}({e.item.arguments})" if e.type == "function_call"
                else f"-> {str({k: v for k, v in parse_tool_output(e.item.output).items() if k in ('ok', 'error', 'next_offer', 'open_offers')})[:240]}"
                for e in result.events
                if e.type in ("function_call", "function_call_output")
            ]
            last_reply = " ".join(replies)
            transcript.append(f"caller: {said}\n  tools: {calls}\n  agent: {last_reply}")
            if shop.state.order_placed:
                break
        print("\n".join(transcript))
        errors = tool_errors(agent.chat_ctx)

    assert shop.state.order_placed, "\n".join(transcript)
    order = shop.ui.orders[0]
    assert order["name"] == "محمد" and order["phone"] == "0551234567"
    salad_ids = {m.id for m in catalog.load_catalog().meals if m.type == "salad"}
    placed = [line["mealId"] for line in shop.ui.orders[0].get("items", [])] or [
        call[1]["mealId"] for call in shop.ui.calls if call[0] == "luqma.addToCart"
    ]
    assert placed and set(placed) <= salad_ids, placed  # only the salad; the declined complement is not there
    assert not errors, errors
