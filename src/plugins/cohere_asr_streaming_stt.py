"""Streaming STT plugin for the local Cohere Arabic transcription vLLM server.

The running local service is a vLLM OpenAI-compatible server hosting
``CohereLabs/cohere-transcribe-arabic-07-2026``. It exposes:
  POST /v1/audio/transcriptions    one-shot transcription, and, when
                                   ``stream=true`` is sent, token streaming as
                                   Server-Sent Events (``transcription.chunk``).

This model is Arabic-only and transcribes a complete audio segment; it does not
expose a microphone-style /api/start -> /api/chunk -> /api/finish protocol. This
plugin therefore buffers one LiveKit speech segment (gating on silence so the
model doesn't hallucinate text from background noise), streams it to the vLLM
transcription endpoint, then emits transcript deltas as interim LiveKit STT
events followed by a final transcript.
"""

from __future__ import annotations

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

# This model transcribes Arabic only; the language is fixed regardless of what
# the AgentSession passes in.
LANGUAGE = "ar"

DEFAULT_MODEL = "CohereLabs/cohere-transcribe-arabic-07-2026"


def _pcm16_to_wav_bytes(pcm: bytes, sample_rate: int = SERVER_SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


class CohereASRStreamingSTT(stt.STT):
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        model: str = DEFAULT_MODEL,
        chunk_ms: int = 250,
        end_silence_ms: int = 500,
        silence_rms: float = 0.008,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Args:
            base_url: vLLM server URL, e.g. "http://localhost:8000".
            model: served model id.
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
        return "cohere-asr-server"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def _transcription_form(self, wav_bytes: bytes, *, stream: bool) -> aiohttp.FormData:
        form = aiohttp.FormData()
        form.add_field("model", self._model)
        form.add_field("language", LANGUAGE)
        form.add_field("response_format", "json")
        form.add_field("stream", "true" if stream else "false")
        form.add_field(
            "file", wav_bytes, filename="audio.wav", content_type="audio/wav"
        )
        return form

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        form = self._transcription_form(frame.to_wav_bytes(), stream=False)

        try:
            async with self._ensure_session().post(
                f"{self._base_url}/v1/audio/transcriptions",
                data=form,
                timeout=aiohttp.ClientTimeout(total=conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(
                        f"cohere-asr transcription failed: {await resp.text()}",
                        status_code=resp.status,
                    )
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise APIConnectionError("failed to reach cohere-asr server") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    language=LANGUAGE,
                    text=(data.get("text") or "").strip(),
                )
            ],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> CohereASRSpeechStream:
        return CohereASRSpeechStream(
            stt=self,
            conn_options=conn_options,
        )


class CohereASRSpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: CohereASRStreamingSTT,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SERVER_SAMPLE_RATE)
        self._cohere_stt = stt
        self._segment_duration = 0.0
        self._silent_chunks = 0
        self._segment_pcm = bytearray()
        self._finalize_after_chunks = max(1, math.ceil(stt._end_silence_ms / stt._chunk_ms))

    async def _stream_segment(self, request_id: str, pcm: bytes) -> str:
        wav = _pcm16_to_wav_bytes(pcm)
        form = self._cohere_stt._transcription_form(wav, stream=True)

        text = ""

        try:
            async with self._cohere_stt._ensure_session().post(
                f"{self._cohere_stt._base_url}/v1/audio/transcriptions",
                data=form,
                timeout=aiohttp.ClientTimeout(total=self._conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(
                        f"cohere-asr streaming request failed: {await resp.text()}",
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

                        text += delta
                        if text.strip():
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(
                                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                    request_id=request_id,
                                    alternatives=[
                                        stt.SpeechData(
                                            language=LANGUAGE,
                                            text=text.strip(),
                                        )
                                    ],
                                )
                            )
        except aiohttp.ClientError as e:
            raise APIConnectionError("failed to reach cohere-asr server") from e

        return text.strip()

    def _append_chunk(self, chunk: bytes, samples: np.ndarray) -> None:
        self._segment_pcm.extend(chunk)
        self._segment_duration += len(samples) / SERVER_SAMPLE_RATE

    async def _finalize_segment(self) -> None:
        if not self._segment_pcm:
            return

        request_id = f"cohere-{uuid.uuid4().hex}"
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
                            language=LANGUAGE,
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
        chunk_samples = SERVER_SAMPLE_RATE * self._cohere_stt._chunk_ms // 1000
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

                    if rms >= self._cohere_stt._silence_rms:
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
