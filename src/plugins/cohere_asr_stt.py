"""Speech-to-text for the Coda Arabic ASR server (OpenAI-compatible, vLLM).

The model (``CohereLabs/cohere-transcribe-arabic-07-2026``) transcribes a
complete audio segment; it cannot take audio while the caller is still
talking. So the session wraps this plugin in LiveKit's ``stt.StreamAdapter``,
which cuts segments with the VAD and calls ``recognize`` once per segment.

Before a segment is sent, a loudness gate (``foreground_speech_gate``) can
reject it as background: speech from another room, a TV. Every rejection is
logged with its levels, so a caller who "is not heard" shows up in the logs
with the numbers that dropped him. ``STT_FOREGROUND_GATE=0`` turns it off.
"""

from __future__ import annotations

import aiohttp
from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents._exceptions import APIConnectionError, APIStatusError
from livekit.agents.types import NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer
from loguru import logger

from src.plugins.foreground_speech_gate import analyze_foreground_speech

LANGUAGE = "ar"
DEFAULT_MODEL = "CohereLabs/cohere-transcribe-arabic-07-2026"


def _repair_heard(text: str) -> str:
    """Fix known ASR slips before the LLM sees the turn."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    try:
        from src.catalog import repair_user_transcript

        return repair_user_transcript(cleaned)
    except Exception:  # noqa: BLE001 - never drop a turn over a repair miss
        return cleaned


class CohereASRSTT(stt.STT):
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        foreground_gate: bool = True,
        min_foreground_rms: float = 0.015,
        min_foreground_snr_db: float = 8.0,
        min_foreground_duration: float = 0.20,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._api_key = (api_key or "").strip() or None
        self._gate = foreground_gate
        self._min_foreground_rms = min_foreground_rms
        self._min_foreground_snr_db = min_foreground_snr_db
        self._min_foreground_duration = min_foreground_duration
        self._session = http_session

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "cohere-asr-server"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def gate_accepts(self, frame: rtc.AudioFrame) -> bool:
        """False when the segment is background; the reason is logged."""
        if not self._gate:
            return True
        result = analyze_foreground_speech(
            frame,
            min_foreground_rms=self._min_foreground_rms,
            min_snr_db=self._min_foreground_snr_db,
            min_foreground_duration=self._min_foreground_duration,
        )
        if not result.accepted:
            logger.info(
                "ASR gate rejected segment reason={} duration={:.2f}s foreground={:.2f}s "
                "noise_rms={:.4f} foreground_rms={:.4f} (min {:.4f}) snr_db={:.1f} (min {:.1f})",
                result.reason, result.duration, result.foreground_duration, result.noise_rms,
                result.foreground_rms, self._min_foreground_rms, result.snr_db, self._min_foreground_snr_db,
            )
        return result.accepted

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        if not self.gate_accepts(frame):
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(language=LANGUAGE, text="")],
            )
        form = aiohttp.FormData()
        form.add_field("model", self._model)
        form.add_field("language", LANGUAGE)
        form.add_field("response_format", "json")
        form.add_field("file", frame.to_wav_bytes(), filename="audio.wav", content_type="audio/wav")
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with self._ensure_session().post(
                f"{self._base_url}/v1/audio/transcriptions",
                data=form,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(f"cohere-asr transcription failed: {await resp.text()}", status_code=resp.status)
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise APIConnectionError("failed to reach cohere-asr server") from e
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=LANGUAGE, text=_repair_heard((data.get("text") or "").strip()))],
        )
