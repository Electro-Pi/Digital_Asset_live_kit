"""Cohere Arabic STT — POST a finished 16 kHz WAV to /v1/audio/transcriptions.

The model transcribes a complete VAD segment, not mic chunks. Wrap this STT
with livekit.agents.stt.StreamAdapter(vad=...) so LiveKit buffers speech first.
"""

from __future__ import annotations

import io
import json
import wave

import httpx
from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    stt,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given
from loguru import logger

_TARGET_SR = 16000
_TARGET_CHANNELS = 1


def _strip_v1(url: str) -> str:
    base = (url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


def _to_mono_16k(buffer: AudioBuffer) -> rtc.AudioFrame:
    frame = rtc.combine_audio_frames(buffer)
    data = bytes(frame.data)
    channels = frame.num_channels
    if channels > 1:
        samples = memoryview(data).cast("h")
        mono = bytearray(len(samples) // channels * 2)
        out = memoryview(mono).cast("h")
        for i in range(len(out)):
            acc = 0
            base = i * channels
            for c in range(channels):
                acc += samples[base + c]
            out[i] = int(acc / channels)
        frame = rtc.AudioFrame(
            data=bytes(mono),
            sample_rate=frame.sample_rate,
            num_channels=1,
            samples_per_channel=len(out),
        )

    if frame.sample_rate == _TARGET_SR:
        return frame

    resampler = rtc.AudioResampler(
        frame.sample_rate,
        _TARGET_SR,
        num_channels=_TARGET_CHANNELS,
        quality=rtc.AudioResamplerQuality.HIGH,
    )
    out_frames = resampler.push(frame)
    out_frames.extend(resampler.flush())
    if not out_frames:
        return rtc.AudioFrame(
            data=b"",
            sample_rate=_TARGET_SR,
            num_channels=_TARGET_CHANNELS,
            samples_per_channel=0,
        )
    return rtc.combine_audio_frames(out_frames)


def _frame_to_wav(frame: rtc.AudioFrame) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(_TARGET_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(_TARGET_SR)
        wav.writeframes(bytes(frame.data))
    return buf.getvalue()


def _parse_sse_text(body: str) -> str:
    pieces: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            content = delta.get("content") or choices[0].get("text") or ""
            if content:
                pieces.append(content)
            continue
        text = data.get("text")
        if text:
            pieces.append(text)
    return "".join(pieces).strip()


class CohereASRStreamingSTT(stt.STT):
    """One-shot Cohere Arabic ASR. Use StreamAdapter + VAD for LiveKit streaming."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "cohere-transcribe-arabic",
        api_key: str = "",
        language: str = "ar",
        timeout_s: float = 30.0,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._base_url = _strip_v1(base_url)
        self._model = model
        self._api_key = api_key or ""
        self._language = language or "ar"
        self._timeout_s = timeout_s
        self._headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "cohere-asr"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = language if is_given(language) and language else self._language
        frame = _to_mono_16k(buffer)
        if frame.samples_per_channel <= 0:
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(text="", language=lang)],
            )

        wav_bytes = _frame_to_wav(frame)
        url = f"{self._base_url}/v1/audio/transcriptions"
        files = {"file": ("segment.wav", wav_bytes, "audio/wav")}
        data = {
            "model": self._model,
            "language": lang,
            "response_format": "json",
            "stream": "false",
        }
        timeout = httpx.Timeout(self._timeout_s, connect=min(10.0, conn_options.timeout))

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=self._headers, data=data, files=files)
        except httpx.TimeoutException:
            raise APITimeoutError() from None
        except Exception as exc:
            raise APIConnectionError(f"Cohere STT connection failed: {exc}") from exc

        if resp.status_code >= 400:
            raise APIStatusError(
                f"STT returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                body=resp.content,
            )

        text = ""
        content_type = (resp.headers.get("content-type") or "").lower()
        raw = resp.text or ""
        if "text/event-stream" in content_type or raw.lstrip().startswith("data:"):
            text = _parse_sse_text(raw)
        else:
            try:
                payload = resp.json()
                text = str(payload.get("text") or "").strip()
            except Exception:
                text = raw.strip()

        logger.info(f"Cohere STT: {frame.duration:.2f}s audio -> {len(text)} chars")
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=text, language=lang)],
        )
