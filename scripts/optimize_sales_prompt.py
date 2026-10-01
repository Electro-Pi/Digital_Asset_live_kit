"""Optimise سلمى's sales playbook with GEPA against live conversations.

Run it from the optimiser venv, never the agent's (dspy upgrades aiohttp,
which livekit pins):

    uv venv --python 3.12 .venv-optim
    uv pip install --python .venv-optim/bin/python -r <(uv pip freeze --python .venv/bin/python) dspy
    set -a; source .env.local; set +a
    .venv-optim/bin/python scripts/optimize_sales_prompt.py baseline      # score old vs current
    .venv-optim/bin/python scripts/optimize_sales_prompt.py optimize --budget 240

What is optimised: only ``SALES_PLAYBOOK`` in ``src/prompts/agent_prompt.py``.
The tool, voice and dialect sections stay fixed — tests and the claim guards
depend on their wording.

How a candidate is scored: each scenario is a short call run through the real
``Assistant`` (real tools, real guards, ``FakeUI`` browser) on the student
model (Coda-V2 by default, the production model). The score mixes
- hard checks on what happened (tools called, arguments, cart, screen),
- voice rules (one question, short, no Latin letters or emoji),
- a Gemini judge reading the transcript against a sales rubric,
and the failed checks plus the judge's critique are the feedback GEPA's
reflection model (Gemini) reads to rewrite the playbook.

GEPA here is the engine ``dspy.GEPA`` runs on (the ``gepa`` package). The
program under optimisation is a tool-calling LiveKit agent rather than a
``dspy.Predict``, so it plugs in through a custom adapter; the reflection
model is a ``dspy.LM``.

The Coda gateway is shared with live speech: keep ``--parallel`` at 6 or
below, and a 503 is load, not a model failure (it is retried).
"""

from __future__ import annotations

# livekit first: importing dspy before it leaves openai half-initialised.
import livekit.agents  # noqa: F401  isort: skip

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from livekit.agents import AgentSession, llm  # noqa: E402
from livekit.plugins import openai as lk_openai  # noqa: E402
from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level=os.environ.get("OPTIM_LOG_LEVEL", "ERROR"))

# The agent catches an LLM failure and speaks a recovery line («LLM request
# failed»), so the call looks fine. Each conversation collects those here
# (the context variable follows it into the session's tasks) and is retried.
from contextvars import ContextVar  # noqa: E402

_CALL_ERRORS: ContextVar[Optional[list]] = ContextVar("call_errors", default=None)


def _collect_llm_failures(message) -> None:
    errors = _CALL_ERRORS.get()
    if errors is not None:
        errors.append(str(message.record["message"])[:200])


logger.add(_collect_llm_failures, level="ERROR", filter=lambda r: "LLM request failed" in r["message"])

import dspy  # noqa: E402
import gepa  # noqa: E402
from gepa.core.adapter import EvaluationBatch  # noqa: E402

from fakes import make_shop  # noqa: E402
from src import catalog, flow_say  # noqa: E402
from src.llm_backend import GEMINI_BASE_URL, resolve_backend  # noqa: E402
from src.prompts import agent_prompt  # noqa: E402
from src.voice_agent.assistant import Assistant  # noqa: E402

CAT = catalog.load_catalog()
COMPONENT = "sales_playbook"
OUT_DIR = ROOT / "logs" / "sales-optim"


# ─── Scenarios ───────────────────────────────────────────────────────────────


@dataclass
class Call:
    """What one conversation did: per turn, the tool calls, outputs and reply."""

    lines: list[str]
    turns: list[dict[str, Any]] = field(default_factory=list)
    shop: Any = None
    error: str = ""

    def calls(self, turn: int) -> list[tuple[str, dict[str, Any]]]:
        return self.turns[turn]["calls"] if turn < len(self.turns) else []

    def called(self, turn: int, name: str) -> list[dict[str, Any]]:
        return [args for n, args in self.calls(turn) if n == name]

    def reply(self, turn: int) -> str:
        return self.turns[turn]["reply"] if turn < len(self.turns) else ""

    def opened(self) -> list[str]:
        return [p["mealId"] for m, p in self.shop.ui.calls if m == "luqma.showMeal"]

    def opened_in(self, turn: int) -> list[str]:
        return self.turns[turn]["opened"] if turn < len(self.turns) else []

    def cart_ids(self) -> list[str]:
        return [line["mealId"] for line in self.shop.ui.cart]


Check = Callable[[Call], tuple[bool, str]]


@dataclass
class Scenario:
    id: str
    lines: list[str]
    expect: str  # what a great salesperson does here, for the judge
    checks: list[Check]


def check(description: str, fn: Callable[[Call], bool]) -> Check:
    def run(call: Call) -> tuple[bool, str]:
        try:
            return bool(fn(call)), description
        except Exception as exc:  # noqa: BLE001 — a check that cannot run failed
            return False, f"{description} (check error: {exc})"
    return run


def price_of(meal_id: str) -> float:
    meal = CAT.by_id(meal_id)
    return meal.effective_price if meal else 0.0


def searched(call: Call, turn: int) -> bool:
    return bool(call.called(turn, "search_meals"))


def opened_a_dish(turn: int) -> Check:
    return check(f"turn {turn + 1}: a dish is opened on the screen (show_meal or open_top)", lambda c: bool(c.opened_in(turn)))


def cart_empty() -> Check:
    return check("nothing is added to the cart without a yes", lambda c: c.cart_ids() == [])


def asks_before_opening(turn: int) -> Check:
    return check(f"turn {turn + 1}: asks a question instead of opening a dish", lambda c: not c.opened_in(turn) and "؟" in c.reply(turn))


# What each of the three discovery questions must be about (tool's `about`).
_TOPIC = {
    "taste": (r"ولا.*(ثاني|غير|ثانيه)", "asks his taste with two options «or something else»"),
    "people": (r"شخص|اشخاص|أشخاص|كم واحد|عدد", "asks how many people"),
    "budget": (r"ميزاني|حدود|سعر|تصرف", "asks his budget"),
}


def asks_about(turn: int, topic: str) -> Check:
    pattern, what = _TOPIC[topic]
    return check(
        f"turn {turn + 1}: opens no dish and {what}",
        lambda c: not c.opened_in(turn) and "؟" in c.reply(turn) and re.search(pattern, strip_marks(c.reply(turn))) is not None,
    )


def no_new_pitch(turn: int) -> Check:
    return check(
        f"turn {turn + 1}: no new dish is pushed (no search_meals / show_meal)",
        lambda c: not c.opened_in(turn) and not c.called(turn, "search_meals"),
    )


def says(turn: int, pattern: str, what: str) -> Check:
    return check(f"turn {turn + 1}: the reply {what}", lambda c: re.search(pattern, strip_marks(c.reply(turn))) is not None)


def strip_marks(text: str) -> str:
    return re.sub(r"[ً-ْٰ]", "", text or "")


SHAWARMA = "madhaq-alsham-shawarma"
SCENARIOS: list[Scenario] = [
    Scenario(
        "hungry_open", ["جوعان، وش ترشحين لي؟"],
        "A recommendation request: she searches, the tool returns narrowing_question about taste, and she opens NO dish. She asks ONE short question with the tool's two options plus «or something else» («تحب لحم ولا دجاج، ولا في بالك شي ثاني؟»).",
        [asks_about(0, "taste"), cart_empty()],
    ),
    Scenario(
        "discover_then_recommend", ["ممكن تقولي لي ايه احسن حاجة ممكن اكلها انهارده؟ اقترحها عليا", "دجاج", "لشخصين", "حدود خمسين ريال"],
        "Three questions, one per turn, each from the tool's narrowing_question: taste (two options or something else), how many people, budget. Then, after the budget, she recommends ONE chicken dish within 50 riyals (the tool opens it) and ties the reason to all three answers («دامك تبي دجاج لشخصين وفي حدود خمسين…»).",
        [
            asks_about(0, "taste"),
            asks_about(1, "people"),
            asks_about(2, "budget"),
            check("the headcount and budget are passed", lambda c: c.shop.state.party_size == 2 and c.shop.state.budget == 50),
            opened_a_dish(3),
            check("the dish fits his answers (chicken, ≤50)", lambda c: bool(c.opened()) and "chicken" in CAT.by_id(c.opened()[-1]).protein_tags and price_of(c.opened()[-1]) <= 50),
        ],
    ),
    Scenario(
        "recommend_with_taste", ["اقترحي علي شي دجاج", "ثلاثة", "مية ريال"],
        "He asked for a recommendation and already gave his taste (chicken): she does not ask his taste again. Turn 1 asks how many people, turn 2 his budget; then she recommends a chicken dish for three within 100 riyals.",
        [
            asks_about(0, "people"),
            asks_about(1, "budget"),
            check("the dish fits his answers (chicken, ≤100)", lambda c: bool(c.opened()) and "chicken" in CAT.by_id(c.opened()[-1]).protein_tags and price_of(c.opened()[-1]) <= 100),
        ],
    ),
    Scenario(
        "answers_all_at_once", ["رشحي لي شي لحم لشخصين بحدود ثمانين ريال"],
        "Taste, people and budget are all in his first sentence: no question at all. She passes serves=2 and max_price=80 and recommends one meat dish in the same turn, tying the reason to all three.",
        [
            opened_a_dish(0),
            check("the dish fits (meat, ≤80)", lambda c: bool(c.opened()) and "meat" in CAT.by_id(c.opened()[-1]).protein_tags and price_of(c.opened()[-1]) <= 80),
        ],
    ),
    Scenario(
        "you_choose", ["اقترحي علي أي شي، اختاري أنتِ"],
        "He hands her the choice: no questions, she searches and opens one popular dish with its price and one reason.",
        [check("searches the catalog", lambda c: searched(c, 0)), opened_a_dish(0), says(0, r"ريال", "names a price")],
    ),
    Scenario(
        "meal_deal_pitch", ["أبي شاورما دجاج من مذاق الشام"],
        "Opens the chicken shawarma; the tool returns a meal_deal (full meal for one riyal more). She pitches the dish and offers the meal deal as a single two-choice question (alone or as a meal), naming what the meal adds.",
        [opened_a_dish(0), says(0, r"وجب", "offers the meal deal"), cart_empty()],
    ),
    Scenario(
        "meal_deal_accept", ["أبي شاورما دجاج من مذاق الشام", "ايه خلها وجبة كاملة"],
        "Accepts the upgrade offer (respond_to_offer on the upgrade offer id). The combo, not the plain sandwich, goes toward the cart.",
        [
            check("the upgrade offer is accepted", lambda c: any(a.get("offer_id", "").startswith("upgrade:") and a.get("accepted") for a in c.called(1, "respond_to_offer"))),
            check("the plain sandwich is not in the cart", lambda c: SHAWARMA not in c.cart_ids()),
        ],
    ),
    Scenario(
        "meal_deal_decline", ["أبي شاورما دجاج من مذاق الشام", "لا، لحالها بس"],
        "He wants the sandwich alone: she takes the dish (pitch accepted or upgrade declined then the dish), no combo in the cart, no pressure to reconsider.",
        [
            check("no combo goes in the cart", lambda c: all(not CAT.by_id(m).is_offer for m in c.cart_ids())),
            check("answers with respond_to_offer", lambda c: bool(c.called(1, "respond_to_offer"))),
        ],
    ),
    Scenario(
        "group_dinner", ["احنا ستة أشخاص ونبي عشا", "لحم", "مو مهم الميزانية"],
        "Passes serves=6 and never asks how many people. Turn 1 asks his taste, turn 2 asks his budget; «مو مهم» means no limit, so on turn 3 she recommends a meat dish or family offer that feeds six and says it feeds them all.",
        [
            check("passes the headcount", lambda c: c.shop.state.party_size == 6),
            asks_about(0, "taste"),
            asks_about(1, "budget"),
            opened_a_dish(2),
        ],
    ),
    Scenario(
        "budget_lunch", ["أبي غدا وميزانيتي ثلاثين ريال بس", "دجاج", "لي بس"],
        "Passes max_price=30 (remembered as his budget) and never asks the budget again. Turn 1 asks his taste, turn 2 how many people; after «لي بس» she opens a chicken dish at or under 30 riyals and upsells nothing above the budget.",
        [
            check("the budget is remembered", lambda c: c.shop.state.budget == 30),
            asks_about(1, "people"),
            check("the opened dish fits the budget", lambda c: bool(c.opened()) and price_of(c.opened()[-1]) <= 30),
        ],
    ),
    Scenario(
        "price_objection", ["أبي مشكل مشاوي من بيت الشواية", "والله غالي شوي"],
        "Does not argue. Searches for a cheaper filling option (max_price below 61 or sort price_asc) and opens it, saying what he saves.",
        [
            check("searches again for something cheaper", lambda c: any(
                (0 < float(a.get("max_price") or 0) < 61) or a.get("sort") == "price_asc" or a.get("only_offers") or a.get("cashback")
                for a in c.called(1, "search_meals"))),
            check("opens a cheaper dish", lambda c: bool(c.opened_in(1)) and price_of(c.opened_in(1)[-1]) < 61),
            cart_empty(),
        ],
    ),
    Scenario(
        "in_a_hurry", ["أبي برجر وأنا مستعجل مرة"],
        "Searches with sort=fastest_delivery, opens a burger and mentions how fast it arrives (delivery minutes from the tool).",
        [
            check("sorts by fastest delivery", lambda c: any(a.get("sort") == "fastest_delivery" for a in c.called(0, "search_meals"))),
            opened_a_dish(0),
            says(0, r"دقيق", "mentions the delivery time"),
        ],
    ),
    Scenario(
        "delivery_question", ["أبي شاورما دجاج من مذاق الشام", "متى يوصل؟"],
        "Answers with the delivery minutes from the tool in one sentence, then brings him back to the open offer with one short question. Adds nothing to the cart.",
        [says(1, r"دقيق", "gives the delivery time"), cart_empty(), says(1, r"؟", "returns to the offer with a question")],
    ),
    Scenario(
        "think_about_it", ["أبي كبسة دجاج", "خلني أفكر شوي"],
        "No pressure: one reassuring sentence, may offer to save the dish to favourites. No new dish pushed, nothing added.",
        [no_new_pitch(1), cart_empty()],
    ),
    Scenario(
        "complaint", ["طلبي اللي قبل وصل بارد، والله زعلان"],
        "Stops selling: a sincere apology and asks how she can help. No dish, no price, no offer in this reply.",
        [no_new_pitch(0), check("no price is spoken", lambda c: "ريال" not in strip_marks(c.reply(0)))],
    ),
    Scenario(
        "just_browsing", ["بس أتفرج، ما أبي أطلب الحين"],
        "Leaves him be: short, warm, tells him she is here if he needs her. No pitch, no price.",
        [no_new_pitch(0), cart_empty()],
    ),
    Scenario(
        "pair_after_add", ["أبي سلطة سيزر", "ايه أبيها بدون إضافات"],
        "The salad goes in; she confirms briefly and offers the returned complement (pair) by name and price with a reason tied to the salad, one question.",
        [
            check("the salad is in the cart", lambda c: any("سيزر" in CAT.by_id(m).name for m in c.cart_ids())),
            says(1, r"ريال", "names the complement's price"),
        ],
    ),
    Scenario(
        "pair_declined_close", ["أبي سلطة سيزر", "ايه أبيها بدون إضافات", "لا ما أبي شي ثاني"],
        "Records the no (respond_to_offer accepted=false on the pair), then a short confident closing question. No new dish.",
        [
            check("the no is recorded", lambda c: any(a.get("accepted") is False for a in c.called(2, "respond_to_offer"))),
            no_new_pitch(2),
        ],
    ),
    Scenario(
        "healthy_work_lunch", ["أبي شي صحي للدوام", "لي بس", "حدود أربعين"],
        "Passes healthy=true. His taste (healthy) is known, so she never asks it; she may ask how many people and his budget (one per turn) before recommending, or recommend at once. By the last turn she has opened a light dish within 40 riyals and sells it as light (calories as a benefit: will not weigh him down at work).",
        [
            check("passes healthy=true", lambda c: any(a.get("healthy") for a in c.called(0, "search_meals"))),
            check("never asks his taste", lambda c: not any(re.search(_TOPIC["taste"][0], strip_marks(c.reply(t))) for t in range(3))),
            check("opens a light dish", lambda c: bool(c.opened()) and ("light" in CAT.by_id(c.opened()[-1]).diet_tags or catalog.meal_is_healthy(CAT.by_id(c.opened()[-1])))),
        ],
    ),
    Scenario(
        "dessert_yes_yes", ["أبي كنافة من حلويات الرياض", "طيب ماشي يا ريت", "ماشي"],
        "Opens the kunafa. His «ماشي يا ريت» is a yes: respond_to_offer on the pitch right away (an extras offer may come back). His next «ماشي» answers that offer. The kunafa must end up in the cart, and she never says it was added before a tool added it.",
        [check("the kunafa is in the cart", lambda c: "halawiyat-riyadh-kunafa" in c.cart_ids())],
    ),
    Scenario(
        "is_it_good", ["أبي شاورما دجاج من مذاق الشام", "هي زينة؟"],
        "Reassures with a fact from the tool (one of the most ordered, rating) and asks again. Adds nothing.",
        [cart_empty(), says(1, r"طلب|تقييم|ينطلب|يطلبونه", "uses social proof from the tool")],
    ),
    Scenario(
        "done_now", ["أبي سلطة سيزر", "ايه بدون إضافات", "بس كذا، اعتمد"],
        "Respects the finishing word: calls checkout (or accepts the checkout offer). Offers nothing new.",
        [check("checkout starts", lambda c: bool(c.called(2, "checkout")) or any(a.get("offer_id") == "checkout" and a.get("accepted") for a in c.called(2, "respond_to_offer")))],
    ),
    Scenario(
        "family_gathering", ["عندنا عزيمة بكرة لعشرة أشخاص، وش عندك؟", "مشاوي لحم", "حدود ثلاث مية"],
        "Passes serves=10 and never asks how many. Turn 1 asks his taste, turn 2 his budget; after «حدود ثلاث مية» she opens the biggest grill option within 300 and is honest about how many it feeds (suggest a quantity if one tray is not enough).",
        [
            check("passes the headcount", lambda c: c.shop.state.party_size == 10),
            asks_about(1, "budget"),
            check("a dish is opened by the third turn", lambda c: bool(c.opened())),
        ],
    ),
    Scenario(
        "calories_side_question", ["أبي كبسة دجاج", "كم سعراتها؟"],
        "Answers the calories honestly from the tool, then returns to the open offer with one question. Adds nothing.",
        [says(1, r"سعر", "gives the calories"), cart_empty()],
    ),
    Scenario(
        "two_noes_stop", ["أبي سلطة سيزر", "ايه بدون إضافات", "لا ما أبي", "لا بس كذا"],
        "After the complement is declined she does not push another upsell; on the last line she moves to checkout.",
        [check("no new dish after the declines", lambda c: not c.opened_in(2) and not c.opened_in(3))],
    ),
]

TRAIN_IDS = [
    "hungry_open", "discover_then_recommend", "recommend_with_taste", "meal_deal_pitch", "meal_deal_accept", "group_dinner", "price_objection", "in_a_hurry",
    "think_about_it", "complaint", "pair_after_add", "healthy_work_lunch", "is_it_good", "two_noes_stop",
]
BY_ID = {s.id: s for s in SCENARIOS}


# ─── Running a conversation ──────────────────────────────────────────────────


def student_backend(name: str):
    if name == "gemini":
        env = dict(os.environ, LLM_PROVIDER="gemini")
        return resolve_backend(env)
    return resolve_backend()


async def converse(system_prompt: str, lines: list[str], backend) -> Call:
    shop = make_shop()
    # A live call opens with the greeting (session.say), so it is her first
    # turn in the history the model sees; its tashkeel sets her style.
    history = llm.ChatContext()
    history.add_message(role="assistant", content=flow_say.GREETING_SAY_AR)
    agent = Assistant(
        instructions=agent_prompt.compose_instructions(catalog_index=catalog.catalog_index(), base_prompt=system_prompt),
        shop=shop,
        chat_ctx=history,
    )
    call = Call(lines=lines, shop=shop)
    async with lk_openai.LLM(**backend.openai_kwargs()) as model, AgentSession(llm=model) as session:
        # A gateway failure makes her speak a fallback instead of raising;
        # scoring that would blame the playbook for a 503. Fail and retry.
        errors: list[str] = []
        _CALL_ERRORS.set(errors)
        session.on("error", lambda ev: None if getattr(getattr(ev, "error", None), "recoverable", True)
                   else errors.append(str(ev.error)[:200]))
        await session.start(agent)
        for line in lines:
            before = len([1 for m, _ in shop.ui.calls if m == "luqma.showMeal"])
            ui_before = len(shop.ui.calls)
            result = await session.run(user_input=line)
            calls, outputs = [], []
            for ev in result.events:
                if ev.type == "function_call":
                    try:
                        args = json.loads(ev.item.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": ev.item.arguments}
                    calls.append((ev.item.name, args))
                elif ev.type == "function_call_output":
                    outputs.append({"tool": ev.item.name, "output": str(ev.item.output)[:4000]})
            reply = " ".join(
                ev.item.text_content for ev in result.events if ev.type == "message" and ev.item.role == "assistant"
            ).strip()
            opened = [p["mealId"] for m, p in shop.ui.calls if m == "luqma.showMeal"][before:]
            added = any(m == "luqma.addToCart" for m, _ in shop.ui.calls[ui_before:])
            call.turns.append({"caller": line, "calls": calls, "outputs": outputs, "reply": reply, "opened": opened, "added": added})
            if errors:
                raise RuntimeError(f"LLM error during the call: {errors[0]}")
    return call


async def converse_retrying(system_prompt: str, lines: list[str], backend, attempts: int = 3) -> Call:
    delay = 5.0
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(converse(system_prompt, lines, backend), timeout=240)
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                await asyncio.sleep(delay + random.random() * 3)
                delay *= 2
    call = Call(lines=lines, shop=make_shop())
    call.error = last
    return call


# ─── Scoring ─────────────────────────────────────────────────────────────────

_LATIN = re.compile(r"[A-Za-z]+")


def _stray_latin(reply: str) -> bool:
    """Latin that is not one of the loanwords the TTS is meant to get in Latin (سموذي → smoothie)."""
    from src.spoken_lexicon import LATIN_PHRASES, LATIN_WORDS

    allowed = {w.lower() for v in (*LATIN_WORDS.values(), *LATIN_PHRASES.values()) for w in v.split()}
    return any(w.lower() not in allowed for w in _LATIN.findall(reply))
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def voice_checks(call: Call) -> list[tuple[bool, str]]:
    results = []
    for i, turn in enumerate(call.turns):
        reply = turn["reply"]
        words = len(strip_marks(reply).split())
        results.append((bool(reply), f"turn {i + 1}: she says something"))
        results.append((reply.count("؟") + reply.count("?") <= 1, f"turn {i + 1}: at most one question (had {reply.count('؟') + reply.count('?')})"))
        results.append((words <= 32, f"turn {i + 1}: short enough for voice ({words} words, limit 32)"))
        from src.speech_claims import claims_added_to_cart

        results.append((turn.get("added", False) or not claims_added_to_cart(reply),
                        f"turn {i + 1}: never says it was added when nothing went in the cart"))
        results.append((not _stray_latin(reply) and not _EMOJI.search(reply) and "*" not in reply,
                        f"turn {i + 1}: no English words, emoji or markdown"))
    return results


JUDGE_PROMPT = """You are grading a Saudi voice sales agent, سلمى, for a food-delivery app, from a transcript.
Tool calls and tool outputs are shown; every dish name and price she says must come from a tool output.

Grade how good a SALESPERSON she is in this call, 0-10:
- On a recommendation request («وش ترشحين», «جوعان») she first asks the tool's three questions, one per turn: his taste (two options plus «or something else»), how many people, his budget, skipping any he already answered. When he names a dish she asks nothing and presents it at once.
- Recommends a specific dish with one concrete reason that fits what THIS caller said (taste, people, budget, hurry, health, occasion), not a generic line.
- At most one step-up or complement per turn, framed as value, built only on tool facts; she respects a no, a budget, a complaint, "just browsing" and "let me think" without pressure.
- Handles objections and side questions briefly and brings him back to the open offer; closes confidently when the order is complete.
- Voice: one or two short sentences, exactly one question at the end, plain, professional everyday Saudi dialect that any Saudi finds normal (not MSA, not Egyptian or Levantine, and not heavy Najdi/Bedouin or odd poetic words such as «على خشمي», «يا بعدي», «طوالي», «يسوى»), no invented facts.

What a great salesperson does in THIS scenario: {expect}

Transcript:
{transcript}

Answer with JSON only: {{"score": <0-10 integer>, "feedback": "<2-4 sentences in English: what cost points and what exactly she should do instead>"}}"""


class Judge:
    def __init__(self, model: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(base_url=GEMINI_BASE_URL, api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

    async def grade(self, scenario: Scenario, call: Call) -> tuple[float, str]:
        prompt = JUDGE_PROMPT.format(expect=scenario.expect, transcript=render(call))
        for attempt in range(3):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    reasoning_effort="low",
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                return max(0.0, min(10.0, float(data.get("score", 0)))) / 10.0, str(data.get("feedback", ""))
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                await asyncio.sleep(3 * (attempt + 1))
        return 0.5, f"(judge unavailable: {err})"


def render(call: Call) -> str:
    out = []
    for i, turn in enumerate(call.turns):
        out.append(f"[{i + 1}] CALLER: {turn['caller']}")
        for name, args in turn["calls"]:
            out.append(f"    TOOL CALL {name} {json.dumps(args, ensure_ascii=False)}")
        for o in turn["outputs"]:
            out.append(f"    TOOL OUTPUT {o['tool']}: {o['output']}")
        out.append(f"    سلمى: {turn['reply']}")
    if call.error:
        out.append(f"(conversation failed: {call.error})")
    return "\n".join(out)


@dataclass
class Graded:
    scenario: str
    score: float
    hard: float
    voice: float
    judge: float
    failed: list[str]
    judge_feedback: str
    transcript: str


async def grade(scenario: Scenario, call: Call, judge: Judge) -> Graded:
    if call.error or not call.turns:
        return Graded(scenario.id, 0.0, 0, 0, 0, [f"conversation failed: {call.error}"], "", render(call))
    hard = [c(call) for c in scenario.checks]
    voice = voice_checks(call)
    judge_score, feedback = await judge.grade(scenario, call)
    hard_score = sum(ok for ok, _ in hard) / len(hard)
    voice_score = sum(ok for ok, _ in voice) / len(voice)
    score = 0.5 * hard_score + 0.2 * voice_score + 0.3 * judge_score
    failed = [d for ok, d in hard + voice if not ok]
    return Graded(scenario.id, round(score, 4), hard_score, voice_score, judge_score, failed, feedback, render(call))


async def evaluate_prompt(system_prompt: str, scenarios: list[Scenario], *, backend, judge: Judge, parallel: int) -> list[Graded]:
    gate = asyncio.Semaphore(parallel)

    async def one(s: Scenario) -> Graded:
        async with gate:
            call = await converse_retrying(system_prompt, s.lines, backend)
        return await grade(s, call, judge)

    return list(await asyncio.gather(*(one(s) for s in scenarios)))


# ─── GEPA adapter ────────────────────────────────────────────────────────────


class SalesAdapter:
    """GEPA ↔ live conversations: a candidate is a playbook text."""

    def __init__(self, *, backend, judge: Judge, parallel: int, log_path: Path) -> None:
        self.backend = backend
        self.judge = judge
        self.parallel = parallel
        self.log_path = log_path
        self.propose_new_texts = None

    def evaluate(self, batch: list[Scenario], candidate: dict[str, str], capture_traces: bool = False) -> EvaluationBatch:
        prompt = agent_prompt.build_system_prompt(candidate[COMPONENT])
        started = time.time()
        graded = asyncio.run(evaluate_prompt(prompt, batch, backend=self.backend, judge=self.judge, parallel=self.parallel))
        with self.log_path.open("a") as log:
            log.write(json.dumps({
                "t": round(time.time() - started, 1),
                "playbook_hash": hash(candidate[COMPONENT]) & 0xFFFFFFFF,
                "scores": {g.scenario: g.score for g in graded},
            }, ensure_ascii=False) + "\n")
        return EvaluationBatch(
            outputs=[g.transcript for g in graded],
            scores=[g.score for g in graded],
            trajectories=graded if capture_traces else None,
        )

    def make_reflective_dataset(self, candidate, eval_batch: EvaluationBatch, components_to_update):
        records = []
        for g in eval_batch.trajectories or []:
            scenario = BY_ID[g.scenario]
            feedback = [f"Score {g.score:.2f} (checks {g.hard:.2f}, voice {g.voice:.2f}, judge {g.judge:.2f})."]
            if g.failed:
                feedback.append("Failed: " + "; ".join(g.failed) + ".")
            if g.judge_feedback:
                feedback.append("Judge: " + g.judge_feedback)
            records.append({
                "Inputs": {"scenario": scenario.id, "caller_lines": " | ".join(scenario.lines), "expected": scenario.expect},
                "Generated Outputs": g.transcript,
                "Feedback": " ".join(feedback),
            })
        return {COMPONENT: records}


REFLECTION_TEMPLATE = """You are improving one section of the system prompt of «سلمى», a Saudi voice sales agent for a food-delivery app («لقمة»). The model running her is a mid-size LLM with tools; the rest of her prompt (tool rules, voice and dialect rules) is fixed and not shown. You may only rewrite the SALES PLAYBOOK section below.

Current SALES PLAYBOOK:
```
<curr_param>
```

Conversations run with it, with the tool calls, tool outputs, her replies, and graded feedback:
```
<side_info>
```

Write an improved SALES PLAYBOOK that fixes the failures above while keeping what works.
Hard constraints:
- Written in Arabic in the same register as the current one (plain professional Saudi, addressed to her as «أنتِ»), starting with the heading «# مهارات البيع».
- At most about 650 words: a shorter, sharper rule beats a longer one. This model stalls and skips tools when the prompt grows.
- Only name tool arguments and result fields that already appear in the current playbook (e.g. `max_price`, `serves`, `sort="fastest_delivery"`, `meal_deal`, `bigger_size`, `costs_more_say_ar`, `same_price`, `costs_less_say_ar`, `delivery_minutes_say_ar`, `selling_points`, `lighter_alternative`, `saved_meals`, `respond_to_offer`, `only_offers`, `cashback`, `narrowing_question`, `options_say_ar`, `pick_for_me`, `open_top`). Never invent tools or fields.
- Never tell her to say a dish, price or fact that does not come from a tool output. Example lines may only use the real dishes and prices already in the examples.
- Keep: on a recommendation request, exactly the tool's three questions (taste with two options or something else, people, budget), one per turn and none he already answered; a named dish gets no questions; one reason fitted to the caller, one step up per turn, respect no/budget/complaint/browsing, return to the open offer after side questions, confident close.
- Generalise: write rules for kinds of situations, not for the exact test sentences.

Return only the new SALES PLAYBOOK inside one ``` block."""


def reflection_lm(model: str) -> Callable[[Any], str]:
    lm = dspy.LM(
        f"openai/{model}",
        api_base=GEMINI_BASE_URL,
        api_key=os.environ["GEMINI_API_KEY"],
        reasoning_effort="high",
        max_tokens=16000,
        temperature=1.0,
        cache=False,
    )

    def call(prompt: Any) -> str:
        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        if isinstance(prompt, str):
            return lm(prompt)[0]
        return lm(messages=messages)[0]

    return call


# ─── Commands ────────────────────────────────────────────────────────────────


def summary(graded: list[Graded]) -> dict[str, Any]:
    n = len(graded) or 1
    return {
        "mean": round(sum(g.score for g in graded) / n, 4),
        "checks": round(sum(g.hard for g in graded) / n, 4),
        "voice": round(sum(g.voice for g in graded) / n, 4),
        "judge": round(sum(g.judge for g in graded) / n, 4),
        "per_scenario": {g.scenario: g.score for g in graded},
    }


def old_prompt_text(path: str = "") -> Optional[str]:
    """An earlier agent_prompt.py's SALMA_SYSTEM_PROMPT (a file, or git HEAD), for the baseline."""
    import subprocess

    if path:
        source = Path(path).read_text()
    else:
        try:
            source = subprocess.run(["git", "show", "HEAD:src/prompts/agent_prompt.py"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout
        except subprocess.CalledProcessError:
            return None
    namespace: dict[str, Any] = {}
    exec(compile(source, "old_agent_prompt", "exec"), namespace)  # noqa: S102 — our own file at HEAD
    return namespace.get("SALMA_SYSTEM_PROMPT")


def cmd_baseline(args) -> None:
    backend, judge = student_backend(args.student), Judge(args.judge_model)
    prompts = {"current": agent_prompt.SALMA_SYSTEM_PROMPT}
    if args.playbook:
        prompts["candidate"] = agent_prompt.build_system_prompt(Path(args.playbook).read_text())
    if not args.skip_old and (old := old_prompt_text(args.old_prompt)):
        prompts["old"] = old
    scenarios = [BY_ID[i] for i in args.only] if args.only else SCENARIOS
    report: dict[str, Any] = {}
    for name, prompt in prompts.items():
        runs = [asyncio.run(evaluate_prompt(prompt, scenarios, backend=backend, judge=judge, parallel=args.parallel))
                for _ in range(args.repeats)]
        flat = [g for run in runs for g in run]
        report[name] = summary(flat)
        report[name]["failures"] = {g.scenario: g.failed for g in flat if g.failed}
        with (OUT_DIR / f"baseline-{name}.jsonl").open("w") as f:
            for g in flat:
                f.write(json.dumps(g.__dict__, ensure_ascii=False) + "\n")
        print(f"{name}: {json.dumps({k: v for k, v in report[name].items() if k != 'failures'}, ensure_ascii=False)}", flush=True)
    (OUT_DIR / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_optimize(args) -> None:
    backend, judge = student_backend(args.student), Judge(args.judge_model)
    train = [BY_ID[i] for i in TRAIN_IDS]
    val = [s for s in SCENARIOS if s.id not in TRAIN_IDS]
    run_dir = OUT_DIR / time.strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    adapter = SalesAdapter(backend=backend, judge=judge, parallel=args.parallel, log_path=run_dir / "evals.jsonl")
    seed = {COMPONENT: agent_prompt.SALES_PLAYBOOK.strip()}
    result = gepa.optimize(
        seed_candidate=seed,
        trainset=train,
        valset=val,
        adapter=adapter,
        reflection_lm=reflection_lm(args.reflection_model),
        reflection_prompt_template=REFLECTION_TEMPLATE,
        reflection_minibatch_size=args.minibatch,
        max_metric_calls=args.budget,
        run_dir=str(run_dir / "gepa"),
        raise_on_exception=False,
        skip_perfect_score=False,
        seed=args.seed,
        display_progress_bar=False,
    )
    best = result.best_candidate[COMPONENT]
    (run_dir / "best_playbook.md").write_text(best + "\n")
    scores = list(result.val_aggregate_scores)
    report = {
        "student": backend.model,
        "seed_val_score": scores[0] if scores else None,
        "best_val_score": scores[result.best_idx] if scores else None,
        "candidates": len(result.candidates),
        "val_scores": scores,
    }
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for i, cand in enumerate(result.candidates):
        (run_dir / f"candidate-{i}.md").write_text(cand[COMPONENT] + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"best playbook: {run_dir / 'best_playbook.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--student", choices=["coda", "gemini"], default="coda",
                        help="the model سلمى runs on while being scored (production: coda)")
    parser.add_argument("--judge-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--reflection-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--parallel", type=int, default=6)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="score the old prompt, the current one, and optionally a candidate playbook")
    b.add_argument("--repeats", type=int, default=2)
    b.add_argument("--playbook", help="a playbook file to score as 'candidate'")
    b.add_argument("--skip-old", action="store_true")
    b.add_argument("--old-prompt", default="", help="an earlier agent_prompt.py to score as 'old' (default: git HEAD)")
    b.add_argument("--only", nargs="*", help="scenario ids")
    o = sub.add_parser("optimize", help="run GEPA on the sales playbook")
    o.add_argument("--budget", type=int, default=240, help="max scenario rollouts")
    o.add_argument("--minibatch", type=int, default=4)
    o.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.parallel > 8:
        parser.error("--parallel above 8 overloads the shared Coda gateway")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {"baseline": cmd_baseline, "optimize": cmd_optimize}[args.cmd](args)


if __name__ == "__main__":
    main()
