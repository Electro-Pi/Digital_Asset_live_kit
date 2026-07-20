"""OmniVoice TTS — calls /v1/audio/speech and decodes to PCM."""

from __future__ import annotations

import io
import os
import uuid

import httpx
import numpy as np
import soundfile as sf
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from loguru import logger

from src.utils.speech_format import format_for_speech, split_for_tts_synthesis

_FRAME_MS = 20
_BYTES_PER_SAMPLE = 2
_CHUNK_GAP_MS = 60
_INITIAL_TRIM_MS = int(os.getenv("OMNIVOICE_INITIAL_TRIM_MS", "260"))
_INITIAL_FADE_MS = int(os.getenv("OMNIVOICE_INITIAL_FADE_MS", "35"))


def sanitize_text(text: str) -> str:
    return format_for_speech(text)


def _push_aligned(buf: bytearray, data: bytes, emitter: tts.AudioEmitter, frame_bytes: int) -> bytearray:
    buf.extend(data)
    while len(buf) >= frame_bytes:
        emitter.push(bytes(buf[:frame_bytes]))
        del buf[:frame_bytes]
    return buf


def _pcm_to_array(pcm: bytes) -> np.ndarray:
    if len(pcm) < 2:
        return np.array([], dtype=np.int16)
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return np.frombuffer(pcm, dtype=np.int16)


def _resample_pcm(pcm: bytes, src_sr: int, dst_sr: int) -> bytes:
    if src_sr == dst_sr or not pcm:
        return pcm
    audio = _pcm_to_array(pcm).astype(np.float32)
    if audio.size == 0:
        return b""
    out_len = max(1, int(round(audio.size * dst_sr / src_sr)))
    x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.int16).tobytes()


def _audio_to_pcm(audio_bytes: bytes) -> tuple[bytes, int]:
    """Decode audio bytes (wav/mp3) → 16-bit mono PCM."""
    audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="int16", always_2d=False)
    if audio.ndim == 2:
        audio = audio.mean(axis=1).astype(np.int16)
    return audio.tobytes(), int(sr)


def _trim_leading_pcm(pcm: bytes, sample_rate: int, threshold: int = 400) -> bytes:
    audio = _pcm_to_array(pcm)
    if audio.size == 0:
        return b""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return pcm
    start = max(0, int(loud[0]) - int(sample_rate * 0.01))
    return audio[start:].tobytes()


def _trim_trailing_pcm(pcm: bytes, sample_rate: int, threshold: int = 400) -> bytes:
    audio = _pcm_to_array(pcm)
    if audio.size == 0:
        return b""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return pcm
    end = min(audio.size, int(loud[-1]) + int(sample_rate * 0.015))
    return audio[:end].tobytes()


def _drop_initial_artifact(pcm: bytes, sample_rate: int) -> bytes:
    """Remove OmniVoice's occasional non-text syllable at the start."""
    audio = _pcm_to_array(pcm).copy()
    if audio.size == 0 or _INITIAL_TRIM_MS <= 0:
        return pcm

    trim_samples = min(audio.size, int(sample_rate * _INITIAL_TRIM_MS / 1000))
    audio = audio[trim_samples:]
    if audio.size == 0:
        return b""

    fade_samples = min(audio.size, int(sample_rate * _INITIAL_FADE_MS / 1000))
    if fade_samples > 0:
        fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        faded = (audio[:fade_samples].astype(np.float32) * fade).astype(np.int16)
        audio[:fade_samples] = faded

    return audio.tobytes()


def _silence_bytes(duration_ms: int, sample_rate: int) -> bytes:
    samples = int(sample_rate * duration_ms / 1000)
    return b"\x00\x00" * samples


def _emit_pcm(
    pcm: bytes,
    sample_rate: int,
    output_emitter: tts.AudioEmitter,
    frame_bytes: int,
) -> float:
    if not pcm:
        return 0.0
    buf = bytearray()
    for i in range(0, len(pcm), frame_bytes * 100):
        buf = _push_aligned(buf, pcm[i : i + frame_bytes * 100], output_emitter, frame_bytes)
    if buf:
        output_emitter.push(bytes(buf))
    return len(_pcm_to_array(pcm)) / sample_rate


class _Stream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts_impl: "OpenAITTSWithSpeechExtraBody",
        input_text: str,
        conn_options: APIConnectOptions,
    ):
        super().__init__(tts=tts_impl, input_text=input_text, conn_options=conn_options)
        self._impl = tts_impl

    async def _synthesize_chunk(
        self,
        client: httpx.AsyncClient,
        chunk: str,
        request_id: str,
    ) -> tuple[bytes, int]:
        url = f"{self._impl._base_url}/v1/audio/speech"
        body = {
            "model": self._impl._model,
            "input": chunk,
            "voice": self._impl._voice,
            "response_format": self._impl._response_format,
            **self._impl._extra_body,
        }
        resp = await client.post(url, json=body)
        if resp.status_code != 200:
            raise APIStatusError(
                f"OmniVoice returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                request_id=request_id,
                body=resp.content,
            )
        return _audio_to_pcm(resp.content)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        rid = f"omnivoice-{uuid.uuid4().hex[:12]}"
        text = sanitize_text(self.input_text)
        if not text:
            output_emitter.flush()
            return

        chunks = split_for_tts_synthesis(text)
        target_sr = self._impl.sample_rate
        frame_bytes = int(target_sr * _FRAME_MS / 1000) * _BYTES_PER_SAMPLE
        logger.info(f"OmniVoice TTS: {len(text)} chars -> {len(chunks)} chunk(s) @ {target_sr}Hz")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                output_emitter.initialize(
                    request_id=rid,
                    sample_rate=target_sr,
                    num_channels=1,
                    mime_type="audio/pcm",
                )

                total_duration = 0.0
                for idx, chunk in enumerate(chunks):
                    pcm, src_sr = await self._synthesize_chunk(client, chunk, rid)
                    pcm = _resample_pcm(pcm, src_sr, target_sr)

                    if idx == 0:
                        pcm = _trim_leading_pcm(pcm, target_sr)
                        pcm = _drop_initial_artifact(pcm, target_sr)
                    else:
                        pcm = _trim_leading_pcm(pcm, target_sr, threshold=350)

                    if idx < len(chunks) - 1:
                        pcm = _trim_trailing_pcm(pcm, target_sr)
                        pcm += _silence_bytes(_CHUNK_GAP_MS, target_sr)

                    chunk_duration = _emit_pcm(pcm, target_sr, output_emitter, frame_bytes)
                    total_duration += chunk_duration
                    logger.debug(
                        f"OmniVoice chunk {idx + 1}/{len(chunks)}: "
                        f"{len(chunk)} chars -> {chunk_duration:.1f}s (src {src_sr}Hz)"
                    )

                logger.info(
                    f"OmniVoice TTS done: {total_duration:.1f}s audio from {len(chunks)} chunk(s)"
                )
                output_emitter.flush()

        except httpx.TimeoutException:
            raise APITimeoutError() from None
        except (APIStatusError, APIConnectionError):
            raise
        except Exception:
            raise APIConnectionError() from None


class OpenAITTSWithSpeechExtraBody(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str,
        model: str = "omnivoice",
        voice: str = "alloy",
        speech_extra_body=None,
        api_key="no-key-needed",
        response_format="wav",
        speed=1.0,
        **_,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._voice = voice
        self._response_format = str(response_format).lower()
        self._extra_body = dict(speech_extra_body) if speech_extra_body else {}

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return _Stream(tts_impl=self, input_text=text, conn_options=conn_options)
