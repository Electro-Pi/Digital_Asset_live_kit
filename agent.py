from dotenv import load_dotenv
from typing import Any, Optional

import asyncio
import os
import time
from loguru import logger

from livekit import agents, rtc
from livekit.agents import llm as livekit_llm
from livekit.agents import stt as livekit_stt
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobProcess,
    room_io,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    MetricsCollectedEvent,
    SessionUsageUpdatedEvent,
    SpeechCreatedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    tokenize,
)
from livekit.agents.tts import StreamAdapter
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.moss_tts_plugin import MOSSTTSPlugin
from src.prompts.medical_prompt import greeting_instructions, system_prompt
from src.billing import billing_from_env, parse_participant_budget
from src.rag import MedicalRetriever, QueryRoute, TurnDecision, settings_from_env
from src.rag.store import verify_corpus_sync


load_dotenv(".env.local")

RAG_SETTINGS = settings_from_env()

MOSS_TTS_SERVER_URL = os.getenv("MOSS_TTS_SERVER_URL", "http://localhost:8088")
MOSS_TTS_MODEL = os.getenv("MOSS_TTS_MODEL", "Rabe3/saudi-tts-4")
MOSS_TTS_VOICE = os.getenv("MOSS_TTS_VOICE", "coda-2")
# The voice this deployment is supposed to be speaking with. Hosted servers that
# have no uploaded voices only accept "default" (the finetuned speaker), while
# the production server has "coda-2" uploaded — so this is config, not a constant.
MOSS_TTS_EXPECTED_VOICE = os.getenv("MOSS_TTS_EXPECTED_VOICE", MOSS_TTS_VOICE)
# Prefer the TTS server's uploaded ``coda-2`` voice. A reference path is only
# valid when that exact file exists inside the TTS server's filesystem.
MOSS_TTS_REF_AUDIO = os.getenv("MOSS_TTS_REF_AUDIO", "").strip() or None
MOSS_TTS_SEED = int(os.getenv("MOSS_TTS_SEED", "1234"))
MOSS_TTS_TEXT_TEMPERATURE = float(os.getenv("MOSS_TTS_TEXT_TEMPERATURE", "1.0"))
MOSS_TTS_TEXT_TOP_P = float(os.getenv("MOSS_TTS_TEXT_TOP_P", "1.0"))
MOSS_TTS_TEXT_TOP_K = int(os.getenv("MOSS_TTS_TEXT_TOP_K", "50"))
MOSS_TTS_AUDIO_TEMPERATURE = float(os.getenv("MOSS_TTS_AUDIO_TEMPERATURE", "0.8"))
MOSS_TTS_AUDIO_TOP_P = float(os.getenv("MOSS_TTS_AUDIO_TOP_P", "0.95"))
MOSS_TTS_AUDIO_TOP_K = int(os.getenv("MOSS_TTS_AUDIO_TOP_K", "50"))
# Delivery pace, applied client-side and pitch-preserving. Below 1.0 is slower.
# Not the server's `speed`, which is a resampler and would change the speaker.
MOSS_TTS_SPEED = float(os.getenv("MOSS_TTS_SPEED", "0.92"))
MOSS_TTS_SENTENCE_MIN_LEN = int(os.getenv("MOSS_TTS_SENTENCE_MIN_LEN", "300"))

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


def _preview(value: Any, limit: int = 220) -> str:
    """Render a value for logs without leaking what the patient said.

    Everything a patient says in this session is health information. Logs on a
    deployed box are not a place for it, so previews collapse to a length by
    default; LOG_SENSITIVE_CONTENT re-enables the real text for local debugging
    only.
    """
    text = " ".join(str(value).split())
    if not RAG_SETTINGS.log_sensitive_content:
        return f"[redacted; chars={len(text)}]"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _error_summary(error: Any) -> str:
    """Describe a provider error without echoing the request body back.

    Provider exceptions frequently embed the prompt — which here contains the
    patient's own words — in their string form.
    """
    if RAG_SETTINGS.log_sensitive_content:
        return str(error)
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return f"{type(error).__name__}(status={status}; details redacted)"


# Per-turn cap on the conversational context handed to the router. Enough to
# carry the clinical topic of the preceding exchange, short enough that it does
# not swamp the patient's actual words in the retrieval query.
_HISTORY_TURN_CHARS = 200


class MedicalAssistant(Agent):
    """The patient-facing agent: screen, retrieve, then answer from evidence."""

    def __init__(self, retriever: MedicalRetriever) -> None:
        super().__init__(instructions=system_prompt)
        self._retriever = retriever

    @staticmethod
    def _recent_history(turn_ctx: livekit_llm.ChatContext) -> Optional[str]:
        """The tail of the conversation, used to make follow-ups interpretable.

        "وكم مرة؟" retrieves nothing on its own; paired with what it follows, it
        retrieves the right screening interval.

        Both roles are included, because a follow-up is usually the answer to
        the clarifying question Nouf just asked, and it is her
        question — not the patient's previous complaint — that gives it meaning.
        Reading only the patient's side, "ايه فيه احمرار" looks like small talk
        and gets routed DIRECT, which is how a red, hot joint reached the model
        with no guideline evidence behind it.
        """
        parts: list[str] = []
        for item in reversed(turn_ctx.items):
            if getattr(item, "role", None) not in ("user", "assistant"):
                continue
            text = (getattr(item, "text_content", None) or "").strip()
            if not text:
                continue
            parts.append(text[:_HISTORY_TURN_CHARS])
            if len(parts) == 2:
                break
        return " ".join(reversed(parts)) if parts else None

    async def on_user_turn_completed(
        self,
        turn_ctx: livekit_llm.ChatContext,
        new_message: livekit_llm.ChatMessage,
    ) -> None:
        utterance = (new_message.text_content or "").strip()
        if not utterance:
            return

        decision: TurnDecision = await self._retriever.decide(
            utterance, history=self._recent_history(turn_ctx)
        )

        if decision.evidence:
            # Injected into this turn's context only — LiveKit does not persist
            # changes made here, so the evidence never accumulates across turns.
            turn_ctx.add_message(role="system", content=decision.evidence)

        logger.info(
            "Turn prepared route={} evidence_count={} degraded={} latency_ms={:.0f}",
            decision.route.value,
            len(decision.hits),
            decision.degraded,
            decision.latency_ms,
        )


# num_idle_processes defaults to 128 in prod (one warm job process per CPU core,
# assuming a dedicated box). This is a shared server, so cap it — a few warm
# processes keep session start-up instant without spawning 128 procs on boot.
#
# AGENT_PORT is the worker's local health/debug HTTP port. It must be unique per
# running agent, so a second agent on the same host (e.g. this checkout running
# alongside another deployment) needs a free port set in .env.local.
AGENT_PORT = int(os.getenv("AGENT_PORT", "2952"))

# Explicit-dispatch worker name. This LiveKit project is shared with sibling
# checkouts, and the frontend mints every room with a RoomConfiguration naming
# this agent. Registering unnamed puts the worker in the automatic pool instead,
# which has two failure modes that both matter here: rooms asking for
# "medical-coda" get no agent at all, and this medical agent can be dispatched
# into another checkout's rooms. Must match LIVEKIT_AGENT_NAME in the frontend.
AGENT_NAME = os.getenv("AGENT_NAME", "").strip()


def _verify_medical_corpus(proc: JobProcess) -> None:
    """Refuse to bring up a worker that cannot reach its guideline corpus.

    Runs once per job process. An agent with an unreachable or empty index
    would answer every clinical question ungrounded, which the PoC's
    knowledge-base constraint rules out — so this failing is the correct
    outcome, not something to degrade past.
    """
    total = verify_corpus_sync(RAG_SETTINGS)
    logger.info(
        "Medical corpus verified collection={} chunks={} qdrant={}",
        RAG_SETTINGS.collection_alias,
        total,
        RAG_SETTINGS.qdrant_url,
    )


server = AgentServer(
    port=AGENT_PORT,
    num_idle_processes=3,
    setup_fnc=_verify_medical_corpus,
)

# One retriever per job process, shared across the sessions that process
# handles. Building it opens a Qdrant connection and a Gemini client, neither of
# which is worth redoing per call.
_retriever: Optional[MedicalRetriever] = None
_retriever_lock = asyncio.Lock()


async def _get_retriever() -> MedicalRetriever:
    global _retriever
    async with _retriever_lock:
        if _retriever is None:
            _retriever = await MedicalRetriever.create(RAG_SETTINGS)
        return _retriever

# Authoritative per-user live-minutes meter. Talks to Supabase with the
# service-role key (server-only). No-op if Supabase env is unset — in that case
# only the LiveKit token TTL caps the session.
billing = billing_from_env()


@server.rtc_session(agent_name=AGENT_NAME)
async def my_agent(ctx: agents.JobContext):
    job = getattr(ctx, "job", None)
    participant = getattr(ctx, "participant", None)
    logger.info(
        "LiveKit agent job starting room_name={} room_sid={} job_id={} participant={} "
        "tts_server_url={} tts_model={} tts_voice={} tts_ref_audio={} "
        "tts_seed={} tts_text_temperature={} tts_text_top_p={} tts_text_top_k={} "
        "tts_audio_temperature={} tts_audio_top_p={} tts_audio_top_k={} tts_speed={} "
        "sentence_min_len={}",
        getattr(ctx.room, "name", None),
        getattr(ctx.room, "sid", None),
        getattr(job, "id", None),
        getattr(participant, "identity", None),
        MOSS_TTS_SERVER_URL,
        MOSS_TTS_MODEL,
        MOSS_TTS_VOICE,
        MOSS_TTS_REF_AUDIO,
        MOSS_TTS_SEED,
        MOSS_TTS_TEXT_TEMPERATURE,
        MOSS_TTS_TEXT_TOP_P,
        MOSS_TTS_TEXT_TOP_K,
        MOSS_TTS_AUDIO_TEMPERATURE,
        MOSS_TTS_AUDIO_TOP_P,
        MOSS_TTS_AUDIO_TOP_K,
        MOSS_TTS_SPEED,
        MOSS_TTS_SENTENCE_MIN_LEN,
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

    if MOSS_TTS_VOICE.lower() != MOSS_TTS_EXPECTED_VOICE.lower():
        logger.error(
            "Configured MOSS_TTS_VOICE is {} but MOSS_TTS_EXPECTED_VOICE is {}. "
            "This can directly cause voice changes.",
            MOSS_TTS_VOICE,
            MOSS_TTS_EXPECTED_VOICE,
        )
    if MOSS_TTS_SERVER_URL.endswith(":8088"):
        logger.info(
            "MOSS_TTS_SERVER_URL points to raw SGLang on 8088. This run uses one "
            "fixed-seed request only; no gateway, no retry, no playback gate."
        )

    moss_tts = MOSSTTSPlugin(
        server_url=MOSS_TTS_SERVER_URL,
        model=MOSS_TTS_MODEL,
        voice=MOSS_TTS_VOICE,
        ref_audio=MOSS_TTS_REF_AUDIO,
        seed=MOSS_TTS_SEED,
        text_temperature=MOSS_TTS_TEXT_TEMPERATURE,
        text_top_p=MOSS_TTS_TEXT_TOP_P,
        text_top_k=MOSS_TTS_TEXT_TOP_K,
        audio_temperature=MOSS_TTS_AUDIO_TEMPERATURE,
        audio_top_p=MOSS_TTS_AUDIO_TOP_P,
        audio_top_k=MOSS_TTS_AUDIO_TOP_K,
        speed=MOSS_TTS_SPEED,
        expected_voice=MOSS_TTS_EXPECTED_VOICE,
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
        # stt=groq.STT(
        #     model="whisper-large-v3-turbo",
        #     language="ar",
        # ),
        # stt=openai.STT(
        #     base_url="http://8.213.50.155/v1",
        #     model="qwen3-asr",
        #     api_key="no-key-needed",
        #     language="ar",
        # ),
        # Gate the Arabic-only Cohere ASR with neural voice detection so only
        # confirmed speech segments reach the transcription model.
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
        # llm=groq.LLM(model="openai/gpt-oss-20b"),
        llm=openai.LLM(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            api_key=LLM_API_KEY,
        ),
        # tts=openai.TTS(
        #     base_url="http://211.21.50.85:57010/v1",
        #     model="orpheus",
        #     voice="tara",
        #     api_key="no-key-needed",
        # ),
        # MOSS re-rolls the speaker slightly on every request, so synthesizing
        # sentence-by-sentence makes the voice audibly shift mid-reply. The
        # StreamAdapter below batches the reply into ~300+ char chunks so a
        # typical turn is one continuous generation (which holds the voice
        # steady), at the cost of TTS starting after the LLM finishes the turn.
        tts=StreamAdapter(
            tts=moss_tts,
            sentence_tokenizer=tokenize.basic.SentenceTokenizer(
                min_sentence_len=MOSS_TTS_SENTENCE_MIN_LEN
            ),
        ),
        # tts=groq.TTS(model="canopylabs/orpheus-arabic-saudi", voice="fahad"),
        # tts=F5TTSPlugin(
        #     server_url="http://45.81.32.2:11096/",
        #     model_name="f5_tts",
        #     reference_audio_path="speech.wav",
        #     reference_text="جصتي تبدا من ايام ما كنا نزور بيت خوالي",
        #     sample_rate=24000,
        # ),
        vad=voice_vad,
        turn_detection=MultilingualModel(),
        # preemptive_generation=True
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

    outage_notice_sent = False

    @session.on("error")
    def _on_error(event: ErrorEvent):
        nonlocal outage_notice_sent
        logger.error(
            "LiveKit session error source={} error={}",
            type(event.source).__name__,
            _error_summary(event.error),
        )
        # Once per session, at most. LiveKit raises this for recoverable faults
        # too — a run of TTS retries produced thirteen of these in two minutes,
        # and every one of them spoke over the patient.
        if outage_notice_sent:
            return
        outage_notice_sent = True
        asyncio.create_task(_deliver_outage_fallback())

    async def _deliver_outage_fallback() -> None:
        # Carries no referral of any kind: the assistant no longer sends
        # patients anywhere, and a synthesis or transport failure says nothing
        # about how sick they are anyway.
        message = (
            "أعتذر، صار عندي خلل تقني بسيط. ممكن تعيد آخر شي قلته؟"
        )
        try:
            await session.say(message, allow_interruptions=True)
        except Exception as say_err:  # noqa: BLE001
            logger.warning("Outage fallback message failed: {}", _error_summary(say_err))

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

    retriever = await _get_retriever()
    assistant = MedicalAssistant(retriever)

    # Keep the embedding connection alive across the greeting, so the patient's
    # first question does not pay reconnection out of its retrieval budget.
    _keepalive_task = asyncio.create_task(retriever.run_keepalive())

    async def _stop_keepalive() -> None:
        _keepalive_task.cancel()

    ctx.add_shutdown_callback(_stop_keepalive)

    logger.info("LiveKit session.start beginning room_name={}", getattr(ctx.room, "name", None))
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=_noise_cancellation_for
            ),
        ),
    )
    logger.info("LiveKit session.start completed room_name={}", getattr(ctx.room, "name", None))

    # ─── Per-user live-minutes budget (authoritative meter) ──────────────────
    # The /api/connection-details token route stamped the Supabase user id +
    # remaining seconds into the participant identity/metadata. We read it here,
    # hard-stop the session when the budget is spent (with a spoken notice), and
    # bill the actual consumed seconds back to Supabase. A periodic checkpoint
    # means a crash mid-session still records most of the usage. The token TTL is
    # an independent hard ceiling layered on top of this.
    # JobContext exposes no `participant` attribute, and room.remote_participants
    # is still empty this early in the job — so both of those lookups always
    # resolved to None, which silently disabled live billing AND the hard stop
    # (leaving only the token TTL). Wait for the caller to actually appear.
    _part = None
    try:
        _part = await asyncio.wait_for(ctx.wait_for_participant(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(
            "No remote participant appeared within 15s; live billing is disabled "
            "for this session and only the token TTL caps it"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not resolve the calling participant for billing: {}", e)

    billing_user_id, billing_remaining = parse_participant_budget(
        getattr(_part, "identity", None), getattr(_part, "metadata", None)
    )
    logger.info(
        "Live budget resolved user={} remaining={}s billing_enabled={}",
        billing_user_id,
        billing_remaining,
        billing.enabled,
    )

    # The remaining-seconds hint in the token is a SNAPSHOT taken when the token
    # was minted, so it cannot see budget spent by a session that started after
    # it — two tabs opened together would each believe they own the whole budget.
    # Re-read the authoritative number from Supabase and take the tighter of the
    # two, then let every checkpoint below correct it against the live server
    # value.
    if billing_user_id and billing.enabled:
        _authoritative = await billing.live_remaining(billing_user_id)
        if _authoritative is not None:
            if billing_remaining is None or _authoritative < billing_remaining:
                logger.info(
                    "Live budget tightened from token hint {}s to server value {}s user={}",
                    billing_remaining,
                    _authoritative,
                    billing_user_id,
                )
                billing_remaining = _authoritative

    _meter_start = time.monotonic()
    _billed_seconds = 0
    # Checkpoint often enough that the overrun past a spent budget is a couple of
    # seconds rather than a whole billing chunk. A user with 5s left used to get
    # a full 15s of conversation for free, every single time.
    _METER_TICK = 5

    async def _end_for_quota() -> None:
        logger.info(
            "Live budget exhausted user={} budget={}s — ending session",
            billing_user_id,
            billing_remaining,
        )
        try:
            await session.say(
                "انتهت دقائقك المجانية لهذا الشهر. تقدر ترقّي اشتراكك عشان نكمل.",
                allow_interruptions=False,
            )
            await asyncio.sleep(2)
        except Exception as say_err:  # noqa: BLE001
            logger.warning("Budget goodbye message failed: {}", say_err)
        # Ending the job only removes the AGENT: the browser stays in the room
        # and its on-screen timer keeps running, which looks like the session is
        # still live. Delete the room so the client is actually disconnected.
        try:
            await ctx.delete_room()
        except Exception as del_err:  # noqa: BLE001
            logger.warning("Could not delete room after budget exhaustion: {}", del_err)
        ctx.shutdown(reason="live_quota_exhausted")

    async def _bill_delta(target_elapsed: int) -> Optional[int]:
        """Charge everything not yet billed. Returns server-reported remaining."""
        nonlocal _billed_seconds
        delta = target_elapsed - _billed_seconds
        if not (billing_user_id and delta > 0):
            return None
        remaining = await billing.consume_live_seconds(billing_user_id, delta)
        _billed_seconds += delta
        return remaining

    async def _meter_loop() -> None:
        try:
            while True:
                await asyncio.sleep(_METER_TICK)
                elapsed = int(time.monotonic() - _meter_start)
                remaining = await _bill_delta(elapsed)
                # Prefer the value the server just returned: it accounts for
                # every other session this user has running concurrently. Only
                # fall back to the local budget when the RPC failed.
                if remaining is not None:
                    if remaining <= 0:
                        await _end_for_quota()
                        return
                elif billing_remaining is not None and elapsed >= billing_remaining:
                    await _end_for_quota()
                    return
        except asyncio.CancelledError:
            pass

    _meter_task = None
    if billing_user_id:
        if billing_remaining is not None and billing_remaining <= 0:
            # Nothing left before a word was spoken — don't hand out a free turn.
            await _end_for_quota()
            return
        _meter_task = asyncio.create_task(_meter_loop())

    async def _final_bill() -> None:
        if _meter_task:
            _meter_task.cancel()
        elapsed = int(time.monotonic() - _meter_start)
        await _bill_delta(elapsed)

    ctx.add_shutdown_callback(_final_bill)

    logger.info("LiveKit initial generate_reply beginning")
    await session.generate_reply(instructions=greeting_instructions)
    logger.info("LiveKit initial generate_reply completed")


if __name__ == "__main__":
    agents.cli.run_app(server)




    
