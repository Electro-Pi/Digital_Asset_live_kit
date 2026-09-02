from collections.abc import AsyncIterable, Callable
from dotenv import load_dotenv
from typing import Any, Optional

import asyncio
import os
import time
from loguru import logger

import httpx

from livekit import agents, rtc
from livekit.agents import llm
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
    ModelSettings,
    SessionUsageUpdatedEvent,
    SpeechCreatedEvent,
    ToolError,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    metrics,
    tokenize,
)
from livekit.agents.types import TimedString
from livekit.agents.tts import StreamAdapter
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.plugins.qwen_asr_streaming_stt import QwenASRStreamingSTT
from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src.arabic_text import strip_tashkeel
from src.leap_navigation import (
    NavigationNarrationError,
    NavigationLocationError,
    NavigatorLanguage,
    build_navigation_route_action,
    narration_text_from_data_packet,
    navigator_intro,
    navigator_prompt,
    parse_navigator_session,
    publish_navigation_route,
    resolve_navigator_location,
)
from src.prompts.agent_prompt import intro_for_voice, prompt_for_voice
from src.billing import (
    billing_from_env,
    parse_participant_api_key,
    parse_participant_budget,
)
from src.voice_profile import (
    DEFAULT_FEMALE_VOICE,
    DEFAULT_MALE_VOICE,
    parse_voice_from_metadata,
    voice_profile,
)


load_dotenv(".env.local")

TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", "http://localhost:16326")
TTS_MODEL = os.getenv("TTS_MODEL", "VoxCPM2")
TTS_API_KEY = os.getenv("SPEECH_GATEWAY_API_KEY", "").strip() or None
# Server-side voice names — see GET /v1/audio/voices. Both speakers are
# precomputed upstream ("nouf" female / نوف, "nawaf" male / نواف); which one a
# session gets is chosen per participant, so these are the names, not the pick.
TTS_VOICE = os.getenv("TTS_VOICE", DEFAULT_FEMALE_VOICE)
TTS_VOICE_MALE = os.getenv("TTS_VOICE_MALE", DEFAULT_MALE_VOICE)
# Prefer those precomputed voices. A reference is only worth setting to override
# both of them with a clone, and only for audio the TTS server can actually read
# (a path inside its filesystem, a URL, or a data URI).
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


class _TranscriptAgent(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(
            instructions=instructions,
        )

    async def transcription_node(
        self,
        text: AsyncIterable[Any],
        model_settings: ModelSettings,
    ) -> AsyncIterable[Any]:
        """Publish the on-screen transcript bare, with no tashkeel.

        The reply is diacritized deliberately so the TTS voices the vowels
        instead of guessing them (see prompts/agent_prompt.py), but a reader
        should not see the marks — Arabic prose is written bare.

        Only the transcript branch is touched. ``tts_node`` is fed from the same
        LLM output through a separate path, so the audio still receives the full
        diacritization; this is what makes the rule hold for every client rather
        than only the ones that remember to strip it themselves.
        """
        async for delta in text:
            bare = strip_tashkeel(delta)
            # TimedString is a str subclass carrying word-level alignment. A
            # plain str here would silently drop the timing the client uses to
            # sync captions to the audio, so rebuild it.
            if isinstance(delta, TimedString):
                yield TimedString(
                    bare,
                    start_time=delta.start_time,
                    end_time=delta.end_time,
                    confidence=delta.confidence,
                    start_time_offset=delta.start_time_offset,
                )
            else:
                yield bare


class Assistant(_TranscriptAgent):
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


class NavigatorAssistant(_TranscriptAgent):
    """Map-aware LEAP persona selected only by signed participant metadata."""

    def __init__(
        self,
        *,
        instructions: str,
        room: rtc.Room,
        language: NavigatorLanguage,
        start_id: str | None = None,
        destination_id: str | None = None,
    ) -> None:
        self._navigator_room = room
        self._navigator_language = language
        self._navigator_start_id = start_id
        self._navigator_destination_id = destination_id
        super().__init__(instructions=instructions)

    async def on_user_turn_completed(
        self,
        turn_ctx: llm.ChatContext,
        new_message: llm.ChatMessage,
    ) -> None:
        """Resolve a clearly stated single endpoint before the LLM can guess.

        This is deliberately stateful and conservative. While collecting the
        first route, an otherwise bare place name fills the missing endpoint.
        Once a route exists, automatic replacement requires explicit navigation
        language such as "take me to" / "وديني إلى", so a casual question about
        an exhibitor does not unexpectedly redraw the map.
        """
        text = " ".join(
            part for part in new_message.content if isinstance(part, str)
        ).strip()
        if not text:
            return

        normalized_text = strip_tashkeel(text).casefold()
        destination_intent = any(marker in normalized_text for marker in (
            " إلى ", " الى ", "ابي", "أبي", "ابغ", "أبغ", "اريد", "أريد",
            "اروح", "أروح", "خذني", "روح بي", "وديني", "ودي", "take me",
            "go to", "going to", "want to", "navigate to", "directions to",
        ))
        current_intent = any(marker in normalized_text for marker in (
            "انا عند", "أنا عند", "انا في", "أنا في", "موقعي", "واقف عند",
            "i am at", "i'm at", "im at", "my location", "standing at",
        ))
        if destination_intent and current_intent:
            return
        if destination_intent:
            endpoint_role = "destination"
        elif current_intent:
            endpoint_role = "current_location"
        elif not self._navigator_start_id:
            endpoint_role = "current_location"
        elif not self._navigator_destination_id:
            endpoint_role = "destination"
        else:
            return

        try:
            resolved = resolve_navigator_location(text, role=endpoint_role.replace("_", " "))
        except NavigationLocationError:
            return

        result = await self.set_navigation_route(
            None,
            **{endpoint_role: resolved.id},
        )
        status = result["status"]
        if status == "route_set":
            instruction = (
                "Trusted navigator update: the deterministic catalog already "
                f"resolved this turn and displayed the route from {result['startId']} "
                f"to {result['destinationId']}. Do not ask for spelling or a booth "
                "code and do not call the tool again. Briefly confirm the new route."
            )
        elif status == "need_destination":
            instruction = (
                "Trusted navigator update: the current location was validated and "
                "remembered. Do not call the tool again for this turn; ask only for "
                "the destination."
            )
        else:
            instruction = (
                "Trusted navigator update: the destination was validated and "
                "remembered. Do not call the tool again for this turn; ask only for "
                "the current location."
            )
        turn_ctx.add_message(role="system", content=instruction)

    @function_tool()
    async def set_navigation_route(
        self,
        context: RunContext,
        current_location: str | None = None,
        destination: str | None = None,
    ) -> dict[str, Any]:
        """Validate and remember either endpoint as soon as it is spoken.

        Do not pre-validate a name and do not ask whether it is a booth, office,
        or other place before calling this tool. Pass the visitor's natural
        wording unchanged. The deterministic resolver is the only authority on
        whether a name is unique, duplicated, or unmatched. Either argument may
        be omitted: the tool remembers a previously validated endpoint and says
        which one is still needed. If both are known, it displays the route.

        Args:
            current_location: A newly spoken current place, if supplied, as a
                location ID, English or Arabic name, or spoken alias.
            destination: A newly spoken destination, if supplied, as a location
                ID, English or Arabic name, or spoken alias.
        """
        _ = context
        resolved_start_id = self._navigator_start_id
        resolved_destination_id = self._navigator_destination_id
        try:
            if current_location and current_location.strip():
                resolved_start_id = resolve_navigator_location(
                    current_location,
                    role="current location",
                ).id
            if destination and destination.strip():
                resolved_destination_id = resolve_navigator_location(
                    destination,
                    role="destination",
                ).id
        except NavigationLocationError as error:
            raise ToolError(str(error)) from error

        if not resolved_start_id and not resolved_destination_id:
            raise ToolError(
                "No location was supplied. Ask for the visitor's current location."
            )

        self._navigator_start_id = resolved_start_id
        self._navigator_destination_id = resolved_destination_id
        if not resolved_start_id:
            destination_match = resolve_navigator_location(resolved_destination_id)
            return {
                "status": "need_current_location",
                "destinationId": destination_match.id,
                "destination": {
                    "name": destination_match.name,
                    "nameAr": destination_match.name_ar,
                },
            }
        if not resolved_destination_id:
            start_match = resolve_navigator_location(resolved_start_id)
            return {
                "status": "need_destination",
                "startId": start_match.id,
                "start": {
                    "name": start_match.name,
                    "nameAr": start_match.name_ar,
                },
            }

        try:
            action = build_navigation_route_action(
                resolved_start_id,
                resolved_destination_id,
                language=self._navigator_language,
            )
        except NavigationLocationError as error:
            raise ToolError(str(error)) from error

        try:
            await publish_navigation_route(self._navigator_room, action)
        except Exception as error:  # noqa: BLE001
            logger.exception(
                "Could not publish navigator route start={} destination={}",
                action["startId"],
                action["destinationId"],
            )
            raise ToolError(
                "The map could not be updated. Do not claim the route is ready; "
                "ask the visitor to try again."
            ) from error

        logger.info(
            "Navigator route published start={} destination={} language={}",
            action["startId"],
            action["destinationId"],
            action["language"],
        )
        return {
            "status": "route_set",
            "startId": action["startId"],
            "destinationId": action["destinationId"],
            "language": action["language"],
            "narration": "pending_frontend_route_steps",
        }


def register_navigator_narration_handler(
    *,
    room: rtc.Room,
    session: AgentSession,
    participant_identity: str,
    language: NavigatorLanguage,
) -> Callable[[rtc.DataPacket], None]:
    """Speak one validated route packet while allowing natural barge-in."""
    last_narration_payload: bytes | None = None

    @room.on("data_received")
    def _on_navigator_narration(packet: rtc.DataPacket) -> None:
        nonlocal last_narration_payload
        packet_payload = bytes(getattr(packet, "data", b""))
        if packet_payload and packet_payload == last_narration_payload:
            logger.info(
                "Ignoring duplicate navigator narration participant={}",
                participant_identity,
            )
            return
        try:
            narration_text = narration_text_from_data_packet(
                packet,
                expected_participant_identity=participant_identity,
                expected_language=language,
            )
        except NavigationNarrationError as error:
            logger.warning(
                "Rejected navigator narration participant={} bytes={} reason={}",
                getattr(getattr(packet, "participant", None), "identity", None),
                len(getattr(packet, "data", b"")),
                error,
            )
            return
        if narration_text is None:
            return

        try:
            # One say() call is the one-shot contract. The text is assembled only
            # from the validated deterministic frontend steps; no LLM is involved.
            session.say(narration_text, allow_interruptions=True)
        except Exception as error:  # noqa: BLE001
            logger.exception(
                "Could not schedule navigator route narration participant={}",
                participant_identity,
            )
            return
        last_narration_payload = packet_payload
        logger.info(
            "Navigator full route narration scheduled participant={} chars={}",
            participant_identity,
            len(narration_text),
        )

    return _on_navigator_narration


# num_idle_processes defaults to 128 in prod (one warm job process per CPU core,
# assuming a dedicated box). This is a shared server, so cap it — a few warm
# processes keep session start-up instant without spawning 128 procs on boot.
#
# AGENT_PORT is the worker's local health/debug HTTP port. It must be unique per
# running agent, so a second agent on the same host (e.g. this checkout running
# alongside another deployment) needs a free port set in .env.local.
AGENT_PORT = int(os.getenv("AGENT_PORT", "2952"))
AGENT_IDLE_PROCESSES = max(1, int(os.getenv("AGENT_IDLE_PROCESSES", "3")))
server = AgentServer(port=AGENT_PORT, num_idle_processes=AGENT_IDLE_PROCESSES)

# Authoritative per-user live-minutes meter. Talks to Supabase with the
# service-role key (server-only). No-op if Supabase env is unset — in that case
# only the LiveKit token TTL caps the session.
billing = billing_from_env()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    job = getattr(ctx, "job", None)
    logger.info(
        "LiveKit agent job starting room_name={} room_sid={} job_id={} "
        "tts_server_url={} tts_model={} tts_voice_default={} "
        "tts_seed={} tts_speed={} sentence_min_len={}",
        getattr(ctx.room, "name", None),
        getattr(getattr(job, "room", None), "sid", None),
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
    # Resolve the caller first so female (نوف) vs male (نواف) is known before the
    # TTS plugin and persona prompt are built.
    _part = None
    try:
        _part = await asyncio.wait_for(ctx.wait_for_participant(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(
            "No remote participant appeared within 15s; defaulting to female voice "
            "and disabling live billing (token TTL still caps the session)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not resolve the calling participant: {}", e)

    participant_metadata = getattr(_part, "metadata", None) if _part else None
    navigator_session = parse_navigator_session(participant_metadata)
    selected_voice = parse_voice_from_metadata(participant_metadata)
    profile = voice_profile(
        selected_voice,
        female_tts_voice=TTS_VOICE,
        male_tts_voice=TTS_VOICE_MALE,
    )
    logger.info(
        "Voice profile selected key={} name_ar={} tts_voice={} has_ref_audio={} "
        "participant={}",
        profile.key,
        profile.display_name_ar,
        profile.tts_voice,
        bool(TTS_REF_AUDIO),
        getattr(_part, "identity", None),
    )
    logger.info(
        "Participant experience selected experience={} language={} start={} destination={}",
        "leap-navigator" if navigator_session else "coda",
        navigator_session.language if navigator_session else None,
        navigator_session.start_id if navigator_session else None,
        navigator_session.destination_id if navigator_session else None,
    )

    voice_tts = VoxCPMTTSPlugin(
        server_url=TTS_SERVER_URL,
        model=TTS_MODEL,
        api_key=TTS_API_KEY,
        voice=profile.tts_voice,
        # Normally None: both speakers are named voices on the server. Setting
        # TTS_REF_AUDIO overrides whichever one this session picked.
        ref_audio=TTS_REF_AUDIO,
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
        # Route narration can be long, so barge-in must be a first-class path:
        # one clearly detected word stops Coda quickly and becomes a normal
        # conversational turn (for example, "لا، وديني لمايكروسوفت").
        allow_interruptions=True,
        min_interruption_duration=0.25,
        min_interruption_words=1,
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

    if navigator_session:
        session_agent: Agent = NavigatorAssistant(
            instructions=navigator_prompt(
                navigator_session.language,
                start_id=navigator_session.start_id,
                destination_id=navigator_session.destination_id,
            ),
            room=ctx.room,
            language=navigator_session.language,
            start_id=navigator_session.start_id,
            destination_id=navigator_session.destination_id,
        )
        initial_reply_instructions = navigator_intro(
            navigator_session.language,
            start_id=navigator_session.start_id,
            destination_id=navigator_session.destination_id,
        )
    else:
        # Preserve the existing Coda cultural-guide behavior for every session
        # that was not explicitly tagged by the trusted token issuer.
        session_agent = Assistant(instructions=prompt_for_voice(profile.key))
        initial_reply_instructions = intro_for_voice(profile.key)

    if navigator_session and _part:
        # Register before session.start publishes the listening state. The web
        # client waits for that state before sending its single reliable route
        # narration packet, so there is no join-time packet-loss race.
        register_navigator_narration_handler(
            room=ctx.room,
            session=session,
            participant_identity=_part.identity,
            language=navigator_session.language,
        )

    logger.info("LiveKit session.start beginning room_name={}", getattr(ctx.room, "name", None))
    await session.start(
        room=ctx.room,
        agent=session_agent,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=_noise_cancellation_for
            ),
        ),
    )
    logger.info("LiveKit session.start completed room_name={}", getattr(ctx.room, "name", None))

    # ─── Per-user live-minutes budget (authoritative meter) ──────────────────
    # The /api/connection-details token route stamped the Supabase user id +
    # remaining seconds into the participant identity/metadata. Participant was
    # already resolved above for voice selection.
    billing_user_id, billing_remaining = parse_participant_budget(
        getattr(_part, "identity", None), getattr(_part, "metadata", None)
    )
    # Set only for sessions opened with an API key through the realtime gateway;
    # None for the website, whose usage stays attributed to the user alone.
    billing_api_key_id = parse_participant_api_key(getattr(_part, "metadata", None))
    logger.info(
        "Live budget resolved user={} remaining={}s api_key={} billing_enabled={}",
        billing_user_id,
        billing_remaining,
        billing_api_key_id,
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
        remaining = await billing.consume_live_seconds(
            billing_user_id, delta, billing_api_key_id
        )
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

    if (
        navigator_session
        and navigator_session.start_id
        and navigator_session.destination_id
    ):
        # The browser already has the deterministic route and will send all of
        # its instructions once Coda is listening. Avoid a competing LLM
        # confirmation before that authoritative narration.
        logger.info(
            "Skipping navigator initial reply; awaiting full frontend route "
            "start={} destination={}",
            navigator_session.start_id,
            navigator_session.destination_id,
        )
    else:
        logger.info(
            "LiveKit initial generate_reply beginning voice={} name_ar={}",
            profile.key,
            profile.display_name_ar,
        )
        await session.generate_reply(instructions=initial_reply_instructions)
        logger.info("LiveKit initial generate_reply completed")


if __name__ == "__main__":
    agents.cli.run_app(server)




    
