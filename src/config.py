"""Every tunable number the agent uses, in one place.

Two kinds live here:

* **Runtime settings** read from the environment (``.env.local`` in dev, the
  systemd ``EnvironmentFile`` in production): model endpoints, audio thresholds,
  timeouts. Change them without a code change.
* **Business rules**: calorie caps, upgrade price ratio, quantity limits,
  search ranking weights. They are defaults here, also overridable from the
  environment, so a rule has exactly one definition and one name.

Nothing else in the codebase should call ``os.getenv`` for these values; import
``CONFIG`` instead. Tests build their own ``Config`` with ``load_config({...})``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env.local")


def _get(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return default if value is None or str(value).strip() == "" else str(value).strip()


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    return float(_get(env, key, str(default)))


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    return int(_get(env, key, str(default)))


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    return _get(env, key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def _optional(env: Mapping[str, str], key: str) -> Optional[str]:
    value = env.get(key)
    return str(value).strip() or None if value is not None else None


@dataclass(frozen=True)
class SearchWeights:
    """How ``src.search`` combines text relevance with the other signals.

    Text relevance is BM25, normalised to 0..1 over the candidates, so these
    weights are in the same unit. A name match must outrank every prior, which
    is why ``text`` is larger than the priors combined.
    """

    text: float = 6.0
    # Field boosts inside BM25: a word in the dish name counts this many times.
    field_name: float = 3.0
    field_restaurant: float = 0.6
    field_description: float = 1.0
    bm25_k1: float = 1.2
    bm25_b: float = 0.5
    # Priors.
    main_dish: float = 0.6  # when the caller named no dish type, mains lead
    light_match: float = 1.5  # healthy search, light dish
    serves_exact: float = 3.0
    serves_larger: float = 1.6  # minus serves_overshoot per extra person
    serves_overshoot: float = 0.5
    serves_smaller: float = -1.5
    open_restaurant: float = 0.8
    rating: float = 0.1  # multiplied by the 0..5 rating
    popular: float = 0.2


@dataclass(frozen=True)
class BusinessRules:
    timezone: str = "Asia/Riyadh"
    # Nutrition thresholds shared by tagging, filtering and the reasons she
    # gives, so she never justifies a pick with a rule the search did not use.
    light_calories_max: int = 400
    high_protein_min: int = 30
    rich_calories_min: int = 650
    rich_fat_min: int = 30
    keto_carbs_max: int = 20
    light_drink_max_calories: int = 100
    healthy_protein_per_100kcal: float = 4.0
    # A healthy search with no explicit calorie cap keeps dishes under this
    # many calories per person.
    health_max_kcal_per_person: int = 600
    # A bigger size is offered to a caller who gave no headcount only while it
    # costs at most this multiple of the dish they picked.
    solo_upgrade_max_price_ratio: float = 1.6
    # A dish's combo («وجبة شاورما كاملة») is offered while it costs at most
    # this fraction of the dish's price on top of it.
    meal_deal_max_extra_ratio: float = 0.6
    max_line_quantity: int = 50
    # Search.
    search_default_limit: int = 5
    search_max_limit: int = 8
    ambiguity_min_matches: int = 40
    # After this many declined offers in one call she stops cross-selling.
    max_declined_offers: int = 2
    # Questions before she recommends on an open request: taste, people, budget.
    max_discovery_questions: int = 3


@dataclass(frozen=True)
class VoiceSettings:
    # Silero VAD. Loaded once per worker process (prewarm).
    vad_activation_threshold: float = 0.65
    vad_deactivation_threshold: float = 0.45
    vad_min_speech_duration: float = 0.20
    vad_min_silence_duration: float = 0.45
    vad_prefix_padding_duration: float = 0.30
    # Loudness gate in front of the ASR. Every rejection is logged with its
    # levels; set STT_FOREGROUND_GATE=0 to send every VAD segment to the ASR.
    foreground_gate: bool = True
    foreground_min_rms: float = 0.015
    foreground_min_snr_db: float = 8.0
    foreground_min_duration: float = 0.20
    # Audio end-of-turn model. "v1-mini" runs locally on CPU and supports
    # Arabic; "v1" is served by LiveKit Inference.
    turn_detector_version: str = "v1-mini"
    # Seconds after he stops before a turn can close. The ASR returns whole
    # segments, so by the time text arrives this has usually passed and the
    # turn detector alone decides. Live 2026-09-24: «لا بص انا كنت عايز» …
    # «آكل كبسة» with a ~0.7s pause was cut in two at 0.3. Resumed speech is
    # seen after vad_min_speech_duration (0.2), so this must exceed pause+0.2.
    endpointing_min_delay: float = 0.8
    endpointing_max_delay: float = 2.5
    preemptive_generation: bool = True
    max_tool_steps: int = 4
    # Seconds of caller silence before the session is marked away. Testers
    # leave the call idle for long stretches.
    user_away_timeout: float = 45 * 60


@dataclass(frozen=True)
class Endpoints:
    tts_server_url: str = "http://localhost:16326"
    tts_api_key: Optional[str] = None
    tts_model: str = "VoxCPM2"
    tts_voice: str = "nouf"
    tts_expected_voice: str = "nouf"
    tts_ref_audio: Optional[str] = None
    tts_ref_text: Optional[str] = None
    tts_style: Optional[str] = None
    tts_phrase_streaming: bool = True
    tts_speed: float = 1.0
    tts_gain_db: float = 4.0
    tts_sentence_min_len: int = 32
    # Send the TTS bare letters instead of the partial tashkeel the prompt asks for.
    tts_strip_tashkeel: bool = False
    stt_base_url: str = "http://localhost:8000"
    stt_model: str = "CohereLabs/cohere-transcribe-arabic-07-2026"
    stt_api_key: Optional[str] = None


@dataclass(frozen=True)
class UISettings:
    rpc_timeout: float = 4.0
    rpc_retry_timeout: float = 3.0
    # When the browser does not answer, report the failure (default). Demo
    # setups without a frontend can set UI_MOCK_ON_FAILURE=1: results are then
    # marked ``"mocked": true`` so neither the model nor the logs mistake them
    # for real screen changes.
    mock_on_failure: bool = False


@dataclass(frozen=True)
class Config:
    log_level: str = "INFO"
    agent_port: int = 2952
    agent_name: str = "alawaly"
    num_idle_processes: int = 3
    catalog_path: Optional[str] = None
    frontend_catalog_path: Optional[str] = None
    endpoints: Endpoints = field(default_factory=Endpoints)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    rules: BusinessRules = field(default_factory=BusinessRules)
    search: SearchWeights = field(default_factory=SearchWeights)
    ui: UISettings = field(default_factory=UISettings)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    d_rules, d_voice, d_ui = BusinessRules(), VoiceSettings(), UISettings()
    tts_voice = _get(env, "TTS_VOICE", Endpoints.tts_voice)
    return Config(
        log_level=_get(env, "LOG_LEVEL", "INFO").upper(),
        agent_port=_int(env, "AGENT_PORT", 2952),
        agent_name=_get(env, "LIVEKIT_AGENT_NAME", "alawaly"),
        num_idle_processes=_int(env, "NUM_IDLE_PROCESSES", 3),
        catalog_path=_optional(env, "CATALOG_PATH"),
        frontend_catalog_path=_optional(env, "FRONTEND_CATALOG_PATH"),
        endpoints=Endpoints(
            tts_server_url=_get(env, "TTS_SERVER_URL", Endpoints.tts_server_url),
            tts_api_key=_optional(env, "TTS_API_KEY"),
            tts_model=_get(env, "TTS_MODEL", Endpoints.tts_model),
            tts_voice=tts_voice,
            tts_expected_voice=_get(env, "TTS_EXPECTED_VOICE", tts_voice),
            tts_ref_audio=_optional(env, "TTS_REF_AUDIO"),
            tts_ref_text=_optional(env, "TTS_REF_TEXT"),
            tts_style=_optional(env, "TTS_STYLE"),
            tts_phrase_streaming=_bool(env, "TTS_PHRASE_STREAMING", True),
            tts_speed=_float(env, "TTS_SPEED", 1.0),
            tts_gain_db=_float(env, "TTS_GAIN_DB", 4.0),
            tts_sentence_min_len=_int(env, "TTS_SENTENCE_MIN_LEN", 32),
            tts_strip_tashkeel=_bool(env, "TTS_STRIP_TASHKEEL", False),
            stt_base_url=_get(env, "STT_BASE_URL", Endpoints.stt_base_url),
            stt_model=_get(env, "STT_MODEL", Endpoints.stt_model),
            stt_api_key=_optional(env, "STT_API_KEY"),
        ),
        voice=VoiceSettings(
            vad_activation_threshold=_float(env, "STT_VAD_ACTIVATION_THRESHOLD", d_voice.vad_activation_threshold),
            vad_deactivation_threshold=_float(env, "STT_VAD_DEACTIVATION_THRESHOLD", d_voice.vad_deactivation_threshold),
            vad_min_speech_duration=_float(env, "STT_VAD_MIN_SPEECH_DURATION", d_voice.vad_min_speech_duration),
            vad_min_silence_duration=_float(env, "STT_VAD_MIN_SILENCE_DURATION", d_voice.vad_min_silence_duration),
            vad_prefix_padding_duration=_float(env, "STT_VAD_PREFIX_PADDING_DURATION", d_voice.vad_prefix_padding_duration),
            foreground_gate=_bool(env, "STT_FOREGROUND_GATE", d_voice.foreground_gate),
            foreground_min_rms=_float(env, "STT_FOREGROUND_MIN_RMS", d_voice.foreground_min_rms),
            foreground_min_snr_db=_float(env, "STT_FOREGROUND_MIN_SNR_DB", d_voice.foreground_min_snr_db),
            foreground_min_duration=_float(env, "STT_FOREGROUND_MIN_DURATION", d_voice.foreground_min_duration),
            turn_detector_version=_get(env, "TURN_DETECTOR_VERSION", d_voice.turn_detector_version),
            endpointing_min_delay=_float(env, "ENDPOINTING_MIN_DELAY", d_voice.endpointing_min_delay),
            endpointing_max_delay=_float(env, "ENDPOINTING_MAX_DELAY", d_voice.endpointing_max_delay),
            preemptive_generation=_bool(env, "PREEMPTIVE_GENERATION", d_voice.preemptive_generation),
            max_tool_steps=_int(env, "MAX_TOOL_STEPS", d_voice.max_tool_steps),
            user_away_timeout=_float(env, "USER_AWAY_TIMEOUT", d_voice.user_away_timeout),
        ),
        rules=BusinessRules(
            timezone=_get(env, "BUSINESS_TIMEZONE", d_rules.timezone),
            light_calories_max=_int(env, "LIGHT_CALORIES_MAX", d_rules.light_calories_max),
            high_protein_min=_int(env, "HIGH_PROTEIN_MIN", d_rules.high_protein_min),
            rich_calories_min=_int(env, "RICH_CALORIES_MIN", d_rules.rich_calories_min),
            rich_fat_min=_int(env, "RICH_FAT_MIN", d_rules.rich_fat_min),
            keto_carbs_max=_int(env, "KETO_CARBS_MAX", d_rules.keto_carbs_max),
            light_drink_max_calories=_int(env, "LIGHT_DRINK_MAX_CALORIES", d_rules.light_drink_max_calories),
            healthy_protein_per_100kcal=_float(env, "HEALTHY_PROTEIN_PER_100KCAL", d_rules.healthy_protein_per_100kcal),
            health_max_kcal_per_person=_int(env, "HEALTH_MAX_KCAL_PER_PERSON", d_rules.health_max_kcal_per_person),
            solo_upgrade_max_price_ratio=_float(env, "SOLO_UPGRADE_MAX_PRICE_RATIO", d_rules.solo_upgrade_max_price_ratio),
            meal_deal_max_extra_ratio=_float(env, "MEAL_DEAL_MAX_EXTRA_RATIO", d_rules.meal_deal_max_extra_ratio),
            max_line_quantity=_int(env, "MAX_LINE_QUANTITY", d_rules.max_line_quantity),
            search_default_limit=_int(env, "SEARCH_DEFAULT_LIMIT", d_rules.search_default_limit),
            search_max_limit=_int(env, "SEARCH_MAX_LIMIT", d_rules.search_max_limit),
            ambiguity_min_matches=_int(env, "AMBIGUITY_MIN_MATCHES", d_rules.ambiguity_min_matches),
            max_declined_offers=_int(env, "MAX_DECLINED_OFFERS", d_rules.max_declined_offers),
            max_discovery_questions=_int(env, "MAX_DISCOVERY_QUESTIONS", d_rules.max_discovery_questions),
        ),
        ui=UISettings(
            rpc_timeout=_float(env, "UI_RPC_TIMEOUT", d_ui.rpc_timeout),
            rpc_retry_timeout=_float(env, "UI_RPC_RETRY_TIMEOUT", d_ui.rpc_retry_timeout),
            mock_on_failure=_bool(env, "UI_MOCK_ON_FAILURE", d_ui.mock_on_failure),
        ),
    )


CONFIG = load_config()
