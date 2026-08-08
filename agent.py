from dotenv import load_dotenv
from typing import Any

import asyncio
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
from livekit.agents.tts import StreamAdapter
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.plugins.qwen_asr_streaming_stt import QwenASRStreamingSTT
from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src.prompts.agent_prompt import intro_for_voice, prompt_for_voice
from src.voice_profile import parse_voice_from_metadata, voice_profile


load_dotenv(".env.local")

TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", "http://localhost:16326")
TTS_MODEL = os.getenv("TTS_MODEL", "VoxCPM2")
TTS_VOICE = os.getenv("TTS_VOICE", "hoda")
# The voice this deployment is supposed to be speaking with. The VoxCPM2 server
# serves "hoda" (precomputed female) and "default" (male), so this is config,
# not a constant — see GET /v1/audio/voices.
TTS_EXPECTED_VOICE = os.getenv("TTS_EXPECTED_VOICE", TTS_VOICE)
# Prefer the TTS server's precomputed ``hoda`` voice. A reference path is only
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
# VoxCPM2's `hoda` already speaks 21% slower than the human reference recording,
# so the extra slow-down bought nothing and cost the artifact.
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


def _preview(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


class Assistant(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(
            instructions=instructions,
        )



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
    logger.info(
        "LiveKit agent job starting room_name={} room_sid={} job_id={} "
        "tts_server_url={} tts_model={} tts_voice_default={} "
        "tts_seed={} tts_speed={} sentence_min_len={}",
        getattr(ctx.room, "name", None),
        getattr(ctx.room, "sid", None),
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

    selected_voice = parse_voice_from_metadata(
        getattr(_part, "metadata", None) if _part else None
    )
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

    logger.info("LiveKit session.start beginning room_name={}", getattr(ctx.room, "name", None))
    await session.start(
        room=ctx.room,
        agent=Assistant(instructions=prompt_for_voice(profile.key)),
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




    
