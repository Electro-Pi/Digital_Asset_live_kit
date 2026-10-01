"""The shared machinery under every agent and task: the LLM node and guardrails.

``HarnessNodes`` is mixed into both the shopping agent and the checkout task so
they behave the same way around the model:

* ``on_user_turn_completed`` runs **once per committed caller turn**. The few
  input guardrails (ASR noise, attempts to abuse the agent) live here and stop
  the turn with ``StopResponse``. Nothing here changes order state.
* ``llm_node`` runs the model and has **no side effects on the order**, because
  LiveKit may run it speculatively (preemptive generation) and throw the result
  away. It streams the reply as it is generated, holding back only a sentence
  that claims an action no tool has performed; parses tool calls the model
  leaked into text; validates every call against its schema; and speaks a
  fallback when the model returns nothing, so the call never goes silent.

Tools get their state through ``self.shop`` (``Shop``), which the agent and its
tasks share for the length of the call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncIterable

from livekit.agents import Agent, StopResponse
from livekit.agents.llm import ChatChunk, ChoiceDelta
from livekit.agents.llm.llm import FunctionToolCall
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice.agent import ModelSettings
from loguru import logger

from src import flow_say
from src.chat_view import (
    has_tool_activity_since_last_user,
    known_amounts,
    prepare_chat_ctx_for_llm,
    tool_calls_since_last_user,
    tool_outputs_since_last_user,
)
from src.config import CONFIG
from src.guardrails import abuse_kind, is_asr_noise
from src.llm_backend import ensure_non_system_turn, resolve_backend
from src.session_state import SessionState
from src.speech_claims import (
    claims_action,
    claims_added_to_cart,
    claims_found,
    claims_order_placed,
    claims_page_opened,
    claims_stall,
    has_reasoning_leak,
    spoken_amounts,
    strip_reasoning_leak,
)
from src.speech_streaming import GuardedReplyStream
from src.spoken_lexicon import strip_speech_artifacts
from src.tool_call_recovery import collect_requested_calls, contains_text_tool_call, extract_text_tool_calls
from src.tool_guard import build_tool_specs, plan_turn
from src.turn_latency import TurnLatency
from src.turn_ledger import CART_ADD, MEAL_PAGE, NAVIGATE, ORDER_COMPLETE, TurnLedger, user_turn_key
from src.ui_bridge import UIBridge, UIResult


@dataclass
class Shop:
    """Everything one call's agents and tasks share."""

    ui: UIBridge
    state: SessionState = field(default_factory=SessionState)
    ledger: TurnLedger = field(default_factory=TurnLedger)
    latency: TurnLatency = field(default_factory=TurnLatency)
    hour: int = 12
    known_customer: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Every browser mutation reports itself to the ledger, however deep in
        # a tool it happened.
        self.ui.on_effect = self.ledger.record


@lru_cache(maxsize=1)
def _backend():
    return resolve_backend()


@lru_cache(maxsize=1)
def _known_words() -> frozenset[str]:
    from src.catalog import get_all_projects
    words = set()
    for p in get_all_projects():
        words.update(p.get("name", "").split())
        words.update(p.get("city", "").split())
    return frozenset(words)


# Sentence boundaries for dropping one false claim without losing the rest.
_SENTENCE_END = ("؟", "?", ".", "!", "،")


def split_sentences(text: str) -> list[str]:
    out, current = [], ""
    for ch in text:
        current += ch
        if ch in _SENTENCE_END:
            out.append(current.strip())
            current = ""
    if current.strip():
        out.append(current.strip())
    return [s for s in out if s]


@dataclass(frozen=True)
class CatalogFacts:
    amounts: frozenset[int] = frozenset()
    looked_up: bool = False

    @classmethod
    def of(cls, chat_ctx: Any) -> "CatalogFacts":
        return cls(frozenset(known_amounts(chat_ctx)), has_tool_activity_since_last_user(chat_ctx))

    def backs(self, sentence: str) -> bool:
        return True


def drop_false_claims(
    speech: str, ledger: TurnLedger, *, tools_ran: bool, facts: CatalogFacts | None = None
) -> tuple[str, list[str]]:
    """Remove sentences that claim something this turn did not do."""
    kept, dropped = [], []
    for sentence in split_sentences(speech):
        false = (
            (claims_page_opened(sentence) and not (ledger.did(MEAL_PAGE) or ledger.did(NAVIGATE)))
            # A promise to go and look, with no tool running, is dead air.
            or (claims_stall(sentence) and not tools_ran)
        )
        (dropped if false else kept).append(sentence)
    return " ".join(kept).strip(), dropped

# Live 2026-09-27: «طيب ماشي يا ريت» → respond_to_offer without `accepted`;
# the call was refused in silence and she said «ما فهمت عليك».
REFUSED_CALL_NOTE = (
    "نداء الأداة ما انقبل: {reasons}. استدعيها مرة ثانية الحين بكل المعطيات المطلوبة "
    "(مثل `accepted=true` لو وافق أو `accepted=false` لو رفض)."
)


@dataclass
class _PassOutcome:
    """What one run of the model left for ``llm_node`` to act on."""

    may_retry: bool = True
    unbacked: list[str] = field(default_factory=list)
    # «نزلتها لك» with no tool this turn: the add he agreed to never happened.
    unperformed_add: list[str] = field(default_factory=list)
    # Every call it asked for was refused by the schema check, and it said nothing.
    refused: list[str] = field(default_factory=list)


def says_enough_on_its_own(speech: str) -> bool:
    """Whether the reply says something worth keeping on its own (not just filler)."""
    return len(strip_speech_artifacts(speech or "").split()) >= 6


class HarnessNodes:
    """Mixed into an ``Agent`` or ``AgentTask``. Requires ``self.shop``."""

    shop: Shop

    # ─── Helpers for tools ───────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self.shop.state

    @property
    def ui(self) -> UIBridge:
        return self.shop.ui

    def screen(self, outcome: UIResult) -> UIResult:
        """The browser's answer, or a clearly marked mock when configured.

        Failures are reported as failures. Only UI_MOCK_ON_FAILURE=1 (demo
        setups with no frontend) turns one into success, and then the data
        says ``"mocked": true`` so nobody mistakes it for a real screen change.
        """
        if outcome.ok or not CONFIG.ui.mock_on_failure:
            return outcome
        promoted = self.shop.ledger.promote_last_failure()
        logger.warning("UI call failed reason={}; UI_MOCK_ON_FAILURE is on, treating as done effect={}",
                       outcome.reason, promoted.kind if promoted else None)
        return UIResult(ok=True, reason="mocked", data={**(outcome.data or {}), "mocked": True})

    @staticmethod
    def no_interruptions(context: Any) -> None:
        """A write that has started must finish, whatever the caller says."""
        disallow = getattr(context, "disallow_interruptions", None)
        if callable(disallow):
            try:
                disallow()
            except Exception as exc:  # noqa: BLE001 - a missing speech handle must not fail the write
                logger.debug("disallow_interruptions unavailable: {}", exc)

    def say_exact(self, context: Any, text: str) -> None:
        """Speak a line word for word (an order summary, a confirmation).

        Used only where the exact words matter. The line is added to the chat
        history, so the model knows it was said.
        """
        line = str(text or "").strip()
        if not line:
            return
        speak = getattr(self._speaker(context), "say", None)
        if speak is None:
            logger.info("No session to speak through; line not spoken: {!r}", line[:120])
            return
        try:
            speak(line)
        except Exception as exc:  # noqa: BLE001 - speech failure must not fail the tool
            logger.warning("session.say failed: {}", exc)

    def _speaker(self, context: Any = None) -> Any:
        """The session to speak through: the tool's, else this agent's."""
        session = getattr(context, "session", None)
        if session is not None:
            return session
        try:
            return self.session  # raises outside a running session
        except Exception:  # noqa: BLE001
            return getattr(self, "_session_for_say", None)

    def _chat_for_tools(self, context: Any = None) -> Any:
        """The context of the turn in progress, including this turn's earlier tool steps."""
        turn_ctx = getattr(self, "_turn_ctx", None)
        if turn_ctx is not None:
            return turn_ctx
        from_context = getattr(context, "chat_ctx", None)
        return from_context if from_context is not None else getattr(self, "chat_ctx", None)

    # ─── Guardrails: once per committed turn ─────────────────────────────

    async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
        text = str(getattr(new_message, "text_content", "") or "")
        if is_asr_noise(text, _known_words()):
            logger.info("Dropping ASR noise turn chars={}", len(text))
            raise StopResponse()
        kind = abuse_kind(text)
        if kind:
            line = self.state.pick_variant(f"abuse:{kind}", flow_say.ABUSE_LINES.get(kind, flow_say.ABUSE_LINES["jailbreak"]))
            logger.info("Guardrail {} answered with a fixed line", kind)
            self.say_exact(None, line)
            raise StopResponse()

    # ─── The LLM node: no order side effects ─────────────────────────────

    async def llm_node(self, chat_ctx: Any, tools: list[Any], model_settings: ModelSettings) -> AsyncIterable[ChatChunk]:
        outcome = _PassOutcome()
        async for chunk in self._llm_pass(chat_ctx, tools, model_settings, outcome):
            yield chunk
        if outcome.refused:
            # The model meant to act but got an argument wrong. Tell it what,
            # once, instead of answering the caller with «ما فهمت عليك».
            logger.warning("All tool calls refused; asking again with the reasons refused={}", outcome.refused)
            async for chunk in self._llm_pass(
                chat_ctx, tools, ModelSettings(tool_choice="required"), _PassOutcome(may_retry=False),
                note=REFUSED_CALL_NOTE.format(reasons="؛ ".join(outcome.refused)),
            ):
                yield chunk
            return
        if outcome.unperformed_add:
            # She told him it was added and called nothing. Ask again with a
            # tool required and the open offers in view, so the add happens.
            logger.warning("Reply claimed an add no tool made; asking again with a tool required dropped={}", outcome.unperformed_add)
            offers = json.dumps(self.state.open_facts(), ensure_ascii=False)
            async for chunk in self._llm_pass(
                chat_ctx, tools, ModelSettings(tool_choice="required"), _PassOutcome(may_retry=False),
                note=ADD_CLAIM_NOTE.format(offers=offers),
            ):
                yield chunk
            return
        if not outcome.unbacked:
            return
        # She named a dish or a price no tool gave her, and called no tool.
        # Ask once more with a tool required, so the next thing she says
        # comes from the catalog.
        logger.warning("Reply stated catalog facts no tool returned; asking again with a tool required dropped={}", outcome.unbacked)
        async for chunk in self._llm_pass(
            chat_ctx, tools, ModelSettings(tool_choice="required"), _PassOutcome(may_retry=False), note=CATALOG_FACT_NOTE
        ):
            yield chunk

    async def _llm_pass(
        self, chat_ctx: Any, tools: list[Any], model_settings: ModelSettings, outcome: "_PassOutcome", note: str = ""
    ) -> AsyncIterable[ChatChunk]:
        shop = self.shop
        # Per-turn bookkeeping only; both are idempotent for the same user message.
        shop.ledger.begin(user_turn_key(chat_ctx))
        self._turn_ctx = chat_ctx

        allowed = {getattr(getattr(t, "info", None), "name", None) or getattr(t, "id", None) for t in tools or []}
        allowed.discard(None)
        specs = build_tool_specs(tools)
        request_ctx = prepare_chat_ctx_for_llm(chat_ctx)
        if note and hasattr(request_ctx, "add_message"):
            request_ctx.add_message(role="system", content=note)
        if _backend().needs_non_system_turn:
            request_ctx, relabelled = ensure_non_system_turn(request_ctx)
            if relabelled:
                logger.info("Opening turn relabelled for {}", _backend().provider)

        tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
        choice = str(getattr(tool_choice, "value", tool_choice) or "")
        forced = tool_choice is not NOT_GIVEN and choice not in {"auto", "none", ""}
        facts = CatalogFacts.of(chat_ctx)
        # Stream every phrase that claims nothing unbacked; hold one that
        # claims an action or a catalog fact until the end of the reply (see
        # drop_false_claims below).
        stream = None if forced else GuardedReplyStream(
            lambda s: not claims_action(s) and facts.backs(s) and not has_reasoning_leak(s),
            CONFIG.endpoints.tts_sentence_min_len,
        )

        text_parts: list[str] = []
        pending: list[Any] = []
        chunk_id = "reply"
        started = time.perf_counter()
        shop.latency.llm_begin()
        try:
            async for chunk in Agent.default.llm_node(self, request_ctx, tools, model_settings):
                if isinstance(chunk, ChatChunk):
                    chunk_id = chunk.id or chunk_id
                    if chunk.delta and chunk.delta.tool_calls:
                        if stream:
                            stream.deferred = True
                        pending.append(chunk)
                        continue
                    if chunk.delta and chunk.delta.content:
                        text_parts.append(chunk.delta.content)
                        for phrase in (stream.push(chunk.delta.content) if stream else ()):
                            logger.info("LLM early speech elapsed={:.3f}s chars={}", time.perf_counter() - started, len(phrase))
                            yield ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=phrase))
                        continue
                    pending.append(chunk)
                elif isinstance(chunk, str):
                    text_parts.append(chunk)
                    for phrase in (stream.push(chunk) if stream else ()):
                        yield ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=phrase))
        except Exception as exc:  # noqa: BLE001 - the call must not go silent on an LLM error
            shop.latency.llm_end()
            logger.error("LLM request failed; speaking a recovery line error={}", exc)
            if stream and stream.spoken:
                return
            yield ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=self._unclear_line("llm_error")))
            return

        text = "".join(text_parts)
        speech, leaked = extract_text_tool_calls(text, allowed_names=allowed)
        cleaned = strip_speech_artifacts(speech)
        if cleaned != speech:
            logger.info("Speech cleanup before={!r} after={!r}", speech[:300], cleaned[:300])
        speech = cleaned

        requested, passthrough, call_extras = collect_requested_calls(pending, leaked)
        already = tool_calls_since_last_user(chat_ctx, allowed)
        approved, refused = plan_turn(requested, specs, already_ran=already, allowed=allowed)
        if choice == "none" and approved:
            logger.warning("tool_choice=none; dropping {}", [c.name for c in approved])
            approved = []
        if refused:
            logger.warning("Refused {} tool call(s): {}", len(refused), refused)
            if (not approved and outcome.may_retry and choice != "none"
                    and not (stream and stream.spoken) and not says_enough_on_its_own(speech)):
                outcome.refused = [str(r) for r in refused]
                return
        if leaked:
            logger.info("Parsed {} tool call(s) the model wrote as text", len(leaked))
        shop.latency.llm_end(tools=[c.name for c in approved], spoke=bool(speech.strip()))

        for chunk in passthrough:
            if isinstance(chunk, ChatChunk) and chunk.delta and chunk.delta.content:
                continue  # text already handled above
            yield chunk

        if approved:
            logger.info("Turn tools: {}", [(c.name, c.arguments) for c in approved])
            if stream and speech.strip() and not stream.spoken:
                logger.info("Tool step: unspoken text dropped chars={}", len(speech))
            yield ChatChunk(
                id=chunk_id,
                delta=ChoiceDelta(
                    role="assistant",
                    tool_calls=[
                        FunctionToolCall(
                            name=c.name,
                            arguments=json.dumps(c.arguments, ensure_ascii=False),
                            call_id=c.call_id,
                            extra=call_extras.get(c.call_id),
                        )
                        for c in approved
                    ],
                ),
            )
            return
        if contains_text_tool_call(text) and not speech.strip():
            logger.warning("Dropped an unparseable tool call written as text preview={!r}", text[:180])

        tools_ran = has_tool_activity_since_last_user(chat_ctx)
        if has_reasoning_leak(speech):
            logger.warning("Dropped reasoning the model wrote into its reply: {!r}", speech[:300])
            speech = strip_reasoning_leak(speech)
        if speech.strip():
            speech, dropped = drop_false_claims(speech, shop.ledger, tools_ran=tools_ran, facts=facts)
            if dropped:
                logger.warning("Dropped claims no tool performed: {} ledger={}", dropped, shop.ledger.snapshot())
            rest = stream.remaining(speech) if stream else speech
            if rest:
                yield ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=rest))
                return
        if stream and stream.spoken:
            return

        # The model returned nothing to say. Never leave the caller in silence.
        fallback = self._fallback_line(chat_ctx, tools_ran)
        if fallback:
            logger.info("Empty reply; speaking fallback chars={}", len(fallback))
            yield ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=fallback))

    def _unclear_line(self, kind: str) -> str:
        return self.state.pick_variant(f"unclear:{kind}", flow_say.UNCLEAR_LINES)

    def _fallback_line(self, chat_ctx: Any, tools_ran: bool) -> str:
        """A tool's own factual summary, or a request to repeat when no tool ran."""
        if tools_ran:
            for output in reversed(tool_outputs_since_last_user(chat_ctx)):
                line = str(output.get("summary_ar") or "").strip()
                if line:
                    return line
            return ""
        return self._unclear_line("empty")
