from dotenv import load_dotenv
from typing import Any, AsyncIterable, Optional, Sequence

import asyncio
import difflib
import json
import os
import time
from loguru import logger

import httpx

from livekit import agents, rtc
from livekit.agents import stt as livekit_stt
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    function_tool,
    room_io,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    MetricsCollectedEvent,
    SessionUsageUpdatedEvent,
    SpeechCreatedEvent,
    ToolError,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    metrics,
    tokenize,
)
from livekit.agents.llm import ChatChunk, ChoiceDelta
from livekit.agents.llm.llm import FunctionToolCall
from livekit.agents.types import NOT_GIVEN
from livekit.agents.tts import StreamAdapter
from livekit.agents.voice.agent import ModelSettings
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.plugins.qwen_asr_streaming_stt import QwenASRStreamingSTT
from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src import catalog
from src.prefs import build_prefs_context, summarize_prefs
from src.prompts.agent_prompt import compose_instructions, intro_for_voice
from src.spoken_lexicon import strip_speech_artifacts, written_phone, written_text
from src.text_tool_calls import (
    CUTOUT_FALLBACK_SAY_AR,
    STALL_FALLBACK_SAY_AR,
    build_offers_path,
    claims_stall,
    contains_text_tool_call,
    extract_text_tool_calls,
    last_user_text,
    prepare_chat_ctx_for_llm,
    recover_implied_add_to_cart,
    recover_implied_get_cart,
    recover_implied_get_saved,
    recover_implied_open_page,
    recover_implied_search_meals,
    recover_implied_set_ui_filter,
    recover_implied_show_meal,
    recover_stalled_turn,
    should_run_add_to_cart,
    should_speak_cutout_fallback,
    user_cancels_repeat_add,
)
from src.ui_bridge import UIBridge
from src.voice_profile import parse_voice_from_metadata, voice_profile


load_dotenv(".env.local")

# One cart line past this is almost always ASR/LLM noise (live: "560" of a
# latte → 49k SAR, then sar_words crashed and the agent went silent). Removals
# still use quantity=0 and are uncapped.
MAX_LINE_QUANTITY = 50

TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", "http://localhost:16326")
TTS_MODEL = os.getenv("TTS_MODEL", "VoxCPM2")
TTS_VOICE = os.getenv("TTS_VOICE", "nouf")
# The voice this deployment is supposed to be speaking with. The VoxCPM2 server
# serves several precomputed female voices ("nouf", "hoda") plus "default"
# (male), so this is config, not a constant — see GET /v1/audio/voices.
TTS_EXPECTED_VOICE = os.getenv("TTS_EXPECTED_VOICE", TTS_VOICE)
# Prefer the TTS server's precomputed ``nouf`` voice. A reference path is only
# valid when that exact file exists inside the TTS server's filesystem.
TTS_REF_AUDIO = os.getenv("TTS_REF_AUDIO", "").strip() or None
TTS_SEED = int(os.getenv("TTS_SEED", "1234"))
# Delivery pace, applied client-side and pitch-preserving. Below 1.0 is slower.
# Not the server's `speed`, which is a resampler and would change the speaker.
#
# 1.0 bypasses the WSOLA stretch entirely, and that is deliberate. The stretch
# inserts duration by repeating pitch periods, which duplicates speech
# transients: measured +7.3% spurious onsets at 0.92, scaling ~1:1 with the
# stretch amount (+2.4% at 0.97, +9.5% at 0.88). That is audible as a slight
# stutter on consonants. The 0.92 was inherited from the MOSS deployment, but
# VoxCPM2's precomputed female voices already speak ~20% slower than the human
# reference recording, so the extra slow-down bought nothing and cost the artifact.
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TTS_SENTENCE_MIN_LEN = int(os.getenv("TTS_SENTENCE_MIN_LEN", "300"))

# ─── LLM / STT backends ──────────────────────────────────────────────────────
# Both are OpenAI-compatible vLLM servers. When vLLM is started with --api-key
# every /v1/* route 401s without a matching bearer token, so the key is required
# for the hosted endpoints and ignorable for an unauthenticated local server.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://8.213.50.155:8001/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4")
# openai.LLM rejects an empty api_key, so fall back to a placeholder.
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip() or "no-key-needed"

STT_BASE_URL = os.getenv("STT_BASE_URL", "http://localhost:8000")
STT_MODEL = os.getenv("STT_MODEL", "CohereLabs/cohere-transcribe-arabic-07-2026")
STT_API_KEY = os.getenv("STT_API_KEY", "").strip() or None

# Use the speech classifier, rather than audio volume alone, to decide which
# microphone segments are sent to ASR.  The slightly conservative defaults keep
# steady room noise and short incidental sounds from opening the STT gate while
# still allowing short Arabic replies.
STT_VAD_ACTIVATION_THRESHOLD = float(
    os.getenv("STT_VAD_ACTIVATION_THRESHOLD", "0.65")
)
STT_VAD_DEACTIVATION_THRESHOLD = float(
    os.getenv("STT_VAD_DEACTIVATION_THRESHOLD", "0.45")
)
STT_VAD_MIN_SPEECH_DURATION = float(
    os.getenv("STT_VAD_MIN_SPEECH_DURATION", "0.20")
)
STT_VAD_MIN_SILENCE_DURATION = float(
    os.getenv("STT_VAD_MIN_SILENCE_DURATION", "0.60")
)
STT_VAD_PREFIX_PADDING_DURATION = float(
    os.getenv("STT_VAD_PREFIX_PADDING_DURATION", "0.30")
)
STT_FOREGROUND_MIN_RMS = float(os.getenv("STT_FOREGROUND_MIN_RMS", "0.015"))
STT_FOREGROUND_MIN_SNR_DB = float(
    os.getenv("STT_FOREGROUND_MIN_SNR_DB", "8.0")
)
STT_FOREGROUND_MIN_DURATION = float(
    os.getenv("STT_FOREGROUND_MIN_DURATION", "0.20")
)


# Load and index the catalog at import, not on the first tool call: a missing or
# malformed catalog.json then shows up in the boot log instead of as سلمى going
# quiet mid-sentence. It is deliberately not fatal — a voice agent that can still
# talk is better than one that will not start.
try:
    catalog.load_catalog()
except Exception as e:  # noqa: BLE001
    logger.error(
        "Catalog failed to load at startup; meal search will fail until fixed: {}", e
    )


def _preview(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _match_addon_loose(requested: str, addons: Sequence[dict]) -> Optional[str]:
    """Resolve a garbled addon reference to a real addon id of this meal.

    The model passes whatever it has — the id, the Arabic name, or the ASR's
    rendering of the name ("Sauce Tomb" for "صوص ثوم"). Matching is scoped to
    the one meal's handful of addons, so loose matching is safe: there is
    nothing else "sauce" could mean here.
    """
    want = catalog.normalize_ar(requested)
    if not want:
        return None
    want_tokens = set(want.split())

    best_id: Optional[str] = None
    best_score = 0.0
    for addon in addons:
        addon_id = str(addon.get("id") or "")
        name = catalog.normalize_ar(addon.get("name"))
        id_text = catalog.normalize_ar(addon_id.replace("-", " ").replace("_", " "))
        if want in (addon_id.lower(), name, id_text):
            return addon_id

        candidate_tokens = set(name.split()) | set(id_text.split())
        overlap = 0.0
        if want_tokens and candidate_tokens:
            # Relative to the smaller side, so "sauce tomb" still meets
            # "garlic sauce" on their one shared token.
            overlap = len(want_tokens & candidate_tokens) / min(
                len(want_tokens), len(candidate_tokens)
            )
        fuzz = max(
            difflib.SequenceMatcher(None, want, name).ratio() if name else 0.0,
            difflib.SequenceMatcher(None, want, id_text).ratio() if id_text else 0.0,
        )
        score = max(fuzz, overlap)
        if score > best_score:
            best_score = score
            best_id = addon_id

    # Half the tokens (or a strong character-level ratio) is the bar;
    # unrelated words score well below it against these short names.
    return best_id if best_score >= 0.5 else None


class Assistant(Agent):
    def __init__(self, instructions: str, ui: UIBridge | None = None) -> None:
        super().__init__(
            instructions=instructions,
        )
        # None only in tests / when no participant resolved; the UI tools then
        # report failure instead of pretending the browser moved.
        self._ui = ui or UIBridge(room=None, identity=None)
        # meal_id → times added this session. The cart lives in the browser, so
        # this is not a mirror of it — it is only here to catch the one mistake
        # the model reliably makes: adding a meal, then "adding the fries" as a
        # second call, which silently creates a second line for the same dish.
        self._added_this_session: dict[str, int] = {}
        # meal_id → the addon/spicy/quantity choices the user stated out loud,
        # so add_to_cart can honour them without the model repeating them.
        self._meal_options: dict[str, dict[str, Any]] = {}
        # Cross-page session memory: last UI snapshot + checkout fields the user
        # already said, so a page change does not wipe what they told سلمى.
        self._last_ui_state: dict[str, Any] = {}
        self._checkout_draft: dict[str, str] = {}
        self._last_meal_id: str | None = None
        self._last_restaurant: str | None = None
        # meal_ids we saved this call — get_saved uses this when the FE RPC
        # is missing so "المحفظات فاضية" cannot fire after we just saved.
        self._saved_this_session: list[str] = []

    @staticmethod
    def _tool_names(tools: Sequence[Any]) -> set[str]:
        names: set[str] = set()
        for tool in tools or []:
            info = getattr(tool, "_info", None) or getattr(tool, "info", None)
            name = getattr(info, "name", None) if info is not None else None
            if name:
                names.add(str(name))
        return names

    def _addons_from_last_user(self, context: RunContext, meal: "catalog.Meal") -> list[str]:
        """Pick addon ids the last user turn actually named (Bug-005)."""
        chat = getattr(self, "chat_ctx", None) or getattr(context, "chat_ctx", None)
        text = last_user_text(chat)
        if not text or not meal.addons:
            return []
        folded = catalog.normalize_ar(text)
        found: list[str] = []
        for addon in meal.addons:
            addon_id = str(addon.get("id") or "")
            name = catalog.normalize_ar(addon.get("name"))
            id_text = catalog.normalize_ar(addon_id.replace("-", " ").replace("_", " "))
            tokens = [t for t in f"{name} {id_text}".split() if len(t) >= 3]
            if name and name in folded:
                found.append(addon_id)
                continue
            if any(tok in folded for tok in tokens):
                found.append(addon_id)
                continue
            if _match_addon_loose(text, [addon]):
                found.append(addon_id)
        # Dedupe, keep catalog order.
        seen: set[str] = set()
        ordered: list[str] = []
        for addon_id in found:
            if addon_id not in seen:
                seen.add(addon_id)
                ordered.append(addon_id)
        return ordered

    async def llm_node(
        self,
        chat_ctx,
        tools,
        model_settings: ModelSettings,
    ):
        """Run the LLM, recovering tool calls printed as plain text.

        Gemma sometimes writes ``call:show_meal{…}`` into the assistant content
        instead of using the function-calling channel. Those leaks used to
        become empty TTS (and a hard session error) while the meal page never
        opened. The whole text turn is held until the stream ends so a call
        split across chunks is still recovered, then real ``FunctionToolCall``
        chunks are emitted and only leftover Arabic is spoken.
        """
        activity = self._get_activity_or_raise()
        assert activity.llm is not None, "llm_node called but no LLM is available"
        tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
        conn_options = activity.session.conn_options.llm_conn_options
        allowed = self._tool_names(tools)

        text_parts: list[str] = []
        last_chunk_id = "recovered"
        pending: list[Any] = []

        # Copy+truncate so a long session cannot fill Gemma's 32768 window
        # (live: 32769 input tokens, 0 output tokens, silent cutout).
        chat_ctx = prepare_chat_ctx_for_llm(chat_ctx)

        try:
            async with activity.llm.chat(
                chat_ctx=chat_ctx,
                tools=tools,
                tool_choice=tool_choice,
                conn_options=conn_options,
            ) as stream:
                async for chunk in stream:
                    if isinstance(chunk, ChatChunk):
                        last_chunk_id = chunk.id or last_chunk_id
                        if chunk.delta and chunk.delta.tool_calls:
                            pending.append(chunk)
                            continue
                        if chunk.delta and chunk.delta.content:
                            text_parts.append(chunk.delta.content)
                            continue
                        pending.append(chunk)
                    elif isinstance(chunk, str):
                        text_parts.append(chunk)
                    else:
                        pending.append(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM node failed; speaking cutout fallback error={}", exc)
            yield ChatChunk(
                id=last_chunk_id,
                delta=ChoiceDelta(role="assistant", content=CUTOUT_FALLBACK_SAY_AR),
            )
            return

        text = "".join(text_parts)
        speech, recovered = extract_text_tool_calls(text, allowed_names=allowed)
        speech = strip_speech_artifacts(speech)
        user_turn = last_user_text(chat_ctx)

        def _drop_pending_tools(names: set[str]) -> None:
            nonlocal pending
            kept: list[Any] = []
            for chunk in pending:
                if not isinstance(chunk, ChatChunk) or not chunk.delta:
                    kept.append(chunk)
                    continue
                calls = list(chunk.delta.tool_calls or [])
                if not calls:
                    kept.append(chunk)
                    continue
                filtered = [
                    tc for tc in calls if getattr(tc, "name", None) not in names
                ]
                if not filtered:
                    continue
                chunk.delta.tool_calls = filtered
                kept.append(chunk)
            pending = kept

        # Live 2026-08-17: "دي تاني معلش" still called add_to_cart a second time.
        # Same afternoon: calories / open-cart / reduce-qty / checkout all
        # recovered or called add_to_cart because تمام/سلة appeared in the turn.
        if not should_run_add_to_cart(user_turn, chat_ctx):
            _drop_pending_tools({"add_to_cart"})
            recovered = [c for c in recovered if c.get("name") != "add_to_cart"]
            logger.warning("Dropped add_to_cart; this turn is not an add request")

        # Structured tool calls from the provider first.
        for chunk in pending:
            yield chunk

        def _chunks_have_tool(name: str) -> bool:
            for chunk in pending:
                if not isinstance(chunk, ChatChunk) or not chunk.delta:
                    continue
                for tc in chunk.delta.tool_calls or []:
                    if getattr(tc, "name", None) == name:
                        return True
            return any(c.get("name") == name for c in recovered)

        # Live 2026-08-16: she said "فتحت لك صفحة … أميكو" after search_meals
        # with zero show_meal — recover it from the last search hit she named.
        # Live 2026-08-17: "تبي أوريك الصفحة؟" is the same — open, don't ask.
        if "show_meal" in allowed and not _chunks_have_tool("show_meal"):
            implied = recover_implied_show_meal(speech, chat_ctx)
            if implied:
                recovered = list(recovered) + [implied]
                logger.warning(
                    "Recovered implied show_meal after open-claim meal_id={}",
                    implied["arguments"].get("meal_id"),
                )

        if (
            "open_page" in allowed
            and not _chunks_have_tool("open_page")
            and not _chunks_have_tool("show_meal")
            and not _chunks_have_tool("show_restaurant")
        ):
            implied_page = recover_implied_open_page(speech, chat_ctx)
            if implied_page:
                recovered = list(recovered) + [implied_page]
                logger.warning(
                    "Recovered implied open_page after open-claim page={}",
                    implied_page["arguments"].get("page"),
                )

        suppress_speech = False
        if (
            "get_cart" in allowed
            and not _chunks_have_tool("get_cart")
            and speech.strip()
        ):
            implied_cart = recover_implied_get_cart(speech, chat_ctx)
            if implied_cart:
                recovered = list(recovered) + [implied_cart]
                suppress_speech = True
                logger.warning("Recovered implied get_cart; suppressed number hallucination")

        if (
            "get_saved" in allowed
            and not _chunks_have_tool("get_saved")
            and speech.strip()
        ):
            implied_saved = recover_implied_get_saved(speech, chat_ctx)
            if implied_saved:
                recovered = list(recovered) + [implied_saved]
                suppress_speech = True
                logger.warning("Recovered implied get_saved; suppressed empty-bookmark claim")

        if (
            "search_meals" in allowed
            and not _chunks_have_tool("search_meals")
        ):
            implied_search = recover_implied_search_meals(speech, chat_ctx)
            if implied_search:
                recovered = list(recovered) + [implied_search]
                suppress_speech = True
                logger.warning(
                    "Recovered implied search_meals after stall/no-restaurant hallucination"
                )

        if (
            "set_ui_filter" in allowed
            and not _chunks_have_tool("set_ui_filter")
            and not _chunks_have_tool("show_meal")
            and not _chunks_have_tool("show_restaurant")
            and not _chunks_have_tool("add_to_cart")
        ):
            implied_filter = recover_implied_set_ui_filter(speech, chat_ctx)
            if implied_filter:
                recovered = list(recovered) + [implied_filter]
                logger.warning(
                    "Recovered implied set_ui_filter kind={} query={}",
                    implied_filter["arguments"].get("offers_kind"),
                    implied_filter["arguments"].get("query"),
                )

        if (
            "add_to_cart" in allowed
            and not _chunks_have_tool("add_to_cart")
            and should_run_add_to_cart(user_turn, chat_ctx)
        ):
            implied_add = recover_implied_add_to_cart(speech, chat_ctx)
            if implied_add:
                recovered = list(recovered) + [implied_add]
                logger.warning(
                    "Recovered implied add_to_cart meal_id={}",
                    implied_add["arguments"].get("meal_id"),
                )

        def _any_tool() -> bool:
            if recovered:
                return True
            for chunk in pending:
                if (
                    isinstance(chunk, ChatChunk)
                    and chunk.delta
                    and chunk.delta.tool_calls
                ):
                    return True
            return False

        # Bug-010 fallback: "لحظة أشوف" with nothing to run. Recover a tool
        # from the last user turn / last search; if even that is empty, speak
        # a real sentence so TTS is not silent and the session does not die.
        if claims_stall(speech) and not _any_tool():
            stalled_calls = [
                c
                for c in recover_stalled_turn(speech, chat_ctx)
                if c["name"] in allowed
            ]
            if stalled_calls:
                recovered = list(recovered) + stalled_calls
                suppress_speech = True
                logger.warning(
                    "Recovered stalled turn into {}: {}",
                    [c["name"] for c in stalled_calls],
                    [c.get("arguments") for c in stalled_calls],
                )
            else:
                speech = STALL_FALLBACK_SAY_AR
                logger.warning("Stall with nothing to run; speaking fallback")
        elif claims_stall(speech):
            suppress_speech = True
            logger.warning("Suppressed stall speech; tools already in this turn")

        if recovered:
            logger.warning(
                "Recovered {} text-leaked tool call(s) into real function calls: {}",
                len(recovered),
                [(c["name"], c["arguments"]) for c in recovered],
            )
            yield ChatChunk(
                id=last_chunk_id,
                delta=ChoiceDelta(
                    role="assistant",
                    tool_calls=[
                        FunctionToolCall(
                            name=c["name"],
                            arguments=json.dumps(c["arguments"], ensure_ascii=False),
                            call_id=c["call_id"],
                        )
                        for c in recovered
                    ],
                ),
            )
        elif contains_text_tool_call(text):
            logger.warning(
                "Dropped unparseable text tool-call leak chars={} preview={!r}",
                len(text),
                text[:180],
            )
            speech = ""

        if speech.strip() and not suppress_speech:
            yield ChatChunk(
                id=last_chunk_id,
                delta=ChoiceDelta(role="assistant", content=speech),
            )
        elif not suppress_speech and not recovered and not _any_tool():
            # Empty stream with nothing to run. After a successful tool the
            # follow-up speech turn is often empty — that is not a cutout.
            if should_speak_cutout_fallback(chat_ctx):
                logger.warning("Empty LLM turn with no tools; speaking cutout fallback")
                yield ChatChunk(
                    id=last_chunk_id,
                    delta=ChoiceDelta(role="assistant", content=CUTOUT_FALLBACK_SAY_AR),
                )
            else:
                logger.info("Empty post-tool LLM turn; staying silent")

    # ─── Catalog ─────────────────────────────────────────────────────────────

    @function_tool()
    async def search_meals(
        self,
        context: RunContext,
        query: str = "",
        cuisine: str = "",
        protein_type: str = "",
        max_calories: int = 0,
        min_protein: int = 0,
        max_price: float = 0,
        min_price: float = 0,
        spicy: bool = False,
        only_offers: bool = False,
        restaurant: str = "",
        serves: int = 0,
        sort: str = "relevance",
        offer_kind: str = "",
        cashback: bool = False,
    ) -> dict[str, Any]:
        """
        Search meals. Call as soon as you know one thing the user wants.
        Speak names/prices/calories from `*_say_ar` fields, never convert digits.
        Leave cuisine and protein_type empty unless the user said them.

        Args:
            query: Arabic search text. Put حلويات here when they asked for sweets.
            cuisine: Only if they named a kitchen (شامي، حلويات، مشاوي).
            protein_type: meat/chicken/seafood/vegetarian only if they said it.
            max_calories: 0 = no limit.
            min_protein: 0 = no limit.
            max_price: 0 = no limit. For "أرخص" use sort=price_asc instead.
            min_price: 0 = no limit. For "أغلى" use sort=price_desc.
            spicy: True only if they asked for spicy meals.
            only_offers: True for combo/family meals. Not a discount filter.
            restaurant: Restrict to one restaurant only if they named it.
            serves: Headcount when they stated it. "أنا وأمي وأبوي" = 3.
            sort: relevance/calories_asc/calories_desc/price_asc/price_desc/rating_desc/protein_desc/fat_desc.
            offer_kind: combo, family, deal, or combo,family. Else empty.
            cashback: True for عروض الكاش باك.
        """
        protein = (protein_type or "").strip().lower()
        if protein and protein not in {"meat", "chicken", "seafood", "vegetarian"}:
            # A silently ignored bad value would look like "no meals with meat".
            raise ToolError(
                "protein_type must be meat, chicken, seafood or vegetarian"
            )

        cuisine_arg = (cuisine or "").strip()
        if cuisine_arg.lower() in {"none", "null", "nil"}:
            cuisine_arg = ""

        # The restaurant the user is standing in ranks first but does not filter.
        # Scoping hard to it (the earlier fix for missing مذاق الشام menu items)
        # backfires on a catalog this size: "ابغى بيتزا" said inside a grill
        # house came back empty and سلمى reported "ما عندنا بيتزا" with 63 in
        # the catalog. Preference gets the on-screen menu without that lie.
        restaurant_arg = (restaurant or "").strip()
        if restaurant_arg.lower() in {"none", "null", "nil"}:
            restaurant_arg = ""
        prefer_arg = restaurant_arg or (self._last_restaurant or "")

        result = catalog.search_meals(
            query=query or "",
            cuisine=cuisine_arg or None,
            protein_type=protein or None,
            max_calories=max_calories or None,
            min_protein=min_protein or None,
            max_price=max_price or None,
            min_price=min_price or None,
            spicy=True if spicy else None,
            only_offers=bool(only_offers),
            restaurant_id=restaurant_arg or None,
            prefer_restaurant_id=prefer_arg or None,
            serves=serves or None,
            offer_kind=offer_kind or None,
            cashback=True if cashback else None,
            sort=sort or "relevance",
        )
        logger.info(
            "Tool search_meals query={!r} cuisine={!r} protein={!r} max_cal={} "
            "min_protein={} max_price={} spicy={} offers={} restaurant={!r} "
            "prefer={!r} serves={} sort={} offer_kind={!r} -> total={} showing={} "
            "ambiguous={} ignored_restaurant={}",
            query, cuisine_arg, protein, max_calories, min_protein, max_price,
            spicy, only_offers, restaurant_arg, prefer_arg, serves, sort,
            offer_kind, result.get("total_matches"), result.get("showing"),
            (result.get("ambiguous") or {}).get("ask_about"),
            result.get("ignored_restaurant"),
        )
        # Bug-002: Offers Combo/Family chips are React state, not URL. When the
        # search is itself a filter request, push the same chips to the browser
        # even if the model forgot set_ui_filter.
        await self._sync_search_ui_filter(
            query=query or "",
            offer_kind=offer_kind or "",
            cuisine=cuisine_arg,
            cashback=bool(cashback),
        )
        return result

    async def _sync_search_ui_filter(
        self,
        *,
        query: str,
        offer_kind: str,
        cuisine: str,
        cashback: bool = False,
    ) -> None:
        folded = catalog.normalize_ar(query)
        kind = (offer_kind or "").strip().lower()
        if not kind:
            kinds: list[str] = []
            if any(w in folded for w in ("كومبو", "كمبو")):
                kinds.append("combo")
            if any(w in folded for w in ("عائلي", "عائله")):
                kinds.append("family")
            if any(w in folded for w in ("ديل", "deal")):
                kinds.append("deal")
            kind = ",".join(kinds)
        ui_query = ""
        if any(w in folded for w in ("مقرمش", "كرسبي", "كريسبي", "crispy")):
            ui_query = "مقرمش"
        elif "كيتو" in folded or "keto" in folded:
            ui_query = "كيتو"
        wants_cashback = cashback or (
            "كاش" in folded or "cashback" in folded or "كاشباك" in folded
        )
        if not kind and not ui_query and not wants_cashback:
            return
        page = "offers" if (kind or wants_cashback) else "search"
        if kind or wants_cashback:
            path = build_offers_path(
                kind=kind,
                cashback=wants_cashback,
                cuisine=cuisine,
                query=ui_query,
            )
        else:
            path = f"/search?q={ui_query}" if ui_query else ""
        outcome = await self._ui.set_ui_filter(
            page=page,
            offers_kind=kind,
            query=ui_query,
            cuisine=cuisine,
            cashback=wants_cashback,
            path=path,
        )
        logger.info(
            "Tool search_meals synced UI filter kind={!r} query={!r} cashback={} "
            "ok={} reason={}",
            kind, ui_query, wants_cashback, outcome.ok, outcome.reason,
        )
        if wants_cashback and path:
            nav = await self._ui.navigate(path)
            logger.info(
                "Tool search_meals navigated cashback path={} ok={} reason={}",
                path, nav.ok, nav.reason,
            )

    @function_tool()
    async def get_meal_details(
        self,
        context: RunContext,
        meal_id: str,
    ) -> dict[str, Any]:
        """
        Full details for one meal, including its addons list. Call this before
        offering additions, and again if the user disputes a price you just
        said. Speak prices from `price_sentence_ar` or `price_say_ar`
        verbatim, never from the digits.

        Args:
            meal_id: The meal_id from a previous search result.
        """
        details = catalog.meal_details(meal_id)
        if details is None:
            raise ToolError(
                f"No meal with id {meal_id!r}. Run search_meals again and use a "
                "meal_id from its results."
            )
        logger.info(
            "Tool get_meal_details meal_id={} name={} addons={}",
            meal_id, details["name"], len(details["addons"]),
        )
        return details

    # ─── UI control (LiveKit RPC to the browser) ─────────────────────────────

    @function_tool()
    async def show_meal(
        self,
        context: RunContext,
        meal_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """
        Open the meal's page in the user's app so they can see it while you
        talk. Call this once you have settled on a recommendation. The result
        includes `price_sentence_ar` — read that price, do not convert
        `price_sar` yourself.

        Args:
            meal_id: The meal_id from a previous search result.
            reason: One short Arabic sentence on why this meal, shown in the UI.
        """
        meal = catalog.load_catalog().by_id(meal_id)
        if meal is None:
            raise ToolError(
                f"No meal with id {meal_id!r}. Search first and use a real meal_id."
            )

        outcome = await self._ui.show_meal(
            meal_id=meal.id, path=meal.page_path, reason=reason
        )
        logger.info(
            "Tool show_meal meal_id={} path={} ok={} reason_for_failure={}",
            meal.id, meal.page_path, outcome.ok, outcome.reason,
        )
        if outcome.ok:
            self._last_meal_id = meal.id
            self._last_ui_state = {
                "path": meal.page_path,
                "page": "meal",
                "mealId": meal.id,
                "titleAr": meal.name,
            }
        result = outcome.as_tool_result(
            on_success=f"فتحت لك صفحة {meal.name}",
            on_failure="ما قدرت أفتح الصفحة، افتحها من التطبيق",
        )
        # Canonical meal facts ride along so she quotes the on-screen price
        # and headcount instead of converting digits from memory. Live
        # failure: after opening the 71-riyal pizza-for-3 she said 61, then
        # repeated 61 when the user challenged it, never calling get_meal_details.
        details = catalog.meal_details(meal.id)
        if details:
            for key in (
                "name_say_ar",
                "price_sar",
                "price_say_ar",
                "price_sentence_ar",
                "price_speech_rule_ar",
                "serves_people",
                "serves_say_ar",
                "serves_rule_ar",
                "includes",
                "includes_say_ar",
                "calories",
                "calories_say_ar",
                "protein_g",
                "protein_say_ar",
            ):
                if key in details:
                    result[key] = details[key]
        result["spicy_available"] = meal.spicy_option
        result["addons"] = [
            {
                "addon_id": a.get("id"),
                "name": a.get("name"),
                "price_sar": a.get("price"),
                "price_say_ar": catalog.sar_words(float(a.get("price") or 0)),
            }
            for a in meal.addons
        ]
        return result

    @function_tool()
    async def show_restaurant(
        self,
        context: RunContext,
        restaurant: str,
    ) -> dict[str, Any]:
        """
        Open one restaurant's page in the user's app, and get its details plus
        a few of its popular dishes. Use when the user asks about a specific
        restaurant rather than a dish.

        Args:
            restaurant: The restaurant's Arabic name (e.g. "بيت الشواية") or a
                restaurant_id from an earlier search result. Never invent an id.
                If the user named a dish + people count (شاورما لخمسة أفراد),
                do NOT use this tool — call search_meals instead.
        """
        raw = (restaurant or "").strip()
        if catalog.looks_like_meal_query(raw) and catalog.restaurant_details(raw) is None:
            raise ToolError(
                f"{raw!r} looks like a meal/offer search, not a restaurant name. "
                "Call search_meals with that text (and serves if they named a "
                "headcount). Do NOT tell the user there is no restaurant."
            )
        details = catalog.restaurant_details(raw)
        if details is None:
            raise ToolError(
                f"No restaurant matching {raw!r}. If the user asked for a dish, "
                "call search_meals instead of telling them the restaurant is missing."
            )

        outcome = await self._ui.navigate(details["page_path"])
        logger.info(
            "Tool show_restaurant input={!r} resolved={} path={} ok={} reason={}",
            restaurant, details["restaurant_id"], details["page_path"],
            outcome.ok, outcome.reason,
        )

        result = outcome.as_tool_result(
            on_success=f"فتحت لك صفحة {details['name']}",
            on_failure="ما قدرت أفتح صفحة المطعم، افتحها من التطبيق",
        )
        if outcome.ok:
            self._last_restaurant = details.get("name")
            self._last_ui_state = {
                "path": details.get("page_path"),
                "page": "restaurant",
                "restaurantId": details.get("restaurant_id"),
                "titleAr": details.get("name"),
            }
        # The details are returned either way: even when the page did not open,
        # سلمى can still answer questions about the restaurant from real data.
        result.update(details)
        return result

    @function_tool()
    async def open_page(
        self,
        context: RunContext,
        page: str,
    ) -> dict[str, Any]:
        """
        Open one of the app's main pages for the user.

        Args:
            page: One of "home", "restaurants", "grocery", "fast", "24h",
                "cart", "checkout", "orders", "offers", "search", "saved",
                "cashback". Arabic names are accepted too: الرئيسية، المطاعم،
                البقالة، توصيل سريع، السلة، إتمام الطلب، الطلبات، العروض،
                البحث، المحفوظات، كاش باك. "المطبخ" / "kitchen" open the
                restaurants list. "البقالة" opens /restaurants?filter=grocery.
                "توصيل سريع" / "صفحة سريع" opens /restaurants?filter=fast.
                "عروض الكاش باك" opens /offers?cashback=1.
                Call this immediately when the user says روح للسلة / وريني
                السلة / افتح السلة / الكارت / ادخل على المطبخ / صفحة المطاعم /
                افتح البقالة / افتحي صفحة التوصيل سريعا / عروض الكاش باك.
        """
        paths = {
            "home": "/",
            "restaurants": "/restaurants",
            "grocery": "/restaurants?filter=grocery",
            "fast": "/restaurants?filter=fast",
            "24h": "/restaurants?filter=24h",
            "cart": "/cart",
            "checkout": "/checkout",
            "orders": "/orders",
            "offers": "/offers",
            "search": "/search",
            "saved": "/saved",
            "cashback": "/offers?cashback=1",
        }
        aliases = {
            "الرئيسية": "home",
            "الرئيسيه": "home",
            "البيت": "home",
            "المطاعم": "restaurants",
            "مطاعم": "restaurants",
            "المطبخ": "restaurants",
            "مطبخ": "restaurants",
            "المطابخ": "restaurants",
            "kitchen": "restaurants",
            "kitchens": "restaurants",
            "صفحة المطاعم": "restaurants",
            "صحة المطاعم": "restaurants",
            "البقالة": "grocery",
            "بقالة": "grocery",
            "صفحة البقالة": "grocery",
            "grocery": "grocery",
            "توصيل سريع": "fast",
            "التوصيل السريع": "fast",
            "صفحة التوصيل": "fast",
            "صفحة سريع": "fast",
            "fast": "fast",
            "24 ساعة": "24h",
            "٢٤ ساعة": "24h",
            "مفتوح 24 ساعة": "24h",
            "24h": "24h",
            "24/7": "24h",
            "٢٤/٧": "24h",
            "على مدار الساعة": "24h",
            "على مدار الساعه": "24h",
            "السلة": "cart",
            "سله": "cart",
            "سلة": "cart",
            "السله": "cart",
            "الكارت": "cart",
            "الكرت": "cart",
            "الكارد": "cart",
            "السالفه": "cart",
            "السالفة": "cart",
            "اتمام الطلب": "checkout",
            "إتمام الطلب": "checkout",
            "الدفع": "checkout",
            "الطلبات": "orders",
            "العروض": "offers",
            "البحث": "search",
            "المحفوظات": "saved",
            "كاش باك": "cashback",
            "الكاش باك": "cashback",
            "كاشباك": "cashback",
            "عروض الكاش باك": "cashback",
            "cashback": "cashback",
        }
        raw = (page or "").strip()
        key = raw.lower()
        folded = catalog.normalize_ar(raw)
        target_key = (
            key if key in paths
            else aliases.get(raw) or aliases.get(folded) or aliases.get(key)
        )
        target = paths.get(target_key or "")
        if not target or not target_key:
            raise ToolError(f"Unknown page {page!r}. Use one of: {', '.join(paths)}")

        outcome = await self._ui.navigate(target)
        logger.info("Tool open_page page={} resolved={} path={} ok={}", page, target_key, target, outcome.ok)
        if outcome.ok:
            self._last_ui_state = {
                "path": target,
                "page": target_key,
                "titleAr": {
                    "home": "الرئيسية",
                    "restaurants": "المطاعم",
                    "grocery": "البقالة",
                    "fast": "توصيل سريع",
                    "24h": "مفتوح 24 ساعة",
                    "cart": "السلة",
                    "checkout": "إتمام الطلب",
                    "orders": "الطلبات",
                    "offers": "العروض",
                    "search": "البحث",
                    "saved": "المحفوظات",
                    "cashback": "عروض الكاش باك",
                }.get(target_key, target),
            }
        return outcome.as_tool_result(
            on_success="فتحتها لك",
            on_failure="ما قدرت أفتحها، افتحها من التطبيق",
        )

    @function_tool()
    async def press_checkout_button(self, context: RunContext) -> dict[str, Any]:
        """
        Press the "إتمام الطلب" button in the cart, which opens the checkout
        form. Call this immediately, with no questions first, whenever the user
        says إتمام الطلب / كملي الطلب / اضغطي الزر / نروح للدفع. Do NOT ask for
        name, phone or address before pressing — press first, then ask once for
        whatever is still missing.
        """
        outcome = await self._ui.navigate("/checkout")
        logger.info("Tool press_checkout_button ok={} reason={}", outcome.ok, outcome.reason)
        if not outcome.ok:
            return outcome.as_tool_result(
                on_success="",
                on_failure="ما قدرت أضغط زر إتمام الطلب، اضغطه من التطبيق",
            )

        self._last_ui_state = {
            "path": "/checkout",
            "page": "checkout",
            "titleAr": "إتمام الطلب",
        }
        # Re-apply anything the user already told us so a returning user does
        # not get asked for the same three fields twice.
        if self._checkout_draft:
            await self._ui.fill_checkout(
                name=self._checkout_draft.get("name"),
                phone=self._checkout_draft.get("phone"),
                address=self._checkout_draft.get("address"),
            )

        missing = [
            key for key in ("name", "phone", "address")
            if not self._checkout_draft.get(key)
        ]
        return {
            "ok": True,
            "page": "checkout",
            "checkout_draft": dict(self._checkout_draft),
            "missing_fields": missing,
            "say_to_user_ar": (
                "ضغطت إتمام الطلب. أعطني الاسم ورقم الجوال والعنوان."
                if len(missing) == 3
                else "ضغطت إتمام الطلب وعبّيت اللي عندي."
            ),
        }

    @function_tool()
    async def get_ui_state(self, context: RunContext) -> dict[str, Any]:
        """
        Read which page the user is looking at right now in the app (path,
        page name, open meal/restaurant id). Call this when they ask "وين أنا؟"
        / "وش قدامي؟", or before acting on a page-specific control, or after
        navigating so your next sentence matches the screen.
        """
        outcome = await self._ui.get_ui_state()
        logger.info(
            "Tool get_ui_state ok={} reason={} data={}",
            outcome.ok, outcome.reason, outcome.data,
        )
        if outcome.ok and outcome.data:
            self._last_ui_state = {
                k: outcome.data.get(k)
                for k in ("path", "page", "mealId", "restaurantId", "titleAr")
                if outcome.data.get(k) is not None
            }
            return {
                "ok": True,
                "source": "rpc",
                "path": outcome.data.get("path"),
                "page": outcome.data.get("page"),
                "meal_id": outcome.data.get("mealId"),
                "restaurant_id": outcome.data.get("restaurantId"),
                "title_ar": outcome.data.get("titleAr"),
                "session_memory": {
                    "last_meal_id": self._last_meal_id,
                    "last_restaurant": self._last_restaurant,
                    "checkout_draft": dict(self._checkout_draft),
                    "last_ui_state": dict(self._last_ui_state),
                },
                "say_to_user_ar": f"أنت الحين في {outcome.data.get('titleAr') or 'التطبيق'}",
            }

        # Browser pushes luqma_* attributes on every route change — works even
        # when the getUiState RPC handler is missing from an old tab.
        attrs = self._ui.page_attributes()
        if attrs.get("luqma_path"):
            self._last_ui_state = {
                "path": attrs.get("luqma_path"),
                "page": attrs.get("luqma_page"),
                "mealId": attrs.get("luqma_meal_id") or None,
                "restaurantId": attrs.get("luqma_restaurant_id") or None,
                "titleAr": attrs.get("luqma_title") or attrs.get("luqma_path"),
            }
            title = self._last_ui_state.get("titleAr") or "التطبيق"
            return {
                "ok": True,
                "source": "attributes",
                "path": self._last_ui_state.get("path"),
                "page": self._last_ui_state.get("page"),
                "meal_id": self._last_ui_state.get("mealId"),
                "restaurant_id": self._last_ui_state.get("restaurantId"),
                "title_ar": title,
                "session_memory": {
                    "last_meal_id": self._last_meal_id,
                    "last_restaurant": self._last_restaurant,
                    "checkout_draft": dict(self._checkout_draft),
                    "last_ui_state": dict(self._last_ui_state),
                },
                "say_to_user_ar": f"أنت الحين في {title}",
            }

        # Fall back to whatever we last navigated to ourselves.
        if self._last_ui_state:
            return {
                "ok": True,
                "stale": True,
                **self._last_ui_state,
                "session_memory": {
                    "last_meal_id": self._last_meal_id,
                    "last_restaurant": self._last_restaurant,
                    "checkout_draft": dict(self._checkout_draft),
                },
                "say_to_user_ar": (
                    f"آخر صفحة فتحتها لك هي "
                    f"{self._last_ui_state.get('titleAr') or self._last_ui_state.get('path')}"
                ),
            }
        return outcome.as_tool_result(
            on_success="هذي الصفحة الحالية",
            on_failure="ما قدرت أعرف الصفحة الحالية من التطبيق",
        )

    @function_tool()
    async def fill_checkout_form(
        self,
        context: RunContext,
        name: str = "",
        phone: str = "",
        address: str = "",
    ) -> dict[str, Any]:
        """
        Fill the checkout form fields the user said out loud. Opens /checkout
        first if needed, writes the fields into the form, and remembers them
        for complete_order. Phone must be a Saudi mobile: 05XXXXXXXX.

        Args:
            name: Customer full name.
            phone: Saudi mobile, digits like 05XXXXXXXX.
            address: Delivery address (district, street, building).
        """
        if name:
            self._checkout_draft["name"] = written_text(name)
        if phone:
            self._checkout_draft["phone"] = written_phone(phone)
        if address:
            self._checkout_draft["address"] = written_text(address)

        # Make sure the form is on screen so the user sees fields fill in.
        nav = await self._ui.navigate("/checkout")
        if nav.ok:
            self._last_ui_state = {
                "path": "/checkout",
                "page": "checkout",
                "titleAr": "إتمام الطلب",
            }

        outcome = await self._ui.fill_checkout(
            name=self._checkout_draft.get("name"),
            phone=self._checkout_draft.get("phone"),
            address=self._checkout_draft.get("address"),
        )
        logger.info(
            "Tool fill_checkout_form draft={} nav_ok={} fill_ok={} reason={}",
            self._checkout_draft, nav.ok, outcome.ok, outcome.reason,
        )
        # Buffered fill (page not mounted yet) still counts as success — the
        # checkout page applies pending fields as soon as it mounts.
        fill_ok = outcome.ok or (outcome.data or {}).get("buffered") is True
        if fill_ok:
            result = {
                "ok": True,
                "say_to_user_ar": "عبّيت البيانات في نموذج إتمام الطلب",
                "checkout_draft": dict(self._checkout_draft),
                "page_opened": nav.ok,
                "buffered": bool((outcome.data or {}).get("buffered")),
            }
            return result
        result = outcome.as_tool_result(
            on_success="عبّيت البيانات في نموذج إتمام الطلب",
            on_failure=(
                "حفظت البيانات عندي. أكمل التأكيد بـ complete_order "
                "باسم ورقم جوال سعودي وعنوان"
            ),
        )
        result["checkout_draft"] = dict(self._checkout_draft)
        result["page_opened"] = nav.ok
        return result

    @function_tool()
    async def complete_order(
        self,
        context: RunContext,
        name: str = "",
        phone: str = "",
        address: str = "",
    ) -> dict[str, Any]:
        """
        Place the order from the user's cart and open the success page. Only
        call after the user clearly asked to confirm / finish the order AND
        you have name + Saudi phone (05XXXXXXXX) + address. Missing fields can
        be taken from checkout_draft remembered earlier in this call.

        Args:
            name: Customer full name (or empty to reuse draft).
            phone: Saudi mobile 05XXXXXXXX (or empty to reuse draft).
            address: Delivery address (or empty to reuse draft).
        """
        if name.strip():
            self._checkout_draft["name"] = written_text(name)
        if phone.strip():
            self._checkout_draft["phone"] = written_phone(phone)
        if address.strip():
            self._checkout_draft["address"] = written_text(address)

        final_name = written_text(self._checkout_draft.get("name", ""))
        final_phone = written_phone(self._checkout_draft.get("phone", ""))
        final_address = written_text(self._checkout_draft.get("address", ""))

        missing = [
            label
            for label, val in (
                ("name", final_name),
                ("phone", final_phone),
                ("address", final_address),
            )
            if not val
        ]
        if missing:
            return {
                "ok": False,
                "failure_reason": "missing_fields",
                "missing": missing,
                "checkout_draft": dict(self._checkout_draft),
                "say_to_user_ar": (
                    "باقي بيانات ناقصة عشان أكمل الطلب: "
                    + " و".join(
                        {"name": "الاسم", "phone": "رقم الجوال", "address": "العنوان"}[m]
                        for m in missing
                    )
                ),
            }

        outcome = await self._ui.complete_order(
            name=final_name, phone=final_phone, address=final_address
        )
        logger.info(
            "Tool complete_order ok={} reason={} data={}",
            outcome.ok, outcome.reason, outcome.data,
        )
        if outcome.ok:
            self._last_ui_state = {
                "path": (outcome.data or {}).get("path") or "/order-success",
                "page": "order_success",
                "titleAr": "نجاح الطلب",
            }
            order_id = (outcome.data or {}).get("orderId")
            total = (outcome.data or {}).get("total")
            total_say = catalog.sar_words(float(total or 0))
            return {
                "ok": True,
                "order_id": order_id,
                "total_sar": total,
                "total_say_ar": total_say,
                "say_to_user_ar": f"تم تأكيد طلبك، والمجموع {total_say}",
            }

        reason = outcome.reason or "unknown"
        say = {
            "empty_cart": "سلتك فارغة، ما أقدر أكمل طلب فاضي",
            "bad_phone": "رقم الجوال لازم يكون سعودي مثل صفر خمسة وبعده ثمان أرقام",
            "missing_fields": "باقي بيانات ناقصة عشان أكمل الطلب",
        }.get(reason, "ما قدرت أكمل تأكيد الطلب من التطبيق")
        return {
            "ok": False,
            "failure_reason": reason,
            "checkout_draft": dict(self._checkout_draft),
            "say_to_user_ar": say,
        }

    def _resolve_meal_options(
        self,
        meal: "catalog.Meal",
        addon_ids: list[str] | None,
        spicy: bool,
        quantity: int,
        *,
        tool: str,
    ) -> dict[str, Any]:
        """Validate addons/spicy/quantity against one meal and price the line.

        Shared by set_meal_options and add_to_cart so the additions the user
        sees ticked on the page are exactly the ones that reach the cart, at
        exactly the total سلمى said out loud. Two code paths pricing the same
        line is how the screen and the voice start disagreeing.
        """
        quantity = max(1, int(quantity or 1))
        if quantity > MAX_LINE_QUANTITY:
            raise ToolError(
                f"quantity {quantity} is too high for one order line "
                f"(max {MAX_LINE_QUANTITY}). Ask the user to confirm a "
                "realistic count — do not add this quantity."
            )

        # Addons are validated against this meal, not accepted on trust: an
        # invented addon id would either 500 the frontend or, worse, be quoted
        # to the user as a real extra with a real price. But validation is
        # loose on spelling: the model often passes the addon *name* as it
        # heard it, and the ASR garbles Arabic addon names into English-ish
        # text ("Sauce Tomb" for "صوص ثوم"). Exact ids pass through untouched;
        # anything else is resolved against this meal's addon ids and names.
        valid = {str(a.get("id")): a for a in meal.addons}
        requested = [str(a) for a in (addon_ids or [])]
        accepted: list[str] = []
        rejected: list[str] = []
        for req in requested:
            match = req if req in valid else _match_addon_loose(req, meal.addons)
            if match and match not in accepted:
                accepted.append(match)
                if match != req:
                    logger.info(
                        "Tool {} resolved addon {!r} -> {!r} meal_id={}",
                        tool, req, match, meal.id,
                    )
            elif not match:
                rejected.append(req)
        if rejected:
            logger.warning(
                "Tool {} dropped unknown addons meal_id={} rejected={} valid={}",
                tool, meal.id, rejected, list(valid),
            )

        if spicy and not meal.spicy_option:
            spicy = False

        addons_total = sum(float(valid[a].get("price") or 0) for a in accepted)
        line_total = (meal.effective_price + addons_total) * quantity

        return {
            "quantity": quantity,
            "spicy": spicy,
            "accepted": accepted,
            "rejected": rejected,
            "addon_names": [valid[a].get("name") for a in accepted],
            "line_total": line_total,
        }

    async def _cart_snapshot(self) -> dict[str, Any]:
        """Live cart totals from the browser — never invent them from memory."""
        outcome = await self._ui.get_cart()
        if not outcome.ok:
            return {"ok": False, "failure_reason": outcome.reason}
        lines: list[dict[str, Any]] = []
        subtotal = 0.0
        loaded = catalog.load_catalog()
        for item in (outcome.data or {}).get("items", []) or []:
            meal = loaded.by_id(item.get("mealId", ""))
            qty = int(item.get("quantity") or 1)
            unit = float(item.get("unitPrice") or 0.0)
            subtotal += unit * qty
            line = {
                "meal_id": item.get("mealId"),
                "name": meal.name if meal else item.get("mealId"),
                "name_say_ar": catalog.speakable_name(meal.name) if meal else "",
                "quantity": qty,
                "unit_price_sar": unit,
                "line_total_sar": round(unit * qty, 2),
                "line_total_say_ar": catalog.sar_words(unit * qty),
            }
            if meal:
                line["calories"] = meal.calories
                line["protein_g"] = meal.protein
                line["carbs_g"] = meal.carbs
                line["fat_g"] = meal.fat
            lines.append(line)
        snap: dict[str, Any] = {
            "ok": True,
            "items": lines,
            "item_count": sum(line["quantity"] for line in lines),
            "subtotal_sar": round(subtotal, 2),
            "subtotal_say_ar": catalog.sar_words(subtotal),
            "is_empty": not lines,
            "cart_speech_rule_ar": (
                "انطقي subtotal_say_ar حرف بحرف. هذا مجموع السلة الحقيقي "
                "على الشاشة. ممنوع تقولين رقم من line_total أو من ذاكرتك."
            ),
        }
        snap.update(catalog.cart_nutrition(lines))
        return snap

    @function_tool()
    async def set_meal_options(
        self,
        context: RunContext,
        meal_id: str,
        quantity: int = 1,
        spicy: bool = False,
        addon_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Tick addons/spicy on the open meal page so the user sees them.
        Does NOT add to the cart. Call add_to_cart only if they said للسلة.

        Args:
            meal_id: meal_id of the open meal.
            quantity: How many, at least 1.
            spicy: True only if they asked for spicy.
            addon_ids: addon_id values from get_meal_details.
        """
        meal = catalog.load_catalog().by_id(meal_id)
        if meal is None:
            raise ToolError(
                f"No meal with id {meal_id!r}. Search first and use a real meal_id."
            )

        opts = self._resolve_meal_options(
            meal, addon_ids, spicy, quantity, tool="set_meal_options"
        )

        outcome = await self._ui.set_meal_options(
            meal_id=meal.id,
            quantity=opts["quantity"],
            spicy=opts["spicy"],
            addon_ids=opts["accepted"],
        )
        # Remembered whether or not the UI accepted it: the user stated these
        # choices out loud, so add_to_cart must honour them even if the page
        # never showed them.
        self._meal_options[meal.id] = {
            "addon_ids": opts["accepted"],
            "spicy": opts["spicy"],
            "quantity": opts["quantity"],
        }
        logger.info(
            "Tool set_meal_options meal_id={} qty={} spicy={} addons={} total={} ok={} reason={}",
            meal.id, opts["quantity"], opts["spicy"], opts["accepted"],
            opts["line_total"], outcome.ok, outcome.reason,
        )

        result = outcome.as_tool_result(
            on_success="حدثت الخيارات في الصفحة",
            on_failure="ما قدرت أعلمها في الصفحة، بس خياراته محفوظة عندي",
        )
        result.update(
            {
                "meal_name": meal.name,
                "meal_name_say_ar": catalog.speakable_name(meal.name),
                "quantity": opts["quantity"],
                "spicy": opts["spicy"],
                "addons_selected": opts["addon_names"],
                "addons_selected_say_ar": [
                    catalog.speakable_name(n) for n in opts["addon_names"] if n
                ],
                "line_total_sar": round(opts["line_total"], 2),
                "line_total_say_ar": catalog.sar_words(opts["line_total"]),
                "added_to_cart": False,
            }
        )
        if opts["rejected"]:
            result["ignored_unknown_addons"] = opts["rejected"]
        return result

    @function_tool()
    async def add_to_cart(
        self,
        context: RunContext,
        meal_id: str,
        quantity: int = 1,
        spicy: bool = False,
        addon_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Add a meal to the cart. Call only when THIS turn says to put it in
        the cart (ضيفها للسلة / حطها). Once per request — a second call
        duplicates the line. Not for opening the cart, changing quantity,
        calories, or addons (open_page / set_cart_quantity / get_cart /
        set_meal_options).

        Args:
            meal_id: meal_id from a previous search.
            quantity: How many to add, at least 1.
            spicy: True only if they asked for spicy.
            addon_ids: From get_meal_details. Omit to keep set_meal_options.
        """
        meal = catalog.load_catalog().by_id(meal_id)
        if meal is None:
            raise ToolError(
                f"No meal with id {meal_id!r}. Search first and use a real meal_id."
            )

        chat = getattr(self, "chat_ctx", None) or getattr(context, "chat_ctx", None)
        user_turn = last_user_text(chat)
        if user_turn and not should_run_add_to_cart(user_turn, chat):
            logger.warning(
                "Blocked add_to_cart; user did not ask to add this turn meal_id={}",
                meal.id,
            )
            cart = await self._cart_snapshot()
            skipped = {
                "ok": True,
                "skipped": True,
                "skip_reason": "user_did_not_ask_to_add",
                "meal_id": meal.id,
                "meal_name": meal.name,
                "say_to_user_ar": "ما ضفتها للسلة لأن الطلب مو إضافة.",
                "on_success": "ما ضفتها للسلة",
            }
            if cart.get("ok"):
                skipped["cart"] = cart
                skipped["subtotal_sar"] = cart.get("subtotal_sar")
                skipped["subtotal_say_ar"] = cart.get("subtotal_say_ar")
            return skipped

        if user_cancels_repeat_add(user_turn):
            logger.warning(
                "Blocked add_to_cart; user cancelled a repeat add meal_id={}",
                meal.id,
            )
            cart = await self._cart_snapshot()
            skipped = {
                "ok": True,
                "skipped": True,
                "skip_reason": "user_cancelled_repeat",
                "meal_id": meal.id,
                "meal_name": meal.name,
                "say_to_user_ar": f"{meal.name} موجودة. ما ضفت نسخة ثانية.",
                "on_success": f"ما ضفت {meal.name} مرة ثانية",
            }
            if cart.get("ok"):
                skipped["cart"] = cart
                skipped["subtotal_sar"] = cart.get("subtotal_sar")
                skipped["subtotal_say_ar"] = cart.get("subtotal_say_ar")
            return skipped

        # Omitting addon_ids after set_meal_options means "the options I already
        # chose", not "nothing". Losing the additions between the two calls
        # would silently ship an order the user did not agree to — and the model
        # does drop them: it treats the second call as a fresh one.
        remembered = self._meal_options.get(meal.id)
        if addon_ids is None and remembered:
            addon_ids = remembered["addon_ids"]
            if not spicy:
                spicy = remembered["spicy"]
            if quantity == 1 and remembered["quantity"] != 1:
                quantity = remembered["quantity"]
            logger.info(
                "Tool add_to_cart reused options from set_meal_options meal_id={} {}",
                meal.id, remembered,
            )

        # Live Bug-005: "ضيفها مع بطاطس وصوص" called add_to_cart with
        # addon_ids=[] so extras never reached the cart. Scrape the last user
        # turn against this meal's addon list.
        if not addon_ids:
            scraped = self._addons_from_last_user(context, meal)
            if scraped:
                addon_ids = scraped
                logger.info(
                    "Tool add_to_cart scraped addons from last user turn meal_id={} {}",
                    meal.id, scraped,
                )

        opts = self._resolve_meal_options(
            meal, addon_ids, spicy, quantity, tool="add_to_cart"
        )
        quantity, spicy = opts["quantity"], opts["spicy"]
        accepted, rejected = opts["accepted"], opts["rejected"]
        line_total = opts["line_total"]

        outcome = await self._ui.add_to_cart(
            meal_id=meal.id, quantity=quantity, spicy=spicy, addon_ids=accepted
        )
        logger.info(
            "Tool add_to_cart meal_id={} qty={} spicy={} addons={} total={} ok={} reason={}",
            meal.id, quantity, spicy, accepted, line_total, outcome.ok, outcome.reason,
        )

        result = outcome.as_tool_result(
            on_success=f"أضفت {meal.name} للسلة",
            on_failure="ما قدرت أضيفها للسلة، ضيفها من التطبيق",
        )
        result.update(
            {
                "meal_name": meal.name,
                "meal_name_say_ar": catalog.speakable_name(meal.name),
                "quantity": quantity,
                "spicy": spicy,
                "addons_added": opts["addon_names"],
                "addons_added_say_ar": [
                    catalog.speakable_name(n) for n in opts["addon_names"] if n
                ],
                "line_total_sar": round(line_total, 2),
                "line_total_say_ar": catalog.sar_words(line_total),
                "addons_surcharge_sar": round(
                    line_total - meal.effective_price * quantity, 2
                ),
                "price_speech_rule_ar": (
                    "انطقي line_total_say_ar حرف بحرف — هذا سعر الوجبة مع "
                    "الإضافات. ممنوع تقولين سعر الوجبة بدون الإضافات."
                ),
            }
        )
        if rejected:
            result["ignored_unknown_addons"] = rejected

        if outcome.ok:
            previous = self._added_this_session.get(meal.id, 0)
            self._added_this_session[meal.id] = previous + 1
            if previous:
                result["duplicate_note_ar"] = (
                    f"{meal.name} كانت مضافة قبل في هذي المكالمة. "
                    "add_to_cart *يزيد* الكمية، ما يستبدلها. إذا المستخدم "
                    "قال 'خليهم اثنين' أو 'واحد بس'، استخدمي set_cart_quantity "
                    "بالعدد المطلق."
                )
            # Always re-read the cart: line_total is only this call's payload,
            # while the browser may already have had quantity on the same line.
            cart = await self._cart_snapshot()
            if cart.get("ok"):
                result["cart"] = cart
                result["subtotal_sar"] = cart["subtotal_sar"]
                result["subtotal_say_ar"] = cart["subtotal_say_ar"]
                result["cart_item_count"] = cart["item_count"]
        return result

    @function_tool()
    async def set_cart_quantity(
        self,
        context: RunContext,
        meal_id: str,
        quantity: int,
        spicy: bool = False,
        addon_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Set the absolute quantity of a meal already in the cart, or remove it.
        Use this when the user changes how many they want ("خليهم واحد",
        "اعمل اثنين", "شيلها من السلة"). Do NOT use add_to_cart for that —
        add_to_cart adds on top of what is already there.

        Args:
            meal_id: The meal_id of the cart line.
            quantity: Absolute count. 0 removes the line entirely.
            spicy: Match the spicy flag of the cart line if known.
            addon_ids: Match the addons of the cart line if known; omit to
                target the only line for this meal_id.
        """
        meal = catalog.load_catalog().by_id(meal_id)
        if meal is None:
            raise ToolError(
                f"No meal with id {meal_id!r}. Use a meal_id from get_cart."
            )
        if quantity < 0:
            raise ToolError("quantity must be 0 or more")
        if quantity > MAX_LINE_QUANTITY:
            raise ToolError(
                f"quantity {quantity} is too high for one order line "
                f"(max {MAX_LINE_QUANTITY}). Ask the user to confirm a "
                "realistic count — do not set this quantity."
            )

        remembered = self._meal_options.get(meal.id)
        if addon_ids is None and remembered:
            addon_ids = remembered["addon_ids"]
            if not spicy:
                spicy = remembered["spicy"]

        accepted: list[str] = []
        if addon_ids:
            known = {str(a.get("id")) for a in meal.addons}
            accepted = [a for a in addon_ids if a in known]

        outcome = await self._ui.set_cart_quantity(
            meal_id=meal.id,
            quantity=quantity,
            spicy=spicy,
            addon_ids=accepted,
        )
        logger.info(
            "Tool set_cart_quantity meal_id={} qty={} spicy={} addons={} ok={} reason={}",
            meal.id, quantity, spicy, accepted, outcome.ok, outcome.reason,
        )
        if quantity <= 0:
            success = f"شلت {meal.name} من السلة"
            failure = (
                "ما قدرت أشيلها من السلة لأن التطبيق ما يدعم تعديل الكمية بعد. "
                "قولي للمستخدم يحدّث الصفحة أو يشيلها من الشاشة، وما تدّعين إنك شلتيها."
            )
        else:
            success = f"عدّلت كمية {meal.name} إلى {quantity}"
            failure = (
                "ما قدرت أعدّل الكمية لأن التطبيق ما يدعمها بعد. "
                "قولي للمستخدم يعدّلها من الشاشة، وما تدّعين إنك عدّلتيها."
            )
        result = outcome.as_tool_result(on_success=success, on_failure=failure)
        if not outcome.ok and outcome.reason == "frontend_handler_missing":
            result["frontend_action_required"] = (
                "Deploy luqma.setCartQuantity (see FRONTEND_INTEGRATION.md). "
                "Source may exist but the running Next build must be rebuilt."
            )
            result["say_to_user_ar"] = (
                "ما أقدر أشيلها من هنا الحين، عدّلها أنت من السلة على الشاشة."
            )
        result.update(
            {
                "meal_name": meal.name,
                "meal_name_say_ar": catalog.speakable_name(meal.name),
                "quantity": max(0, quantity),
                "removed": quantity <= 0,
            }
        )
        if outcome.ok:
            if quantity <= 0:
                self._added_this_session.pop(meal.id, None)
            cart = await self._cart_snapshot()
            if cart.get("ok"):
                result["cart"] = cart
                result["subtotal_sar"] = cart["subtotal_sar"]
                result["subtotal_say_ar"] = cart["subtotal_say_ar"]
                result["cart_item_count"] = cart["item_count"]
        return result

    @function_tool()
    async def save_meal(
        self,
        context: RunContext,
        meal_id: str,
        saved: bool = True,
    ) -> dict[str, Any]:
        """
        Save a meal to the user's favourites (the heart button), or remove it.
        Only meals can be saved — restaurants cannot.

        Args:
            meal_id: The meal_id from a previous search result.
            saved: True to save it, False to remove it from favourites.
        """
        cat = catalog.load_catalog()
        meal = cat.by_id(meal_id)
        if meal is None:
            # Distinguish the two ways this goes wrong, because the model's
            # recovery differs: a restaurant id means "you cannot save that",
            # anything else means "you made the id up, search again".
            if cat.restaurant_by_id(meal_id) is not None:
                raise ToolError(
                    f"{meal_id!r} is a restaurant. Only meals can be saved — "
                    "pass a meal_id from a search result."
                )
            raise ToolError(
                f"No meal with id {meal_id!r}. Search first and use a real meal_id."
            )

        saved = bool(saved)
        outcome = await self._ui.set_saved(meal_id=meal.id, saved=saved)
        logger.info(
            "Tool save_meal meal_id={} saved={} ok={} reason={}",
            meal.id, saved, outcome.ok, outcome.reason,
        )

        result = outcome.as_tool_result(
            on_success=(
                f"حفظت {meal.name} في المحفوظات"
                if saved
                else f"شلت {meal.name} من المحفوظات"
            ),
            on_failure=(
                "ما قدرت أحفظها، احفظها من زر القلب في التطبيق"
                if saved
                else "ما قدرت أشيلها من المحفوظات"
            ),
        )
        result.update(
            {
                "meal_name": meal.name,
                "meal_name_say_ar": catalog.speakable_name(meal.name),
                "saved": saved,
            }
        )
        if outcome.ok:
            if saved:
                if meal.id not in self._saved_this_session:
                    self._saved_this_session.append(meal.id)
            else:
                self._saved_this_session = [
                    mid for mid in self._saved_this_session if mid != meal.id
                ]
        return result

    @function_tool()
    async def get_saved(self, context: RunContext) -> dict[str, Any]:
        """
        Read the meals currently in المحفوظات. Call this BEFORE saying the
        list is empty, and BEFORE deleting "everything" in bookmarks. Speak
        names from `items[].name_say_ar` only.
        """
        outcome = await self._ui.get_saved()
        logger.info("Tool get_saved ok={} reason={}", outcome.ok, outcome.reason)
        ids: list[str] = []
        if outcome.ok:
            raw = (outcome.data or {}).get("mealIds") or (outcome.data or {}).get(
                "meal_ids"
            ) or []
            ids = [str(x) for x in raw if x]
        if not ids:
            ids = list(self._saved_this_session)

        loaded = catalog.load_catalog()
        items: list[dict[str, Any]] = []
        for meal_id in ids:
            meal = loaded.by_id(meal_id)
            if meal is None:
                continue
            items.append(
                {
                    "meal_id": meal.id,
                    "name": meal.name,
                    "name_say_ar": catalog.speakable_name(meal.name),
                    "price_say_ar": catalog.sar_words(meal.effective_price),
                }
            )
        empty = not items
        say = (
            "المحفوظات فاضية"
            if empty
            else f"في المحفوظات {catalog.number_to_arabic_words(len(items))} وجبات"
        )
        result: dict[str, Any] = {
            "ok": True if (outcome.ok or items) else outcome.ok,
            "items": items,
            "item_count": len(items),
            "is_empty": empty,
            "rpc_ok": outcome.ok,
            "failure_reason": None if outcome.ok else outcome.reason,
            "say_to_user_ar": say,
            "saved_speech_rule_ar": (
                "انطقي الأسماء من items فقط. ممنوع تقولين فاضية إذا item_count>0."
            ),
        }
        if not outcome.ok and not items:
            result["say_to_user_ar"] = (
                "ما قدرت أقرأ المحفوظات من التطبيق. إذا تشوف وجبات قدامك "
                "قلي اسمها وأشيلها."
            )
        return result

    @function_tool()
    async def clear_saved(self, context: RunContext) -> dict[str, Any]:
        """
        Remove every meal from المحفوظات. Use when the user says
        احذفي اللي في المحفوظات / فضّي المحفوظات.
        """
        listed = await self.get_saved(context)
        ids = [str(i.get("meal_id")) for i in (listed.get("items") or []) if i.get("meal_id")]
        rpc = await self._ui.clear_saved()
        if rpc.ok:
            self._saved_this_session = []
            logger.info("Tool clear_saved rpc ok count={}", len(ids))
            return rpc.as_tool_result(
                on_success="فضّيت المحفوظات",
                on_failure="ما قدرت أفضي المحفوظات من التطبيق",
            )
        # FE has not registered luqma.clearSaved yet — unsaved each id.
        unsaved = 0
        for meal_id in ids:
            meal = catalog.load_catalog().by_id(meal_id)
            if meal is None:
                continue
            one = await self._ui.set_saved(meal_id=meal.id, saved=False)
            if one.ok:
                unsaved += 1
        self._saved_this_session = []
        logger.info(
            "Tool clear_saved fallback unsaved={} rpc_reason={}",
            unsaved, rpc.reason,
        )
        ok = unsaved > 0 or not ids
        return {
            "ok": ok,
            "cleared": unsaved,
            "say_to_user_ar": (
                "فضّيت المحفوظات" if ok else "ما قدرت أفضي المحفوظات من التطبيق"
            ),
        }

    @function_tool()
    async def set_ui_filter(
        self,
        context: RunContext,
        offers_kind: str = "",
        query: str = "",
        restaurants_filter: str = "",
        cuisine: str = "",
        cashback: bool = False,
        menu_tab: str = "",
    ) -> dict[str, Any]:
        """
        Apply an on-screen filter chip so the page matches a tap the user
        could make: offers Combo/Family/Deal, cashback, search query,
        restaurants 24h/fast/grocery, home cuisine, or a restaurant menu tab.
        Also search the catalog with the same filters so speech matches the
        screen.

        Args:
            offers_kind: "combo", "family", "deal", "combo,family", or "all".
            query: Search text to put in the search box (e.g. مقرمش, كيتو).
            restaurants_filter: "all", "grocery", "24h", or "fast".
            cuisine: Cuisine chip if the user named one (شامي, سعودي, …).
            cashback: True to keep / add ?cashback=1 on the offers page.
            menu_tab: Restaurant page tab: "all", "offers", "popular", "menu".
        """
        kind = (offers_kind or "").strip().lower()
        if kind in {"كومبو", "كمبو"}:
            kind = "combo"
        if kind in {"عائلي", "عائله"}:
            kind = "family"
        if kind in {"عرض", "ديل", "deal"}:
            kind = "deal"
        tab = (menu_tab or "").strip().lower()
        if tab in {"عروض", "offers"}:
            tab = "offers"
        elif tab in {"الاكثر طلبا", "الأكثر طلبا", "popular"}:
            tab = "popular"
        elif tab in {"القائمه", "القائمة", "menu"}:
            tab = "menu"
        elif tab in {"الكل", "all"}:
            tab = "all"
        path = ""
        page = ""
        if (kind and kind != "all") or cashback:
            page = "offers"
            path = build_offers_path(
                kind=kind,
                cashback=bool(cashback),
                cuisine=cuisine,
                query=query if kind or cashback else "",
            )
        elif query and not restaurants_filter:
            page = "search"
            path = f"/search?q={query}"
        if restaurants_filter and not page:
            page = "restaurants"
            path = f"/restaurants?filter={restaurants_filter}"
            if cuisine:
                path += f"&cuisine={cuisine}"
        if cuisine and not path:
            page = "home"
            path = f"/?cuisine={cuisine}"
        if tab:
            rid = str((self._last_ui_state or {}).get("restaurantId") or "").strip()
            if rid:
                page = "restaurant"
                path = f"/restaurants/{rid}"
                if tab != "all":
                    path += f"?tab={tab}"

        outcome = await self._ui.set_ui_filter(
            page=page,
            offers_kind=kind,
            query=query,
            restaurants_filter=restaurants_filter,
            cuisine=cuisine,
            cashback=bool(cashback),
            menu_tab=tab,
            path=path,
        )
        logger.info(
            "Tool set_ui_filter kind={!r} query={!r} path={!r} ok={} reason={}",
            kind, query, path, outcome.ok, outcome.reason,
        )
        # Always search so speech is grounded even if the FE RPC is missing.
        search: dict[str, Any] = {}
        if kind and kind != "all":
            search = catalog.search_meals(
                query=query or "",
                only_offers=True,
                offer_kind=kind,
                cashback=True if cashback else None,
            )
        elif cashback:
            search = catalog.search_meals(
                query=query or "",
                cashback=True,
            )
        elif query:
            search = catalog.search_meals(query=query)
        elif restaurants_filter:
            search = {}
        elif cuisine:
            search = catalog.search_meals(query=cuisine, cuisine=cuisine)
        if not outcome.ok and path:
            nav = await self._ui.navigate(path)
            if nav.ok:
                outcome = nav

        result = outcome.as_tool_result(
            on_success="طبّقت الفلتر",
            on_failure=(
                "ما قدرت أغيّر الفلتر في الصفحة، بس هذي نتائج البحث من الكتالوج"
            ),
        )
        if search:
            result["search"] = {
                "total_matches": search.get("total_matches"),
                "results": search.get("results"),
                "say_to_user_ar": search.get("say_to_user_ar"),
            }
        result["offers_kind"] = kind or None
        result["path"] = path or None
        return result

    @function_tool()
    async def get_cart(self, context: RunContext) -> dict[str, Any]:
        """
        Read what is currently in the user's cart. Use before talking about the
        cart contents, total price, or calorie/protein totals, so you never
        guess or add the numbers yourself. Speak calories from
        `calories_total_say_ar` and protein from `protein_total_say_ar`, or
        read `nutrition_say_ar` verbatim.
        """
        outcome = await self._ui.get_cart()
        logger.info("Tool get_cart ok={} reason={}", outcome.ok, outcome.reason)
        result = outcome.as_tool_result(
            on_success="",
            on_failure="ما قدرت أشوف السلة الحين",
        )
        if not outcome.ok:
            return result

        # The RPC only knows meal ids and prices. Resolve names here so سلمى can
        # read the cart back without inventing what is in it.
        lines: list[dict[str, Any]] = []
        subtotal = 0.0
        loaded = catalog.load_catalog()
        for item in (outcome.data or {}).get("items", []) or []:
            meal = loaded.by_id(item.get("mealId", ""))
            qty = int(item.get("quantity") or 1)
            unit = float(item.get("unitPrice") or 0.0)
            subtotal += unit * qty
            line = {
                "meal_id": item.get("mealId"),
                "name": meal.name if meal else item.get("mealId"),
                "name_say_ar": catalog.speakable_name(meal.name) if meal else "",
                "quantity": qty,
                "unit_price_sar": unit,
                "line_total_sar": round(unit * qty, 2),
                "line_total_say_ar": catalog.sar_words(unit * qty),
            }
            if meal:
                line["calories"] = meal.calories
                line["calories_say_ar"] = catalog.number_to_arabic_words(meal.calories)
                line["protein_g"] = meal.protein
                line["protein_say_ar"] = catalog.number_to_arabic_words(meal.protein)
                line["carbs_g"] = meal.carbs
                line["fat_g"] = meal.fat
            raw_addons = item.get("addonIds") or item.get("addon_ids") or []
            addon_ids = [str(a) for a in raw_addons if a] if isinstance(raw_addons, list) else []
            if addon_ids:
                line["addon_ids"] = addon_ids
                if meal:
                    by_id = {str(a.get("id") or ""): a for a in meal.addons}
                    names = [
                        str(by_id[i].get("name") or i)
                        for i in addon_ids
                        if i in by_id
                    ]
                    if names:
                        line["addon_names"] = names
                        line["addon_names_say_ar"] = [
                            catalog.speakable_name(n) for n in names
                        ]
            lines.append(line)

        result["items"] = lines
        result["item_count"] = sum(line["quantity"] for line in lines)
        result["subtotal_sar"] = round(subtotal, 2)
        result["subtotal_say_ar"] = catalog.sar_words(subtotal)
        result["is_empty"] = not lines
        result.update(catalog.cart_nutrition(lines))
        result["cart_speech_rule_ar"] = (
            "انطقي subtotal_say_ar حرف بحرف لمجموع السعر. "
            "انطقي nutrition_say_ar أو calories_total_say_ar حرف بحرف "
            "لسعرات السلة كلها. ممنوع تجمعين أرقام في رأسك، وممنوع تأخذين "
            "سعرات آخر صنف بس. unit_price_sar لكل سطر يشمل الإضافات."
        )
        result["calories_speech_rule_ar"] = (
            f"مجموع السعرات هو {result.get('calories_total_say_ar')}. "
            "انطقي هذا الحقل كما هو."
        )
        return result

    # ─── Misc ────────────────────────────────────────────────────────────────

    @function_tool()
    async def lookup_weather(
        self,
        context: RunContext,
        location: str,
    ) -> dict[str, Any]:
        """
        Look up weather information for a given location.

        Args:
            location: The location to look up weather information for.
        """
        
        # await context.session.say("هشفهولك حالا.")

        async with httpx.AsyncClient(timeout=4.0) as client:

            # ---------------------------
            # 1. Geocoding
            # ---------------------------
            geo_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": location,
                    "count": 1,
                    "language": "en",
                },
            )

            if geo_response.status_code != 200:
                raise ToolError(f"Geocoding API failed for: {location}")

            geo_data = geo_response.json()

            results = geo_data.get("results")
            if not results:
                raise ToolError(f"Could not find location: {location}")

            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            place_name = results[0]["name"]

            # ---------------------------
            # 2. Weather API
            # ---------------------------
            weather_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": ["temperature_2m", "weathercode"],
                    "temperature_unit": "celsius",
                },
            )

            if weather_response.status_code != 200:
                raise ToolError(f"Weather API failed for: {location}")

            weather_data = weather_response.json()

            current = weather_data.get("current", {})

            if not current:
                raise ToolError("No current weather data returned")

        return {
            "location": place_name,
            "summary": f"{current.get('temperature_2m')}°C",
        }


# num_idle_processes defaults to 128 in prod (one warm job process per CPU core,
# assuming a dedicated box). This is a shared server, so cap it — a few warm
# processes keep session start-up instant without spawning 128 procs on boot.
#
# AGENT_PORT is the worker's local health/debug HTTP port. It must be unique per
# running agent, so a second agent on the same host (e.g. this checkout running
# alongside another deployment) needs a free port set in .env.local.
AGENT_PORT = int(os.getenv("AGENT_PORT", "2952"))
server = AgentServer(port=AGENT_PORT, num_idle_processes=3)


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    job = getattr(ctx, "job", None)
    # Room.sid is an async property in the current SDK; touching it without
    # await creates an orphan coroutine and a RuntimeWarning on every session.
    logger.info(
        "LiveKit agent job starting room_name={} job_id={} "
        "tts_server_url={} tts_model={} tts_voice_default={} "
        "tts_seed={} tts_speed={} sentence_min_len={}",
        getattr(ctx.room, "name", None),
        getattr(job, "id", None),
        TTS_SERVER_URL,
        TTS_MODEL,
        TTS_VOICE,
        TTS_SEED,
        TTS_SPEED,
        TTS_SENTENCE_MIN_LEN,
    )

    logger.info(
        "STT VAD configured activation_threshold={} deactivation_threshold={} "
        "min_speech_duration={} min_silence_duration={} prefix_padding_duration={} "
        "foreground_min_rms={} foreground_min_snr_db={} foreground_min_duration={}",
        STT_VAD_ACTIVATION_THRESHOLD,
        STT_VAD_DEACTIVATION_THRESHOLD,
        STT_VAD_MIN_SPEECH_DURATION,
        STT_VAD_MIN_SILENCE_DURATION,
        STT_VAD_PREFIX_PADDING_DURATION,
        STT_FOREGROUND_MIN_RMS,
        STT_FOREGROUND_MIN_SNR_DB,
        STT_FOREGROUND_MIN_DURATION,
    )

    logger.info(
        "Model backends llm_base_url={} llm_model={} llm_api_key_set={} "
        "stt_base_url={} stt_model={} stt_api_key_set={}",
        LLM_BASE_URL,
        LLM_MODEL,
        LLM_API_KEY != "no-key-needed",
        STT_BASE_URL,
        STT_MODEL,
        bool(STT_API_KEY),
    )

    # Voice selection is stamped into participant metadata by /api/connection-details.
    # Resolve the caller first so female (coda-2) vs male (ref-audio clone) is known
    # before the TTS plugin and persona prompt are built.
    _part = None
    try:
        _part = await asyncio.wait_for(ctx.wait_for_participant(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(
            "No remote participant appeared within 15s; defaulting to female voice"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not resolve the calling participant: {}", e)

    # One read of the participant metadata feeds both the voice selection and
    # the user's saved food preferences — the frontend stamps them into the same
    # JSON blob when it mints the token.
    participant_metadata = getattr(_part, "metadata", None) if _part else None

    selected_voice = parse_voice_from_metadata(participant_metadata)
    profile = voice_profile(selected_voice, female_tts_voice=TTS_VOICE)
    logger.info(
        "Voice profile selected key={} name_ar={} tts_voice={} has_ref_audio={} "
        "ref_text_chars={} participant={}",
        profile.key,
        profile.display_name_ar,
        profile.tts_voice,
        bool(profile.ref_audio) or bool(TTS_REF_AUDIO and profile.key == "female"),
        len(profile.ref_text or ""),
        getattr(_part, "identity", None),
    )

    # Saved onboarding answers, rendered as an Arabic block appended to the
    # persona prompt. Empty when the client sent no `prefs` object at all, in
    # which case the agent runs on the bare persona.
    prefs_context = build_prefs_context(participant_metadata)
    instructions = compose_instructions(
        voice=profile.key,
        catalog_index=catalog.catalog_index(),
        prefs_context=prefs_context,
    )
    logger.info(
        "User preferences resolved has_prefs_block={} summary={} block_chars={} "
        "instructions_chars={} participant={}",
        bool(prefs_context),
        summarize_prefs(participant_metadata),
        len(prefs_context),
        len(instructions),
        getattr(_part, "identity", None),
    )
    if prefs_context:
        logger.info("User preferences block injected: {}", _preview(prefs_context, 400))

    voice_tts = VoxCPMTTSPlugin(
        server_url=TTS_SERVER_URL,
        model=TTS_MODEL,
        voice=profile.tts_voice,
        ref_audio=(
            TTS_REF_AUDIO
            if profile.key == "female" and TTS_REF_AUDIO
            else profile.ref_audio
        ),
        ref_text=profile.ref_text,
        seed=TTS_SEED,
        speed=TTS_SPEED,
        expected_voice=profile.expected_voice,
        voice_min_f0=profile.voice_min_f0,
        voice_max_f0=profile.voice_max_f0,
        # Enforces the tashkeel rules the prompt cannot hold, then respells the
        # words the voice reads wrong. See catalog.clean_for_speech.
        text_filter=catalog.clean_for_speech,
    )

    voice_vad = silero.VAD.load(
        activation_threshold=STT_VAD_ACTIVATION_THRESHOLD,
        deactivation_threshold=STT_VAD_DEACTIVATION_THRESHOLD,
        min_speech_duration=STT_VAD_MIN_SPEECH_DURATION,
        min_silence_duration=STT_VAD_MIN_SILENCE_DURATION,
        prefix_padding_duration=STT_VAD_PREFIX_PADDING_DURATION,
    )

    def _noise_cancellation_for(params: Any) -> Any:
        participant_obj = getattr(params, "participant", None)
        participant_kind = getattr(participant_obj, "kind", None)
        participant_identity = getattr(participant_obj, "identity", None)
        use_telephony = participant_kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        logger.info(
            "LiveKit audio input noise cancellation selected participant={} kind={} mode={}",
            participant_identity,
            participant_kind,
            "BVCTelephony" if use_telephony else "BVC",
        )
        return noise_cancellation.BVCTelephony() if use_telephony else noise_cancellation.BVC()

    session = AgentSession(
        stt=livekit_stt.StreamAdapter(
            stt=CohereASRStreamingSTT(
                base_url=STT_BASE_URL,
                model=STT_MODEL,
                api_key=STT_API_KEY,
                min_foreground_rms=STT_FOREGROUND_MIN_RMS,
                min_foreground_snr_db=STT_FOREGROUND_MIN_SNR_DB,
                min_foreground_duration=STT_FOREGROUND_MIN_DURATION,
            ),
            vad=voice_vad,
        ),
        llm=openai.LLM(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            api_key=LLM_API_KEY,
        ),
        # The TTS re-rolls the speaker slightly on every request, so synthesizing
        # sentence-by-sentence makes the voice audibly shift mid-reply. The
        # StreamAdapter below batches the reply into ~300+ char chunks so a
        # typical turn is one continuous generation (which holds the voice
        # steady), at the cost of TTS starting after the LLM finishes the turn.
        tts=StreamAdapter(
            tts=voice_tts,
            sentence_tokenizer=tokenize.basic.SentenceTokenizer(
                min_sentence_len=TTS_SENTENCE_MIN_LEN
            ),
        ),
        vad=voice_vad,
        turn_detection=MultilingualModel(),
        # Default is 15s of silence → user_away, which drops the call. Testers
        # idle ~15 minutes (Bug-006). Keep the agent listening; the frontend
        # token TTL is a separate limit the FE team must raise.
        user_away_timeout=45 * 60,
    )


    last_eou_metrics = None
    collected_metrics = []
    speech_created_count = 0

    @session.on("speech_created")
    def _on_speech_created(event: SpeechCreatedEvent):
        nonlocal speech_created_count
        speech_created_count += 1
        handle = event.speech_handle
        logger.info(
            "LiveKit speech created count={} speech_id={} source={} user_initiated={} "
            "allow_interruptions={} input_modality={} num_steps={} scheduled={} interrupted={}",
            speech_created_count,
            getattr(handle, "id", None),
            event.source,
            event.user_initiated,
            getattr(handle, "allow_interruptions", None),
            getattr(getattr(handle, "input_details", None), "modality", None),
            getattr(handle, "num_steps", None),
            getattr(handle, "scheduled", None),
            getattr(handle, "interrupted", None),
        )

        def _on_speech_done(done_handle):
            logger.info(
                "LiveKit speech done speech_id={} interrupted={} num_steps={} chat_items={}",
                getattr(done_handle, "id", None),
                getattr(done_handle, "interrupted", None),
                getattr(done_handle, "num_steps", None),
                len(getattr(done_handle, "chat_items", []) or []),
            )

        try:
            handle.add_done_callback(_on_speech_done)
        except Exception as e:
            logger.warning("Could not attach speech done callback: {}", e)

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(event: UserInputTranscribedEvent):
        logger.info(
            "LiveKit user input transcribed final={} speaker_id={} language={} chars={} transcript={!r}",
            event.is_final,
            event.speaker_id,
            event.language,
            len(event.transcript or ""),
            _preview(event.transcript),
        )

    @session.on("user_state_changed")
    def _on_user_state_changed(event: UserStateChangedEvent):
        logger.info(
            "LiveKit user state changed old_state={} new_state={} created_at={}",
            event.old_state,
            event.new_state,
            event.created_at,
        )

    @session.on("conversation_item_added")
    def _on_conversation_item_added(event: ConversationItemAddedEvent):
        item = event.item
        role = getattr(item, "role", None)
        item_type = type(item).__name__
        content = getattr(item, "content", None)
        logger.info(
            "LiveKit conversation item added type={} role={} content_preview={!r}",
            item_type,
            role,
            _preview(content if content is not None else item),
        )

    @session.on("function_tools_executed")
    def _on_function_tools_executed(event: FunctionToolsExecutedEvent):
        call_names = [getattr(call, "name", None) for call in event.function_calls]
        output_types = [
            type(output).__name__ if output is not None else None
            for output in event.function_call_outputs
        ]
        logger.info(
            "LiveKit function tools executed calls={} output_types={} has_tool_reply={} has_agent_handoff={}",
            call_names,
            output_types,
            event.has_tool_reply,
            event.has_agent_handoff,
        )

    @session.on("session_usage_updated")
    def _on_session_usage_updated(event: SessionUsageUpdatedEvent):
        logger.info("LiveKit session usage updated: {}", event.usage)

    @session.on("error")
    def _on_error(event: ErrorEvent):
        logger.error(
            "LiveKit session error source={} error={}",
            type(event.source).__name__,
            event.error,
        )

    @session.on("metrics_collected")
    def _on_metrics_collected(event: MetricsCollectedEvent):
        nonlocal last_eou_metrics

        if getattr(event.metrics, "type", None) == "eou_metrics":
            last_eou_metrics = event.metrics

        collected_metrics.append(event.metrics)

        logger.info(
            "LiveKit metrics collected type={} timestamp={} details={}",
            getattr(event.metrics, "type", None),
            getattr(event.metrics, "timestamp", None),
            event.metrics,
        )

    async def log_usage():
        logger.info("Session ended.")
        logger.info(f"Total metrics events collected: {len(collected_metrics)}")

        for metric in collected_metrics:
            logger.info(metric)

    ctx.add_shutdown_callback(log_usage)

    @session.on("agent_state_changed")
    def _on_agent_state_changed(event: AgentStateChangedEvent):
        logger.info(
            "LiveKit agent state changed old_state={} new_state={} created_at={}",
            event.old_state,
            event.new_state,
            event.created_at,
        )

        if event.new_state == "speaking" and last_eou_metrics:
            try:
                elapsed_time = time.time() - last_eou_metrics.timestamp
                logger.info(
                    f"Time since last EOU: {elapsed_time:.2f} seconds"
                )
            except Exception as e:
                logger.warning(f"Could not calculate EOU latency: {e}")

    # UI control targets the participant that is actually on the call. Without
    # one (the 15s wait above timed out) the bridge reports failure per call
    # rather than silently no-oping, so سلمى never claims it opened a page.
    ui_bridge = UIBridge(room=ctx.room, identity=getattr(_part, "identity", None))
    logger.info(
        "UI bridge ready available={} target_identity={}",
        ui_bridge.available,
        getattr(_part, "identity", None),
    )

    logger.info("LiveKit session.start beginning room_name={}", getattr(ctx.room, "name", None))
    await session.start(
        room=ctx.room,
        agent=Assistant(instructions=instructions, ui=ui_bridge),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=_noise_cancellation_for
            ),
        ),
    )
    logger.info("LiveKit session.start completed room_name={}", getattr(ctx.room, "name", None))

    logger.info(
        "LiveKit initial generate_reply beginning voice={} name_ar={}",
        profile.key,
        profile.display_name_ar,
    )
    await session.generate_reply(
        instructions=intro_for_voice(profile.key)
    )
    logger.info("LiveKit initial generate_reply completed")


if __name__ == "__main__":
    agents.cli.run_app(server)




    
