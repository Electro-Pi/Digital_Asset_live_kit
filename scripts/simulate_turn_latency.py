"""Replay a turn's latency budget without a call, a browser or an LLM.

Everything between the transcript and her first word runs for real: the tool
guard, the catalog searches, the turn ledger, ``llm_node`` itself, and the
framework's tool loop, which this script stands in for. Only the two network
services are simulated, each by the delay it actually showed in the logs:

* the LLM, by a scripted stream that sleeps ``--llm-delay`` per round trip,
* the browser RPC, by ``--rpc-delay`` inside the fake UI bridge,

plus the three numbers LiveKit reports rather than the agent timing them:
``--eou``, ``--stt`` and ``--tts-ttfb``.

So the LLM and TTS figures are whatever you tell it, while the *shape* of the
turn — how many round trips the tool loop costs, and how long the catalog and
the guards take — is measured for real. That shape is the part the old logging
never showed.

    .venv/bin/python scripts/simulate_turn_latency.py                # every case
    .venv/bin/python scripts/simulate_turn_latency.py --case salad   # the live turn
    .venv/bin/python scripts/simulate_turn_latency.py --llm-delay 0.4

Defaults come from the 2026-09-21 09:26:47 «سلطة» turn: eou 1.66s, ASR 1.20s,
~1.0s per LLM round trip, 0.25s per browser RPC, TTS first byte 0.56s.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from livekit.agents.llm import (  # noqa: E402
    ChatChunk,
    ChatContext,
    ChoiceDelta,
    FunctionCall,
    FunctionCallOutput,
)
from livekit.agents.llm.llm import FunctionToolCall  # noqa: E402
from livekit.agents.voice.agent import ModelSettings  # noqa: E402
from loguru import logger  # noqa: E402

from agent import Assistant  # noqa: E402
from src.ui_bridge import UIResult  # noqa: E402
from test_luqma_flow import FakeUI  # noqa: E402


# ─── The two simulated services ──────────────────────────────────────────────


class SlowUI(FakeUI):
    """The fake browser, plus the round-trip delay a real tab costs."""

    def __init__(self, rpc_delay: float) -> None:
        super().__init__()
        self._rpc_delay = rpc_delay

    async def _invoke(self, method: str, payload: dict) -> UIResult:
        await asyncio.sleep(self._rpc_delay)
        return await super()._invoke(method, payload)


class ScriptedLLM:
    """One LLM round trip: sleep like the real one, then return the script."""

    def __init__(self, chunks: list[ChatChunk], delay: float) -> None:
        self._chunks = chunks
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __aiter__(self):
        return self._run()

    async def _run(self):
        # The model is thinking. This is the cost the tool loop pays per step.
        await asyncio.sleep(self._delay)
        for chunk in self._chunks:
            yield chunk


def _tool_step(*calls: tuple[str, dict]) -> list[ChatChunk]:
    return [
        ChatChunk(
            id="sim",
            delta=ChoiceDelta(
                role="assistant",
                tool_calls=[
                    FunctionToolCall(
                        name=name,
                        arguments=json.dumps(args, ensure_ascii=False),
                        call_id=f"sim_{name}_{i}",
                    )
                    for i, (name, args) in enumerate(calls)
                ],
            ),
        )
    ]


def _speech_step(text: str) -> list[ChatChunk]:
    return [ChatChunk(id="sim", delta=ChoiceDelta(role="assistant", content=text))]


# ─── The framework's tool loop, stood in for ─────────────────────────────────


def say(line: str = "") -> None:
    """Everything on stderr, where the log sink is, so the order holds."""
    print(line, file=sys.stderr, flush=True)


async def run_turn(*, case: str, script: list[list[ChatChunk]], heard: str, args) -> dict:
    ui = SlowUI(args.rpc_delay)
    agent = Assistant(instructions="simulation", ui=ui)

    ctx = ChatContext()
    ctx.add_message(role="user", content=heard)
    run_ctx = SimpleNamespace(chat_ctx=ctx, session=None)

    # What the session handlers do on a real call.
    agent.latency.begin(chars=len(heard))
    agent.latency.note_eou(delay=args.eou, transcription=args.eou - 0.01)
    agent.latency.note_stt(args.stt)

    spoken = ""
    for chunks in script:
        stream = ScriptedLLM(chunks, args.llm_delay)
        with patch.object(Assistant, "_get_activity_or_raise", return_value=SimpleNamespace(
            llm=SimpleNamespace(chat=lambda **_: stream),
            session=SimpleNamespace(conn_options=SimpleNamespace(llm_conn_options=None)),
        )):
            out = [c async for c in agent.llm_node(ctx, agent.tools, ModelSettings())]

        calls = [c for chunk in out if chunk.delta for c in (chunk.delta.tool_calls or [])]
        spoken += "".join(c.delta.content or "" for c in out if c.delta)
        if not calls:
            break

        # The framework runs the approved tools, then feeds their output back.
        for call in calls:
            fn = getattr(agent, call.name)
            result = await fn(run_ctx, **json.loads(call.arguments))
            ctx.items.append(
                FunctionCall(call_id=call.call_id, name=call.name, arguments=call.arguments)
            )
            ctx.items.append(
                FunctionCallOutput(
                    call_id=call.call_id,
                    name=call.name,
                    output=json.dumps(result, ensure_ascii=False, default=str),
                    is_error=False,
                )
            )

    # TTS is the last thing before she is audible.
    await asyncio.sleep(args.tts_ttfb)
    agent.latency.note_tts(ttfb=args.tts_ttfb, chars=len(spoken))

    pages = [p.get("mealId") for m, p in ui.calls if m == "luqma.showMeal"]
    cart = [p.get("mealId") for m, p in ui.calls if m == "luqma.addToCart"]

    say()
    say(f"── {case} ──")
    say(f"   heard  : {heard}")
    say(f"   said   : {spoken[:110] or '(nothing — the turn would be silent)'}")
    say(f"   pages  : {pages or '(none)'}")
    say(f"   cart   : {cart or '(none)'}")
    say(f"   effects: {[(e['kind'], e['target']) for e in agent.turn.snapshot()['effects']]}")
    say()
    budget = agent.latency.summary()
    agent.latency.report()
    return {"case": case, **budget}


# ─── Cases ───────────────────────────────────────────────────────────────────


def _cases() -> dict[str, tuple[str, list[list[ChatChunk]]]]:
    return {
        # One round trip, no tools: the regime the old ~3.5s measurement assumed.
        "chat": (
            "ما أخبارك يا سلمى؟",
            [_speech_step("بِخَيْر وَعَافْيَهْ يَا رَبّ، وِش خَاطْرِك تَتَغَدَّى؟")],
        ),
        # The live turn: three searches, a show_meal, then speech. Four round
        # trips before she says a word.
        "salad": (
            "حسناً ممكن تجيبي لي سلطة؟",
            [
                _tool_step(("search_meals", {"query": "سلطة"})),
                _tool_step(("search_meals", {"query": "سلطة خضراء دجاج"})),
                _tool_step(("search_meals", {"query": "سلطة يونانية سيزر"})),
                _tool_step(("show_meal", {"meal_id": "formaggio-7", "reason": "سلطة سيزر"})),
                _speech_step("شُوف هَذِي، سَلَطَة سِيزَر بِـثَمانْيَهْ وَعِشْرِين رِيال."),
            ],
        ),
        # The turn that used to end in dead air: the model asks for
        # resolve_extras, which adds the meal by calling add_to_cart itself.
        # The budget also shows a cart turn is two round trips, not one.
        "add-to-cart": (
            "طيب يا ريت تظيفيها لي في السلة",
            [
                _tool_step(("search_meals", {"query": "سلطة سيزر"})),
                _tool_step(("resolve_extras", {"chosen": [], "spicy": False})),
                _speech_step("تَمّ، ضِفْتْهَا لَك فِي السَّلَّهْ. تِبِي شَي ثَانِي؟"),
            ],
        ),
        # What the same request costs when the model searches once.
        "salad-one-search": (
            "حسناً ممكن تجيبي لي سلطة؟",
            [
                _tool_step(("search_meals", {"query": "سلطة سيزر"})),
                _speech_step("شُوف هَذِي، سَلَطَة سِيزَر بِـثَمانْيَهْ وَعِشْرِين رِيال."),
            ],
        ),
    }


def main() -> int:
    cases = _cases()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--case", choices=[*cases, "all"], default="all")
    p.add_argument("--llm-delay", type=float, default=1.0, help="seconds per LLM round trip")
    p.add_argument("--rpc-delay", type=float, default=0.25, help="seconds per browser RPC")
    p.add_argument("--eou", type=float, default=1.66, help="VAD silence wait + transcription")
    p.add_argument("--stt", type=float, default=1.20, help="ASR inference inside that wait")
    p.add_argument("--tts-ttfb", type=float, default=0.56, help="TTS first byte")
    args = p.parse_args()

    # Only the latency lines and warnings; the agent's own INFO chatter would
    # bury them exactly as it did in the journal.
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{message}",
        filter=lambda r: r["message"].startswith("Turn latency") or r["level"].no >= 30,
    )

    say()
    say(f"Simulating with llm_delay={args.llm_delay}s rpc_delay={args.rpc_delay}s "
        f"eou={args.eou}s stt={args.stt}s tts_ttfb={args.tts_ttfb}s")
    say("LLM and TTS are the numbers you passed. The tool-loop shape, the catalog")
    say("searches, the guards and the ledger are real.")

    wanted = [args.case] if args.case != "all" else list(cases)
    started = time.monotonic()
    rows = []
    for name in wanted:
        heard, script = cases[name]
        rows.append(asyncio.run(run_turn(case=name, script=script, heard=heard, args=args)))

    if len(rows) > 1:
        say()
        say("── where the wait goes ──")
        say(f"{'case':<20}{'total':>8}{'eou+asr':>9}{'llm':>7}{'tools':>7}{'tts':>6}"
            f"{'other':>7}{'trips':>7}{'wasted':>8}")
        for r in rows:
            say(f"{r['case']:<20}{r['total']:>7.2f}s{r['eou'] or 0:>9.2f}{r['llm']:>7.2f}"
                f"{r['tools']:>7.2f}{r['tts_ttfb'] or 0:>6.2f}{r['other']:>7.2f}"
                f"{r['steps']:>7}{r['tool_steps']:>8}")
        best, worst = min(rows, key=lambda r: r["total"]), max(rows, key=lambda r: r["total"])
        if best is not worst:
            say()
            say(f"{worst['case']} costs {worst['total'] - best['total']:.2f}s more than "
                f"{best['case']}, and the difference is "
                f"{worst['steps'] - best['steps']} extra LLM round trip(s).")

    say()
    say(f"{len(wanted)} turn(s) simulated in {time.monotonic() - started:.1f}s of wall time.")
    say()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
