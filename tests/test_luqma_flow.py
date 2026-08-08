"""End-to-end harness: real prompt, real tools, real LLM, stubbed browser.

Runs لقمة through scripted conversations in text. The voice pipeline is the
slow, flaky part of this agent and it is not what changes when the catalog or
the prompt changes — so the loop worth having is this one: it exercises the
exact tool schemas the model will see, the exact search code, and the exact
system prompt, in a few seconds instead of a phone call.

The frontend is stubbed by ``FakeUI``, which records the RPCs an agent would
send to the browser and answers them the way the real handlers must. That makes
the UI contract testable before any frontend code exists.

Run:  .venv/bin/python tests/test_luqma_flow.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv(".env.local")

from livekit.agents.llm import utils as llm_utils  # noqa: E402

from agent import Assistant  # noqa: E402
from src import catalog  # noqa: E402
from src.prefs import build_prefs_context  # noqa: E402
from src.prompts.agent_prompt import compose_instructions  # noqa: E402
from src.ui_bridge import UIResult  # noqa: E402

LLM_BASE_URL = os.environ["LLM_BASE_URL"].rstrip("/")
LLM_MODEL = os.environ["LLM_MODEL"]
LLM_API_KEY = os.environ["LLM_API_KEY"]

TASHKEEL = set(range(0x064B, 0x0653))


def _is_letter(c: str) -> bool:
    return 0x0620 <= ord(c) <= 0x064A


def audit_tashkeel(text: str) -> tuple[int, int, list[str]]:
    """Words, words carrying tashkeel, and words with a marked final letter."""
    words = re.findall(r"[ؠ-ٟ]+", text)
    marked, violations = 0, []
    for w in words:
        idx = [i for i, c in enumerate(w) if _is_letter(c)]
        if not idx:
            continue
        if any(ord(c) in TASHKEEL for c in w):
            marked += 1
        if idx[-1] + 1 < len(w) and ord(w[idx[-1] + 1]) in TASHKEEL:
            violations.append(w)
    return len(words), marked, violations


class FakeUI:
    """Stands in for the browser. Mirrors what FRONTEND_INTEGRATION.md asks for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cart: list[dict[str, Any]] = []
        self.current_path = "/"
        self.available = True

    async def call(self, method: str, payload: dict[str, Any]) -> UIResult:
        self.calls.append((method, payload))
        if method == "luqma.navigate":
            self.current_path = payload["path"]
            return UIResult(ok=True, data={"path": self.current_path})
        if method == "luqma.showMeal":
            self.current_path = payload["path"]
            return UIResult(ok=True, data={"path": self.current_path})
        if method == "luqma.addToCart":
            self.cart.append(payload)
            return UIResult(ok=True, data={"cartCount": len(self.cart)})
        if method == "luqma.getCart":
            return UIResult(ok=True, data={"items": self.cart, "count": len(self.cart)})
        return UIResult(ok=False, reason="frontend_handler_missing")

    async def navigate(self, path: str) -> UIResult:
        return await self.call("luqma.navigate", {"path": path})

    async def show_meal(self, meal_id: str, path: str, reason: str = "") -> UIResult:
        return await self.call(
            "luqma.showMeal", {"mealId": meal_id, "path": path, "reason": reason}
        )

    async def add_to_cart(self, *, meal_id, quantity=1, spicy=False, addon_ids=None) -> UIResult:
        return await self.call(
            "luqma.addToCart",
            {"mealId": meal_id, "quantity": quantity, "spicy": spicy,
             "addonIds": addon_ids or []},
        )

    async def get_cart(self) -> UIResult:
        return await self.call("luqma.getCart", {})


class BrokenUI(FakeUI):
    """A frontend that has not shipped the handlers yet."""

    async def call(self, method: str, payload: dict[str, Any]) -> UIResult:
        self.calls.append((method, payload))
        return UIResult(ok=False, reason="frontend_handler_missing")


def openai_tools(assistant: Assistant) -> tuple[list[dict], dict]:
    """The exact schemas the LLM sees, plus a name → callable dispatch map."""
    schemas, dispatch = [], {}
    for tool in assistant.tools:
        # Already returns the {"type": "function", "function": {...}} wrapper.
        schema = llm_utils.build_legacy_openai_schema(tool, internally_tagged=False)
        schemas.append(schema)
        dispatch[schema["function"]["name"]] = tool
    return schemas, dispatch


async def chat(client: httpx.AsyncClient, messages: list[dict], tools: list[dict]) -> dict:
    resp = await client.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json={
            "model": LLM_MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.6,
            "max_tokens": 400,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


async def run_conversation(title: str, turns: list[str], *, prefs: str | None, ui: FakeUI) -> dict:
    assistant = Assistant(instructions="placeholder", ui=ui)  # type: ignore[arg-type]
    tools, dispatch = openai_tools(assistant)

    instructions = compose_instructions(
        catalog_index=catalog.catalog_index(),
        prefs_context=build_prefs_context(prefs) if prefs else "",
    )
    messages: list[dict] = [{"role": "system", "content": instructions}]

    stats = {"words": 0, "marked": 0, "violations": [], "tool_calls": []}
    print("\n" + "#" * 78)
    print("#", title)
    print("#" * 78)

    async with httpx.AsyncClient() as client:
        for turn in turns:
            print(f"\nUSER : {turn}")
            messages.append({"role": "user", "content": turn})

            for _ in range(6):  # tool loop
                msg = await chat(client, messages, tools)
                calls = msg.get("tool_calls") or []
                messages.append(msg)
                if not calls:
                    break
                for call in calls:
                    fn = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"] or "{}")
                    stats["tool_calls"].append(fn)
                    print(f"  ⚙ {fn}({json.dumps(args, ensure_ascii=False)})")
                    try:
                        out = await dispatch[fn](None, **args)
                    except Exception as e:  # ToolError etc.
                        out = {"error": str(e)}
                    preview = json.dumps(out, ensure_ascii=False)
                    print(f"    → {preview[:200]}{'…' if len(preview) > 200 else ''}")
                    messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": preview}
                    )

            reply = (msg.get("content") or "").strip()
            print(f"لقمة  : {reply}")
            w, m, v = audit_tashkeel(reply)
            stats["words"] += w
            stats["marked"] += m
            stats["violations"] += v

    return stats


CARES = json.dumps(
    {
        "voice": "female",
        "prefs": {
            "done": True, "skipped": False, "schemaVersion": 8,
            "calories": {
                "preference": "care", "cares_about_calories": True,
                "label_ar": "يهتم بالسعرات الغذائية",
                "label_en": "User cares about calorie counts",
            },
        },
    },
    ensure_ascii=False,
)


async def main() -> None:
    totals = {"words": 0, "marked": 0, "violations": [], "tool_calls": []}

    ui = FakeUI()
    s = await run_conversation(
        "الطلب الأساسي: وجبة قليلة السعرات وفيها لحم",
        [
            "هلا، ابغى وجبة قليلة السعرات وتكون لحم",
            "تحت خمسين ريال",
            "زين، ودي فيها",
            "ابغى معها بطاطس وكولا",
        ],
        prefs=CARES,
        ui=ui,
    )
    print(f"\n  [ui] path={ui.current_path} cart={ui.cart}")
    assert ui.current_path.startswith("/meals/"), "should have navigated to a meal page"
    for k in totals:
        totals[k] += s[k] if not isinstance(totals[k], list) else s[k]

    ui2 = FakeUI()
    s = await run_conversation(
        "سؤال مفتوح — لازم تسأل سؤال واحد وتبحث بدري",
        ["جوعان، وش تنصحيني؟", "شي بحري", "كم سعراته؟"],
        prefs=None,
        ui=ui2,
    )
    for k in totals:
        totals[k] += s[k] if not isinstance(totals[k], list) else s[k]

    ui3 = BrokenUI()
    s = await run_conversation(
        "الفرونت إند ما نفذ الـ RPC — لازم تعترف ما قدرت",
        ["ابغى برجر دجاج، افتحيه لي"],
        prefs=None,
        ui=ui3,
    )
    for k in totals:
        totals[k] += s[k] if not isinstance(totals[k], list) else s[k]

    print("\n" + "=" * 78)
    words, marked = totals["words"], totals["marked"]
    print(f"tashkeel coverage : {marked}/{words} ({marked / max(words,1):.0%})")
    print(f"final-letter slips: {len(totals['violations'])} ({len(totals['violations'])/max(words,1):.1%})")
    print(f"tool calls        : {totals['tool_calls']}")


if __name__ == "__main__":
    asyncio.run(main())
