"""Entrypoint for the Alawaly Real Estate voice agent (المساعد الذكي لشركة العوالي العقارية).

    python agent.py download-files   # fetch the VAD and turn-detector models
    python agent.py start            # production worker
    python agent.py dev              # development worker
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from src.config import CONFIG

logger.remove()
logger.add(sys.stderr, level=CONFIG.log_level, backtrace=True, diagnose=False)


class _DropClosedSignal(logging.Filter):
    """The Rust client logs this after the caller hangs up and the signal socket is already gone."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "no stream available" not in record.getMessage()


logging.getLogger("livekit").addFilter(_DropClosedSignal())


from livekit import agents, rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AgentServer,
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    UserInputTranscribedEvent,
    inference,
    room_io,
)
from livekit.agents import stt as livekit_stt  # noqa: E402
from livekit.agents.tts import StreamAdapter  # noqa: E402
from livekit.plugins import noise_cancellation, openai, silero  # noqa: E402

from src import catalog, flow_say  # noqa: E402
from src.llm_backend import resolve_backend  # noqa: E402
from src.plugins.cohere_asr_stt import CohereASRSTT  # noqa: E402
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin  # noqa: E402
from src.prefs import build_prefs_context, summarize_prefs  # noqa: E402
from src.prompts.agent_prompt import compose_instructions  # noqa: E402
from src.session_state import SessionState  # noqa: E402
from src.speech_streaming import ConcatPhraseTokenizer, WholeReplyTokenizer  # noqa: E402
from src.ui_bridge import UIBridge  # noqa: E402
from src.voice_agent.assistant import Assistant  # noqa: E402
from src.voice_agent.base import Shop  # noqa: E402
from src.voice_profile import parse_voice_from_metadata, voice_profile  # noqa: E402

LLM_BACKEND = resolve_backend()

# Load the catalog at import so a broken file shows in the boot log, and check
# it still matches the app's copy.
try:
    catalog.load_catalog()
    catalog.check_catalog_sync()
except Exception as e:  # noqa: BLE001 - a voice agent that can still talk beats one that will not start
    logger.error("Catalog failed to load at startup; search will fail until fixed: {}", e)


class _TurnDecisionLog(logging.Handler):
    """Bring LiveKit's end-of-turn decisions into our log at INFO.

    LiveKit logs the turn detector's probability and each commit at DEBUG
    only; without them a caller cut off mid-sentence cannot be diagnosed.
    """

    _WANTED = {"eot prediction", "user turn committed"}

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() not in self._WANTED:
            return
        fields = {
            k: (round(v, 3) if isinstance(v, float) else v)
            for k, v in record.__dict__.items()
            if k in {"probability", "unlikely_threshold", "endpointing_delay", "language", "trigger", "source",
                     "end_of_turn_probability", "delay_completed"}
        }
        logger.info("Turn {} {}", record.getMessage(), fields)


def _log_turn_decisions() -> None:
    lk = logging.getLogger("livekit.agents")
    if any(isinstance(h, _TurnDecisionLog) for h in lk.handlers):
        return
    # The existing handlers keep their own level, so this adds no DEBUG noise.
    for handler in logging.getLogger().handlers + lk.handlers:
        if handler.level == logging.NOTSET:
            handler.setLevel(lk.getEffectiveLevel())
    lk.addHandler(_TurnDecisionLog(level=logging.DEBUG))
    lk.setLevel(logging.DEBUG)


def prewarm(proc: agents.JobProcess) -> None:
    """Load the VAD once per worker process, not once per call."""
    v = CONFIG.voice
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=v.vad_activation_threshold,
        deactivation_threshold=v.vad_deactivation_threshold,
        min_speech_duration=v.vad_min_speech_duration,
        min_silence_duration=v.vad_min_silence_duration,
        prefix_padding_duration=v.vad_prefix_padding_duration,
    )


server = AgentServer(port=CONFIG.agent_port, num_idle_processes=CONFIG.num_idle_processes)
server.setup_fnc = prewarm


def build_session(vad: Any, tts: Any) -> AgentSession:
    v, e = CONFIG.voice, CONFIG.endpoints
    return AgentSession(
        # The Coda ASR transcribes whole segments, so the VAD cuts them.
        stt=livekit_stt.StreamAdapter(
            stt=CohereASRSTT(
                base_url=e.stt_base_url,
                model=e.stt_model,
                api_key=e.stt_api_key,
                foreground_gate=v.foreground_gate,
                min_foreground_rms=v.foreground_min_rms,
                min_foreground_snr_db=v.foreground_min_snr_db,
                min_foreground_duration=v.foreground_min_duration,
            ),
            vad=vad,
        ),
        llm=openai.LLM(**LLM_BACKEND.openai_kwargs()),
        tts=StreamAdapter(
            tts=tts,
            sentence_tokenizer=ConcatPhraseTokenizer() if e.tts_phrase_streaming else WholeReplyTokenizer(),
        ),
        vad=vad,
        turn_handling={
            # Audio end-of-turn model; supports Arabic (the text model did not).
            "turn_detection": inference.TurnDetector(version=v.turn_detector_version),
            "endpointing": {"min_delay": v.endpointing_min_delay, "max_delay": v.endpointing_max_delay},
            "preemptive_generation": {"enabled": v.preemptive_generation},
        },
        max_tool_steps=v.max_tool_steps,
        user_away_timeout=v.user_away_timeout,
    )


REJOIN_GRACE_SECONDS = 20


def _hold_session_after_leave(ctx: agents.JobContext, session: AgentSession, caller_identity: str | None) -> None:
    """If the caller drops, keep the room for 20 seconds so they can rejoin."""
    hold: dict[str, asyncio.Task[None] | None] = {"task": None}

    async def _close_if_still_gone() -> None:
        logger.info("Caller left; waiting {}s before closing the session", REJOIN_GRACE_SECONDS)
        try:
            await asyncio.sleep(REJOIN_GRACE_SECONDS)
        except asyncio.CancelledError:
            logger.info("Caller rejoined; keeping the session")
            return
        if ctx.room.remote_participants:
            logger.info("Caller is back in the room; keeping the session")
            return
        logger.info("Caller did not return within {}s; closing the session", REJOIN_GRACE_SECONDS)
        session.shutdown(drain=False)

    def _on_disconnected(participant: rtc.RemoteParticipant) -> None:
        if caller_identity and participant.identity != caller_identity:
            return
        task = hold["task"]
        if task is not None and not task.done():
            return
        hold["task"] = asyncio.create_task(_close_if_still_gone())

    def _on_connected(participant: rtc.RemoteParticipant) -> None:
        if caller_identity and participant.identity != caller_identity:
            return
        task = hold["task"]
        if task is not None and not task.done():
            task.cancel()
        hold["task"] = None

    ctx.room.on("participant_disconnected", _on_disconnected)
    ctx.room.on("participant_connected", _on_connected)


def _noise_cancellation_for(params: Any) -> Any:
    participant = getattr(params, "participant", None)
    telephony = getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    return noise_cancellation.BVCTelephony() if telephony else noise_cancellation.BVC()


def _attach_logging(session: AgentSession, shop: Shop) -> None:
    latency = shop.latency

    @session.on("user_input_transcribed")
    def _on_transcribed(event: UserInputTranscribedEvent) -> None:
        if event.is_final:
            latency.begin(chars=len(event.transcript or ""))
            logger.info("Heard final={} language={} text={!r}", event.is_final, event.language, (event.transcript or "")[:220])

    @session.on("function_tools_executed")
    def _on_tools(event: FunctionToolsExecutedEvent) -> None:
        logger.info("Tools executed calls={} reply={}", [c.name for c in event.function_calls], event.has_tool_reply)

    @session.on("error")
    def _on_error(event: ErrorEvent) -> None:
        logger.error("Session error source={} error={}", type(event.source).__name__, event.error)

    @session.on("conversation_item_added")
    def _on_conversation_item(event: ConversationItemAddedEvent) -> None:
        """Per-turn latency now lives on the chat message, not metrics_collected."""
        item = event.item
        metrics = getattr(item, "metrics", None) or {}
        if not isinstance(metrics, dict):
            return
        role = getattr(item, "role", None)
        try:
            if role == "user":
                latency.note_eou(
                    delay=metrics.get("end_of_turn_delay"),
                    transcription=metrics.get("transcription_delay"),
                )
            elif role == "assistant":
                latency.note_ttft(metrics.get("llm_node_ttft"))
                text = getattr(item, "text_content", None) or ""
                latency.note_tts(ttfb=metrics.get("tts_node_ttfb"), chars=len(text))
        except Exception as exc:  # noqa: BLE001 - diagnostics must not end a call
            logger.warning("Latency bookkeeping failed role={} error={}", role, exc)

    @session.on("agent_state_changed")
    def _on_state(event: AgentStateChangedEvent) -> None:
        logger.info("Agent state {} -> {}", event.old_state, event.new_state)
        if event.new_state == "speaking":
            try:
                latency.report()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Latency report failed: {}", exc)


@server.rtc_session(agent_name=CONFIG.agent_name)
async def my_agent(ctx: agents.JobContext) -> None:
    e = CONFIG.endpoints
    _log_turn_decisions()
    logger.info(
        "Call starting room={} llm={} model={} stt={} tts={} turn_detector={}",
        getattr(ctx.room, "name", None), LLM_BACKEND.provider, LLM_BACKEND.model, e.stt_model, e.tts_model,
        CONFIG.voice.turn_detector_version,
    )
    participant = None
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning("No caller joined within 15s; continuing without screen control")
    metadata = getattr(participant, "metadata", None) if participant else None

    profile = voice_profile(parse_voice_from_metadata(metadata), female_tts_voice=e.tts_voice)
    prefs_context = build_prefs_context(metadata)
    instructions = compose_instructions(voice=profile.key, catalog_index=catalog.catalog_index(), prefs_context=prefs_context)
    logger.info("Prompt ready chars={} prefs={}", len(instructions), summarize_prefs(metadata))

    use_ref = profile.key == "female" and e.tts_ref_audio
    tts = VoxCPMTTSPlugin(
        server_url=e.tts_server_url,
        api_key=e.tts_api_key,
        model=e.tts_model,
        voice=profile.tts_voice,
        ref_audio=e.tts_ref_audio if use_ref else profile.ref_audio,
        ref_text=e.tts_ref_text if use_ref else profile.ref_text,
        style=e.tts_style,
        phrase_streaming=e.tts_phrase_streaming,
        speed=e.tts_speed,
        gain_db=e.tts_gain_db,
        expected_voice=profile.expected_voice,
        voice_min_f0=profile.voice_min_f0,
        voice_max_f0=profile.voice_max_f0,
        text_filter=catalog.clean_for_speech,
    )
    vad = ctx.proc.userdata.get("vad")
    if vad is None:  # a process started without prewarm (tests, console)
        prewarm(ctx.proc)
        vad = ctx.proc.userdata["vad"]
    session = build_session(vad, tts)

    hour = datetime.now(ZoneInfo(CONFIG.rules.timezone)).hour
    shop = Shop(
        ui=UIBridge(room=ctx.room, identity=getattr(participant, "identity", None)),
        state=SessionState(),
        hour=hour,
    )
    _attach_logging(session, shop)
    assistant = Assistant(instructions=instructions, shop=shop)

    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=_noise_cancellation_for),
            close_on_disconnect=False,
        ),
    )
    # Speak before any other work. The first sentence is short so TTS starts immediately.
    session.say(flow_say.GREETING_SAY_AR, allow_interruptions=False)
    _hold_session_after_leave(ctx, session, getattr(participant, "identity", None))


if __name__ == "__main__":
    agents.cli.run_app(server)
