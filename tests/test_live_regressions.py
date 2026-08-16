"""Replay of the live session that failed on 2026-08-12, against the real LLM.

The user (in the CEO demo follow-up) hit three problems:
1. "ممكن نضيف Sauce Tomb" (ASR garble of صوص ثوم) — سلمى said the addon does
   not exist although the open meal has it, without calling any tool.
2. She answered "ثماني ثواني وأرجع لك" and then went silent.
3. Cart adds did not land on the first attempt.

Run: .venv/bin/python tests/test_live_regressions.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_luqma_flow import FakeUI, chat, openai_tools  # noqa: E402

import httpx  # noqa: E402

from agent import Assistant  # noqa: E402
from src import catalog  # noqa: E402
from src.prompts.agent_prompt import compose_instructions  # noqa: E402

# Any of these in a reply means سلمى promised to come back later instead of
# doing the work in the same turn — the exact failure the user described.
STALL_RE = re.compile(
    r"(أ?رجع لك|أ?رد عليك|انتظرني|لحظات? و|ثوان[يٍ] و)"
)


async def converse(turns: list[str], ui: FakeUI) -> list[dict]:
    assistant = Assistant(instructions="placeholder", ui=ui)  # type: ignore[arg-type]
    tools, dispatch = openai_tools(assistant)
    instructions = compose_instructions(catalog_index=catalog.catalog_index())
    messages: list[dict] = [{"role": "system", "content": instructions}]
    log: list[dict] = []

    async with httpx.AsyncClient() as client:
        for turn in turns:
            print(f"\nUSER : {turn}")
            messages.append({"role": "user", "content": turn})
            turn_tools: list[tuple[str, dict]] = []
            for _ in range(6):
                msg = await chat(client, messages, tools)
                calls = msg.get("tool_calls") or []
                messages.append(msg)
                if not calls:
                    break
                for call in calls:
                    fn = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"] or "{}")
                    turn_tools.append((fn, args))
                    print(f"  ⚙ {fn}({json.dumps(args, ensure_ascii=False)[:160]})")
                    try:
                        out = await dispatch[fn](None, **args)
                    except Exception as e:  # ToolError etc.
                        out = {"error": str(e)}
                    preview = json.dumps(out, ensure_ascii=False)
                    print(f"    → {preview[:180]}{'…' if len(preview) > 180 else ''}")
                    messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": preview}
                    )
            reply = (msg.get("content") or "").strip()
            print(f"سلمى  : {reply}")
            log.append({"user": turn, "reply": reply, "tools": turn_tools})
    return log


def assert_no_stall(log: list[dict]) -> None:
    for entry in log:
        stall = STALL_RE.search(entry["reply"])
        assert not stall, (
            f"stalled with {stall.group(0)!r} on turn {entry['user']!r}: "
            f"{entry['reply']!r}"
        )
        assert entry["reply"], f"empty (silent) reply on turn {entry['user']!r}"


async def scenario_addon_garble() -> None:
    """The kebab-offer session, with the ASR garbles verbatim."""
    ui = FakeUI()
    log = await converse(
        [
            "افتحي لي عرض الكباب اللي اشتري واحد والثاني مجانا من بيت الشواية",
            "ممكن نضيف Sauce Tomb مع الوجبة",
            "وخليها لي Spicy ومعاها كولا",
            "تمام ضيفيها في السلة وجبتين",
        ],
        ui,
    )

    assert_no_stall(log)

    cart_calls = [p for m, p in ui.calls if m == "luqma.addToCart"]
    assert cart_calls, f"nothing reached the cart; UI calls: {[m for m, _ in ui.calls]}"
    final = cart_calls[-1]
    assert final["mealId"] == "bayt-alshawaya-offer-3", final
    assert "garlic-sauce" in final["addonIds"], (
        f"صوص ثوم (Sauce Tomb) did not survive to the cart: {final}"
    )
    assert "cola" in final["addonIds"], f"كولا did not survive to the cart: {final}"
    assert final["spicy"] is True, final
    assert final["quantity"] == 2, final

    # She must never have claimed the addon is missing.
    for entry in log:
        assert "ما لقيت" not in entry["reply"] or "ثوم" not in entry["reply"], (
            f"claimed صوص ثوم missing: {entry['reply']!r}"
        )
    print("\nPASS scenario_addon_garble")


async def scenario_addons_question_answered_from_data() -> None:
    """Asking what addons exist must produce real names, in the same turn."""
    ui = FakeUI()
    log = await converse(
        [
            "وريني وجبة الكباب من بيت الشواية",
            "إيه الإضافات المتاحة ليها؟",
        ],
        ui,
    )
    assert_no_stall(log)

    reply = catalog.normalize_ar(log[-1]["reply"])
    named = [n for n in ("ثوم", "مخلل", "ارز", "كولا", "سلطه") if n in reply]
    assert named, f"no real addon names in the answer: {log[-1]['reply']!r}"
    print("\nPASS scenario_addons_question_answered_from_data")


async def scenario_first_attempt_cart() -> None:
    """A plain 'add it' must call add_to_cart in that same turn."""
    ui = FakeUI()
    log = await converse(
        [
            "ابغى برجر دجاج",
            "زين، ضيفيها للسلة",
        ],
        ui,
    )
    assert_no_stall(log)

    add_turn_tools = [fn for fn, _ in log[-1]["tools"]]
    assert "add_to_cart" in add_turn_tools, (
        f"'ضيفيها للسلة' did not call add_to_cart in the same turn: {add_turn_tools}"
    )
    assert ui.cart, "cart is still empty after an explicit add request"
    print("\nPASS scenario_first_attempt_cart")


async def main() -> None:
    await scenario_addon_garble()
    await scenario_addons_question_answered_from_data()
    await scenario_first_attempt_cart()
    print("\nALL LIVE-REGRESSION SCENARIOS PASS")


if __name__ == "__main__":
    asyncio.run(main())
