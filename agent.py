from collections.abc import AsyncIterable
from dotenv import load_dotenv
from typing import Any, Optional

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

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.plugins.qwen_asr_streaming_stt import QwenASRStreamingSTT
from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src.arabic_text import SPOKEN, WRITTEN, DualFormStream, strip_tashkeel
from src.leap_rag import (
    HOME_STAND,
    LeapRag,
    VisitorStore,
    participant_channel,
    participant_test_mode,
)
from src.prompts.agent_prompt import intro_for_voice, prompt_for_voice
from src.prompts.leap_prompt import (
    LEAP_INTRO,
    LEAP_NAME_REQUEST,
    LEAP_REGISTRATION_REMINDER,
    LEAP_SYSTEM_PROMPT,
    LEAP_WEB_SYSTEM_PROMPT,
    LEAP_WELCOME,
)
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

# ─── Speech gateway ──────────────────────────────────────────────────────────
# All three model backends — TTS, LLM and STT — are reached through the Coda
# Speech Gateway rather than dialled directly. The gateway holds the Vast
# credential, routes to the serverless workers, meters usage and breaks the
# circuit on a dead worker; none of that belongs in a voice agent. Setting the
# three *_BASE_URL vars individually still works, and is how you point one
# backend somewhere else without moving the other two.
SPEECH_GATEWAY_URL = os.getenv("SPEECH_GATEWAY_URL", "http://127.0.0.1:8011").rstrip("/")
SPEECH_GATEWAY_API_KEY = os.getenv("SPEECH_GATEWAY_API_KEY", "").strip()

TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", SPEECH_GATEWAY_URL)
# The gateway publishes exactly one model name and rejects any other.
TTS_MODEL = os.getenv("TTS_MODEL", "Coda-V2")
TTS_API_KEY = os.getenv("TTS_API_KEY", "").strip() or SPEECH_GATEWAY_API_KEY
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
# Both are OpenAI-compatible routes on the gateway. The gateway authenticates
# with its own keys, so one token covers all three backends unless a per-backend
# key is set explicitly.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", f"{SPEECH_GATEWAY_URL}/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "Coda-V2")
# openai.LLM rejects an empty api_key, so fall back to a placeholder.
LLM_API_KEY = (
    os.getenv("LLM_API_KEY", "").strip() or SPEECH_GATEWAY_API_KEY or "no-key-needed"
)

STT_BASE_URL = os.getenv("STT_BASE_URL", SPEECH_GATEWAY_URL)
STT_MODEL = os.getenv("STT_MODEL", "Coda-V2")
STT_API_KEY = os.getenv("STT_API_KEY", "").strip() or SPEECH_GATEWAY_API_KEY or None

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

# Load the local exhibitor database and visitor store once per warm worker, so a
# call never pays database/index startup time. LEAP_ENABLED is the kill switch
# for both the telephone and browser LEAP assistants.
LEAP_ENABLED = os.getenv("LEAP_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LEAP_REGISTRATION_IDLE_SECONDS = float(
    os.getenv("LEAP_REGISTRATION_IDLE_SECONDS", "8.0")
)
leap_rag: LeapRag | None = None
leap_visitors: VisitorStore | None = None
if LEAP_ENABLED:
    leap_rag = LeapRag()
    leap_visitors = VisitorStore.from_env()


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

    async def transcription_node(
        self,
        text: AsyncIterable[Any],
        model_settings: ModelSettings,
    ) -> AsyncIterable[Any]:
        """Publish the on-screen transcript: written form, and no tashkeel.

        The reply is diacritized deliberately so the TTS voices the vowels, but
        a reader should not see those marks. For companies, booths and numbers,
        LEAP writes ``[[written|spoken]]`` and this branch keeps the searchable
        written half while ``tts_node`` below keeps the speakable half.
        """
        markers = DualFormStream(WRITTEN)
        async for delta in text:
            resolved = markers.feed(delta)
            if not resolved:
                continue
            bare = strip_tashkeel(resolved)
            # TimedString is a str subclass carrying word-level alignment. A
            # resolved marker no longer aligns with the original timing, so
            # retain timing only when the delta passed through unchanged.
            if isinstance(delta, TimedString) and resolved == delta:
                yield TimedString(
                    bare,
                    start_time=delta.start_time,
                    end_time=delta.end_time,
                    confidence=delta.confidence,
                    start_time_offset=delta.start_time_offset,
                )
            else:
                yield bare
        if tail := markers.flush():
            yield strip_tashkeel(tail)

    def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> Any:
        """Synthesize the spoken half of each LEAP dual-form marker."""

        async def spoken() -> AsyncIterable[str]:
            markers = DualFormStream(SPOKEN)
            async for delta in text:
                if resolved := markers.feed(delta):
                    yield resolved
            if tail := markers.flush():
                yield tail

        return Agent.default.tts_node(self, spoken(), model_settings)

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


class LeapAssistant(Assistant):
    """Stateful, tool-grounded LEAP assistant with the telephone lead gate."""

    def __init__(
        self,
        *,
        rag: LeapRag,
        visitors: VisitorStore | None,
        session_id: str,
        visitor_source: str = "telephone",
        instructions: str = LEAP_SYSTEM_PROMPT,
        registered: bool = False,
    ) -> None:
        self._rag = rag
        self._visitors = visitors
        self._session_id = session_id
        self._visitor_source = visitor_source
        self._visitor_saved = registered
        super().__init__(instructions=instructions)

    @property
    def visitor_saved(self) -> bool:
        return self._visitor_saved

    # Deliberately shadow the cultural guide's decorated weather tool. It is
    # outside the LEAP call scope and removing it keeps tool selection smaller.
    async def lookup_weather(self, context: RunContext, location: str) -> dict[str, Any]:
        del context, location
        return {"ok": False, "error": "not_available_in_leap_mode"}

    def _registration_required(self) -> dict[str, Any] | None:
        if self._visitor_saved:
            return None
        return {
            "ok": False,
            "error": "registration_required",
            "instruction": "Collect name and phone, then call save_visitor first.",
        }

    @function_tool()
    async def save_visitor(
        self,
        context: RunContext,
        name: str,
        phone_number: str,
    ) -> dict[str, Any]:
        """Save the caller's name and mobile number before LEAP assistance.

        Args:
            name: The caller's stated name, exactly as understood.
            phone_number: The caller's mobile number, preferably as digits.
        """
        del context
        if self._visitors is None:
            return {"saved": False, "error": "visitor_store_unavailable"}
        try:
            result = await asyncio.to_thread(
                self._visitors.save,
                self._session_id,
                name,
                phone_number,
                self._visitor_source,
            )
        except ValueError as exc:
            error = str(exc)
            logger.info(
                "LEAP visitor validation rejected session={} field={}",
                self._session_id,
                error,
            )
            return {
                "saved": False,
                "error": error,
                "instruction": (
                    "Ask for the name again."
                    if error == "name_invalid"
                    else "Ask for the phone number again, digit by digit."
                ),
            }
        self._visitor_saved = True
        logger.info("LEAP visitor saved session={}", self._session_id)
        return {
            **result,
            "say_next_exactly": LEAP_WELCOME,
        }

    @function_tool()
    async def lookup_company(
        self,
        context: RunContext,
        query: str,
    ) -> dict[str, Any]:
        """Find LEAP companies by name, booth, sector, service, or sponsor status.

        Args:
            query: A short company name, booth code, sector, or activity phrase.
        """
        del context
        if blocked := self._registration_required():
            return blocked
        result = await self._rag.lookup_company(query, limit=3)
        logger.info(
            "LEAP company lookup session={} ok={} matches={} semantic_used={} retrieval_ms={}",
            self._session_id,
            result.get("ok"),
            len(result.get("matches") or []),
            result.get("semantic_used", False),
            result.get("retrieval_ms"),
        )
        return result

    @function_tool()
    async def route_to_booth(
        self,
        context: RunContext,
        destination: str,
        origin: str = HOME_STAND,
    ) -> dict[str, Any]:
        """Get a verified shortest walking route to a company or booth.

        Args:
            destination: Destination company name or booth code.
            origin: Current company or booth; defaults to Coda booth H2.K30.
        """
        del context
        if blocked := self._registration_required():
            return blocked
        result = self._rag.route(origin, destination)
        logger.info(
            "LEAP route lookup session={} ok={} error={}",
            self._session_id,
            result.get("ok"),
            result.get("error"),
        )
        if not result.get("ok"):
            return result
        return {
            "ok": True,
            "destination": result.get("destination"),
            "meters": result.get("meters"),
            "minutes": result.get("minutes"),
            "spoken_directions_ar": result.get("spoken_directions_ar"),
        }

    @function_tool()
    async def nearest_amenity(
        self,
        context: RunContext,
        kind: str,
        origin: str = HOME_STAND,
    ) -> dict[str, Any]:
        """Get a verified route to a prayer room, clinic, or main entrance.

        Args:
            kind: One of prayer, prayer_m, prayer_f, medical, or entrance.
            origin: Current company or booth; defaults to Coda booth H2.K30.
        """
        del context
        if blocked := self._registration_required():
            return blocked
        result = self._rag.nearest_amenity(origin, kind)
        logger.info(
            "LEAP amenity lookup session={} kind={} ok={} error={}",
            self._session_id,
            kind,
            result.get("ok"),
            result.get("error"),
        )
        if not result.get("ok"):
            return result
        return {
            "ok": True,
            "destination": result.get("destination"),
            "meters": result.get("meters"),
            "minutes": result.get("minutes"),
            "spoken_directions_ar": result.get("spoken_directions_ar"),
        }


class LeapWebAssistant(LeapAssistant):
    """The grounded LEAP assistant for signed-in browser sessions."""

    def __init__(self, *, rag: LeapRag, session_id: str) -> None:
        super().__init__(
            rag=rag,
            visitors=None,
            session_id=session_id,
            visitor_source="web",
            instructions=LEAP_WEB_SYSTEM_PROMPT,
            registered=True,
        )

    # Shadow the decorated telephone tool so LiveKit never offers lead capture
    # to a browser visitor who already authenticated to reach the microphone.
    async def save_visitor(
        self,
        context: RunContext,
        name: str,
        phone_number: str,
    ) -> dict[str, Any]:
        del context, name, phone_number
        return {"saved": False, "error": "not_available_in_web_mode"}


# num_idle_processes defaults to 128 in prod (one warm job process per CPU core,
# assuming a dedicated box). This is a shared server, so cap it — a few warm
# processes keep session start-up instant without spawning 128 procs on boot.
#
# AGENT_PORT is the worker's local health/debug HTTP port. It must be unique per
# running agent, so a second agent on the same host (e.g. this checkout running
# alongside another deployment) needs a free port set in .env.local.
AGENT_PORT = int(os.getenv("AGENT_PORT", "2952"))
AGENT_LOAD_THRESHOLD = float(os.getenv("AGENT_LOAD_THRESHOLD", "0.95"))
server = AgentServer(
    port=AGENT_PORT,
    num_idle_processes=3,
    load_threshold=AGENT_LOAD_THRESHOLD,
)

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
    selected_voice = parse_voice_from_metadata(participant_metadata)
    channel = participant_channel(participant_metadata)
    telephone_test_mode = participant_test_mode(participant_metadata)
    is_leap_telephone = LEAP_ENABLED and channel == "telephone"
    is_leap_web = LEAP_ENABLED and channel == "web"
    session_mode = (
        "leap_telephone"
        if is_leap_telephone
        else "leap_web"
        if is_leap_web
        else "cultural_web"
    )
    # Both LEAP prompts use a female persona. Normalize stale male selections
    # from older clients before the TTS profile is created.
    if is_leap_web and selected_voice != "female":
        logger.info(
            "LEAP web session requested voice={!r}; using the female voice",
            selected_voice,
        )
        selected_voice = "female"
    profile = voice_profile(
        selected_voice,
        female_tts_voice=TTS_VOICE,
        male_tts_voice=TTS_VOICE_MALE,
    )
    logger.info(
        "Voice profile selected key={} name_ar={} tts_voice={} has_ref_audio={} "
        "participant={} channel={} mode={} test_mode={}",
        profile.key,
        profile.display_name_ar,
        profile.tts_voice,
        bool(TTS_REF_AUDIO),
        getattr(_part, "identity", None),
        channel,
        session_mode,
        telephone_test_mode,
    )

    voice_tts = VoxCPMTTSPlugin(
        server_url=TTS_SERVER_URL,
        api_key=TTS_API_KEY,
        model=TTS_MODEL,
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
        track_obj = getattr(params, "track", None)
        track_name = getattr(track_obj, "name", None)
        # The AudioSocket bridge is a regular LiveKit participant, not a SIP
        # participant, but deliberately publishes its 8 kHz phone audio as the
        # ``telephone`` track. Detect both forms so low-bandwidth caller speech
        # is not processed as a browser microphone and suppressed by BVC.
        use_telephony = (
            participant_kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
            or track_name == "telephone"
        )
        logger.info(
            "LiveKit audio input noise cancellation selected participant={} kind={} track={} mode={}",
            participant_identity,
            participant_kind,
            track_name,
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
        turn_handling={
            # LiveKit's multilingual semantic turn detector does not support
            # Arabic. Use the existing VAD directly for the supported path.
            "turn_detection": "vad",
            "endpointing": {"mode": "dynamic", "min_delay": 0.5, "max_delay": 3.0},
            "interruption": {
                "enabled": True,
                "mode": "vad",
                "min_duration": 0.5,
                "min_words": 0,
                "resume_false_interruption": True,
                "false_interruption_timeout": 2.0,
            },
        },
        user_away_timeout=(
            LEAP_REGISTRATION_IDLE_SECONDS if is_leap_telephone else 15.0
        ),
    )


    last_eou_metrics = None
    collected_metrics = []
    speech_created_count = 0
    registration_reminders = 0
    leap_assistant: LeapAssistant | None = None

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
        nonlocal registration_reminders
        transcript_preview = (
            "<redacted LEAP telephone transcript>"
            if is_leap_telephone
            else _preview(event.transcript)
        )
        logger.info(
            "LiveKit user input transcribed final={} speaker_id={} language={} chars={} transcript={!r}",
            event.is_final,
            event.speaker_id,
            event.language,
            len(event.transcript or ""),
            transcript_preview,
        )
        if (
            event.is_final
            and leap_assistant is not None
            and not leap_assistant.visitor_saved
        ):
            registration_reminders = 0

    @session.on("user_state_changed")
    def _on_user_state_changed(event: UserStateChangedEvent):
        nonlocal registration_reminders
        logger.info(
            "LiveKit user state changed old_state={} new_state={} created_at={}",
            event.old_state,
            event.new_state,
            event.created_at,
        )
        if (
            event.new_state == "away"
            and leap_assistant is not None
            and not leap_assistant.visitor_saved
            and registration_reminders == 0
        ):
            registration_reminders = 1
            logger.info("LEAP registration idle; scheduling one missing-field reminder")
            try:
                session.generate_reply(
                    instructions=LEAP_REGISTRATION_REMINDER,
                    allow_interruptions=True,
                )
            except Exception as exc:
                logger.warning(
                    "Could not schedule LEAP registration reminder error_type={}",
                    type(exc).__name__,
                )

    @session.on("conversation_item_added")
    def _on_conversation_item_added(event: ConversationItemAddedEvent):
        item = event.item
        role = getattr(item, "role", None)
        item_type = type(item).__name__
        content = getattr(item, "content", None)
        content_preview = (
            "<redacted LEAP telephone conversation item>"
            if is_leap_telephone and item_type != "AgentHandoff"
            else _preview(content if content is not None else item)
        )
        logger.info(
            "LiveKit conversation item added type={} role={} content_preview={!r}",
            item_type,
            role,
            content_preview,
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

    if is_leap_telephone:
        if leap_rag is None or leap_visitors is None:
            raise RuntimeError("LEAP telephone mode is enabled but its services are unavailable")
        leap_assistant = LeapAssistant(
            rag=leap_rag,
            visitors=leap_visitors,
            session_id=str(getattr(ctx.room, "name", None) or getattr(job, "id", "unknown")),
            visitor_source=(
                "telephone_test" if telephone_test_mode else "telephone"
            ),
        )
        active_agent: Agent = leap_assistant
        initial_instructions = LEAP_INTRO
    elif is_leap_web:
        if leap_rag is None:
            raise RuntimeError("LEAP web mode is enabled but its services are unavailable")
        active_agent = LeapWebAssistant(
            rag=leap_rag,
            session_id=str(getattr(ctx.room, "name", None) or getattr(job, "id", "unknown")),
        )
        initial_instructions = None
    else:
        active_agent = Assistant(instructions=prompt_for_voice(profile.key))
        initial_instructions = intro_for_voice(profile.key)

    logger.info(
        "LiveKit session.start beginning room_name={} assistant_mode={}",
        getattr(ctx.room, "name", None),
        session_mode,
    )
    await session.start(
        room=ctx.room,
        agent=active_agent,
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
    if billing_user_id and billing.enabled and not is_leap_telephone:
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
    elif billing_user_id and billing.enabled and is_leap_telephone:
        # The bridge mints the token immediately before joining, so this
        # snapshot is fresh. The five-second meter below corrects concurrent
        # consumption without adding a database round trip before the greeting.
        logger.info("Using fresh token budget for low-latency telephone startup")

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

    logger.info(
        "LiveKit initial response beginning voice={} name_ar={} mode={}",
        profile.key,
        profile.display_name_ar,
        session_mode,
    )
    if is_leap_telephone:
        await session.say(LEAP_NAME_REQUEST, allow_interruptions=True)
    elif is_leap_web:
        await session.say(LEAP_WELCOME, allow_interruptions=True)
    else:
        await session.generate_reply(instructions=initial_instructions)
    logger.info("LiveKit initial response completed")


if __name__ == "__main__":
    agents.cli.run_app(server)




    
