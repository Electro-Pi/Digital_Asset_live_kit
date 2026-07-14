"""Streaming STT plugin for the local QwenCleo/Qwen3-ASR vLLM server.

The running local service is a vLLM OpenAI-compatible server. It exposes:
  POST /v1/audio/transcriptions    one-shot transcription
  POST /v1/chat/completions        token streaming when stream=true and the
                                   audio is sent as an audio_url data URI

vLLM streams output tokens for a complete audio segment; it does not expose a
microphone-style /api/start -> /api/chunk -> /api/finish protocol. This plugin
therefore buffers one LiveKit speech segment, sends it to the vLLM streaming
chat endpoint, then emits transcript deltas as interim LiveKit STT events.
"""

from __future__ import annotations

import base64
import io
import json
import math
import uuid
import wave

import aiohttp
import numpy as np

from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents._exceptions import APIConnectionError, APIStatusError
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import AudioBuffer, is_given

SERVER_SAMPLE_RATE = 16000

_PROMPT_LANGUAGE_NAMES = {
    "ar": "Arabic",
    "en": "English",
    "zh": "Chinese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}


def _to_prompt_language(language: str | None) -> str | None:
    if not language:
        return None
    return _PROMPT_LANGUAGE_NAMES.get(language.split("-")[0].lower(), language)


def _to_transcription_language(language: str | None) -> str | None:
    if not language:
        return None
    return language.split("-")[0].lower()


def _pcm16_to_wav_bytes(pcm: bytes, sample_rate: int = SERVER_SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


class QwenASRStreamingSTT(stt.STT):
    def __init__(
        self,
        *,
        base_url: str,
        language: str | None = "ar",
        model: str = "mohammedaly22/QwenCleo-ASR",
        chunk_ms: int = 250,
        end_silence_ms: int = 500,
        silence_rms: float = 0.008,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Args:
            base_url: vLLM server URL, e.g. "http://localhost:8001".
            language: ISO code ("ar") or full name ("Arabic"); None = auto-detect.
            chunk_ms: local audio chunk size used for silence detection.
            end_silence_ms: trailing silence after which the segment is finalized.
            silence_rms: RMS threshold (on [-1, 1] samples) below which a chunk
                counts as silence; chunks are gated so the model doesn't
                hallucinate text from background noise.
        """
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True)
        )
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._model = model
        self._chunk_ms = chunk_ms
        self._end_silence_ms = end_silence_ms
        self._silence_rms = silence_rms
        self._session = http_session

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "qwen-asr-server"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = language if is_given(language) else self._language
        frame = rtc.combine_audio_frames(buffer)
        form = aiohttp.FormData()
        form.add_field("model", self._model)
        if lang:
            form.add_field("language", _to_transcription_language(lang) or "")
        form.add_field(
            "file", frame.to_wav_bytes(), filename="audio.wav", content_type="audio/wav"
        )

        try:
            async with self._ensure_session().post(
                f"{self._base_url}/v1/audio/transcriptions",
                data=form,
                timeout=aiohttp.ClientTimeout(total=conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(
                        f"qwen-asr transcription failed: {await resp.text()}",
                        status_code=resp.status,
                    )
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise APIConnectionError("failed to reach qwen-asr server") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    language=data.get("language") or lang or "",
                    text=data.get("text") or "",
                )
            ],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> QwenASRSpeechStream:
        return QwenASRSpeechStream(
            stt=self,
            language=language if is_given(language) else self._language,
            conn_options=conn_options,
        )


class QwenASRSpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: QwenASRStreamingSTT,
        language: str | None,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SERVER_SAMPLE_RATE)
        self._qwen_stt = stt
        self._language = language
        self._segment_duration = 0.0
        self._silent_chunks = 0
        self._segment_pcm = bytearray()
        self._finalize_after_chunks = max(1, math.ceil(stt._end_silence_ms / stt._chunk_ms))

    async def _stream_segment(self, request_id: str, pcm: bytes) -> str:
        wav = _pcm16_to_wav_bytes(pcm)
        data_url = "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")
        content: list[dict] = [{"type": "audio_url", "audio_url": {"url": data_url}}]
        prompt_language = _to_prompt_language(self._language)
        if prompt_language:
            content.insert(0, {"type": "text", "text": f"language {prompt_language}"})

        payload = {
            "model": self._qwen_stt._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 256,
            "stream": True,
            "temperature": 0,
        }

        text = ""
        prefix_buf = ""
        passed_marker = False
        marker = "<asr_text>"

        try:
            async with self._qwen_stt._ensure_session().post(
                f"{self._qwen_stt._base_url}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(
                        f"qwen-asr streaming request failed: {await resp.text()}",
                        status_code=resp.status,
                    )

                async for raw in resp.content:
                    for line in raw.decode("utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            return text.strip()

                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if not delta:
                            continue

                        if not passed_marker:
                            prefix_buf += delta
                            marker_idx = prefix_buf.find(marker)
                            if marker_idx == -1:
                                continue
                            passed_marker = True
                            delta = prefix_buf[marker_idx + len(marker):]
                            if not delta:
                                continue

                        text += delta
                        if text.strip():
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(
                                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                    request_id=request_id,
                                    alternatives=[
                                        stt.SpeechData(
                                            language=self._language or "",
                                            text=text.strip(),
                                        )
                                    ],
                                )
                            )
        except aiohttp.ClientError as e:
            raise APIConnectionError("failed to reach qwen-asr server") from e

        return text.strip()

    def _append_chunk(self, chunk: bytes, samples: np.ndarray) -> None:
        self._segment_pcm.extend(chunk)
        self._segment_duration += len(samples) / SERVER_SAMPLE_RATE

    async def _finalize_segment(self) -> None:
        if not self._segment_pcm:
            return

        request_id = f"qwen-{uuid.uuid4().hex}"
        pcm = bytes(self._segment_pcm)
        duration = self._segment_duration
        self._segment_pcm.clear()
        self._segment_duration = 0.0
        self._silent_chunks = 0

        text = await self._stream_segment(request_id, pcm)
        if text:
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=request_id,
                    alternatives=[
                        stt.SpeechData(
                            language=self._language or "",
                            text=text,
                        )
                    ],
                )
            )
        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.RECOGNITION_USAGE,
                request_id=request_id,
                recognition_usage=stt.RecognitionUsage(
                    audio_duration=duration
                ),
            )
        )

    async def _run(self) -> None:
        chunk_samples = SERVER_SAMPLE_RATE * self._qwen_stt._chunk_ms // 1000
        chunk_bytes = chunk_samples * 2  # int16
        buf = bytearray()

        try:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    await self._finalize_segment()
                    continue

                buf.extend(data.data)
                while len(buf) >= chunk_bytes:
                    chunk = bytes(buf[:chunk_bytes])
                    del buf[:chunk_bytes]

                    samples = (
                        np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    )
                    rms = float(np.sqrt(np.mean(samples**2)))

                    if rms >= self._qwen_stt._silence_rms:
                        self._silent_chunks = 0
                        self._append_chunk(chunk, samples)
                    elif self._segment_pcm:
                        self._silent_chunks += 1
                        if self._silent_chunks == 1:
                            # hangover chunk so trailing word endings aren't clipped
                            self._append_chunk(chunk, samples)
                        elif self._silent_chunks >= self._finalize_after_chunks:
                            await self._finalize_segment()

            await self._finalize_segment()
        finally:
            pass
