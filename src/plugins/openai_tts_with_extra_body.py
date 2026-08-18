"""OmniVoice TTS — calls /v1/audio/speech and decodes to PCM."""

from __future__ import annotations

import asyncio
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

from src.utils.speech_format import split_for_tts_synthesis
from src.utils.tts_normalize import normalize_for_tts

_FRAME_MS = 20
_BYTES_PER_SAMPLE = 2
_CHUNK_GAP_MS = 60
_CHUNK_REQUEST_GAP_S = float(os.getenv("TTS_CHUNK_REQUEST_GAP_S", "0.35"))
_INITIAL_TRIM_MS = int(os.getenv("OMNIVOICE_INITIAL_TRIM_MS", "260"))
_INITIAL_FADE_MS = int(os.getenv("OMNIVOICE_INITIAL_FADE_MS", "35"))
_OOM_RETRIES = int(os.getenv("TTS_OOM_RETRIES", "2"))
_OOM_RETRY_WAIT_S = float(os.getenv("TTS_OOM_RETRY_WAIT_S", "1.5"))
_OOM_FALLBACK = os.getenv(
    "TTS_OOM_FALLBACK",
    "عذرا، الخدمة الصوتية مشغولة الحين، ممكن تعيد السؤال؟",
)
# Serialize Moss requests so concurrent jobs don't pile onto an already-full GPU.
_tts_lock = asyncio.Lock()


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

        last_error: APIStatusError | None = None
        for attempt in range(_OOM_RETRIES + 1):
            resp = await client.post(url, json=body, headers=self._impl._headers)
            if resp.status_code == 200:
                return _audio_to_pcm(resp.content)

            err = APIStatusError(
                f"TTS returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                request_id=request_id,
                body=resp.content,
            )
            last_error = err
            is_oom = resp.status_code == 500 and "out of memory" in (resp.text or "").lower()
            if is_oom and attempt < _OOM_RETRIES:
                wait = _OOM_RETRY_WAIT_S * (attempt + 1)
                logger.warning(
                    f"TTS CUDA OOM on chunk ({len(chunk)} chars); waiting {wait:.1f}s "
                    f"(attempt {attempt + 1}/{_OOM_RETRIES})"
                )
                await asyncio.sleep(wait)
                continue
            raise err

        assert last_error is not None
        raise last_error

    async def _emit_chunks(
        self,
        client: httpx.AsyncClient,
        chunks: list[str],
        rid: str,
        output_emitter: tts.AudioEmitter,
        target_sr: int,
        frame_bytes: int,
    ) -> float:
        total_duration = 0.0
        for idx, chunk in enumerate(chunks):
            if idx > 0 and _CHUNK_REQUEST_GAP_S > 0:
                await asyncio.sleep(_CHUNK_REQUEST_GAP_S)

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
                f"Moss TTS chunk {idx + 1}/{len(chunks)}: "
                f"{len(chunk)} chars -> {chunk_duration:.1f}s (src {src_sr}Hz)"
            )
        return total_duration

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        rid = f"moss-tts-{uuid.uuid4().hex[:12]}"
        text = await normalize_for_tts(self.input_text)
        if not text:
            output_emitter.flush()
            return

        chunks = split_for_tts_synthesis(text)
        target_sr = self._impl.sample_rate
        frame_bytes = int(target_sr * _FRAME_MS / 1000) * _BYTES_PER_SAMPLE
        logger.info(f"Moss TTS: {len(text)} chars -> {len(chunks)} chunk(s) @ {target_sr}Hz")

        try:
            async with _tts_lock:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                    output_emitter.initialize(
                        request_id=rid,
                        sample_rate=target_sr,
                        num_channels=1,
                        mime_type="audio/pcm",
                    )

                    try:
                        total_duration = await self._emit_chunks(
                            client, chunks, rid, output_emitter, target_sr, frame_bytes
                        )
                    except APIStatusError as err:
                        is_oom = (
                            err.status_code == 500
                            and "out of memory" in (str(err) or "").lower()
                        )
                        if not is_oom or not _OOM_FALLBACK:
                            raise
                        # Don't hammer LiveKit retries with the full long text again.
                        logger.error(
                            "Moss TTS CUDA OOM after retries — speaking short fallback."
                        )
                        await asyncio.sleep(_OOM_RETRY_WAIT_S)
                        fallback_chunks = split_for_tts_synthesis(_OOM_FALLBACK)
                        total_duration = await self._emit_chunks(
                            client,
                            fallback_chunks,
                            rid,
                            output_emitter,
                            target_sr,
                            frame_bytes,
                        )

                    logger.info(
                        f"Moss TTS done: {total_duration:.1f}s audio from "
                        f"{len(chunks)} chunk(s)"
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
        self._api_key = api_key or "no-key-needed"
        self._headers = {"Authorization": f"Bearer {self._api_key}"}

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        # Keep LiveKit retries low — Moss OOM won't clear by replaying the same long text.
        if conn_options.max_retry > 1:
            conn_options = APIConnectOptions(
                max_retry=1,
                retry_interval=conn_options.retry_interval,
                timeout=max(conn_options.timeout, 60.0),
            )
        return _Stream(tts_impl=self, input_text=text, conn_options=conn_options)
