"""Streaming TTS plugin for the hosted VoxCPM2 server.

Ported from Digital_Asset_live_kit with food-catalog vocabulary removed.
Key improvements over the clause-splitting version:
- Sends the whole utterance to VoxCPM in one request (natural prosody)
- Intelligent tail trim: cuts trailing breath without clipping real speech
- _TailHoldback: buffers last 480ms so flush can trim without un-emitting audio
- strip_control_tokens: drops chat-template artifacts before synthesis
"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid

import aiohttp
try:
    import numpy as np
except Exception:
    np = None

from loguru import logger
from livekit.agents import tts, utils
from livekit.agents._exceptions import APIConnectionError, APIStatusError
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

SERVER_SAMPLE_RATE = 48000
SERVER_CHANNELS = 1

VOICE_MIN_F0 = 135.0
VOICE_MAX_F0 = 340.0
F0_ANALYSIS_SECONDS = 1.5

# Tail-trim constants (from Digital_Asset_live_kit voxcpm_tts_plugin.py).
HOLD_TAIL_MS = 360
BREATH_FRAME_MS = 10
BREATH_RATIO = 0.12
POST_PAUSE_MS = 70
TRAILING_SILENCE_MS = 60
FADE_MS = 24
DECAY_TAIL_MS = 110
DECAY_RATIO = 0.35
DECAY_PEAK_RATIO = 0.22

# Chat-template tokens that leak out of the LLM.
_CONTROL_TOKENS = re.compile(r"<\|?[^<>]{0,80}?\|?>")
_TEMPLATE_WORDS = re.compile(
    r"\b(?:thought|analysis|commentary|final|channel|assistant|system)\b",
    re.IGNORECASE,
)

# Non-phonetic Arabic marks that are never pronounced.
_NON_PHONETIC_MARKS = re.compile(
    "[\u0670\u0640\u0610-\u061a\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed]"
)
_TASHKEEL = "\\u064b-\\u0652"
_ALL_REMOVABLE_MARKS = re.compile(
    "[" + _TASHKEEL + "\u0670\u0640\u0610-\u061a\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed]"
)

import os as _os
STRIP_TASHKEEL = _os.getenv("TTS_STRIP_TASHKEEL", "0").strip().lower() in {"1", "true", "yes", "on"}


def normalize_arabic(text: str, *, strip_tashkeel: bool | None = None) -> str:
    """Remove non-phonetic marks, preserve letter identity (NFC first)."""
    if strip_tashkeel is None:
        strip_tashkeel = STRIP_TASHKEEL
    pattern = _ALL_REMOVABLE_MARKS if strip_tashkeel else _NON_PHONETIC_MARKS
    return pattern.sub("", unicodedata.normalize("NFC", text))


def strip_control_tokens(text: str) -> str:
    """Drop chat-template artifacts so the voice never speaks them."""
    cleaned = _CONTROL_TOKENS.sub(" ", text)
    cleaned = _TEMPLATE_WORDS.sub(" ", cleaned)
    if re.search(r"\bcall\s*:", cleaned, re.IGNORECASE):
        return ""
    if re.search(r"<\s*tool_call\b", cleaned, re.IGNORECASE):
        return ""
    return " ".join(cleaned.split())


# ── Tail trim ────────────────────────────────────────────────────────────────

def _pcm16(pcm: bytes) -> "np.ndarray":
    aligned = (len(pcm) // 2) * 2
    return np.frombuffer(pcm[:aligned], dtype="<i2")


def _pack_pcm(samples: "np.ndarray") -> bytes:
    if samples.size == 0:
        return b""
    return np.clip(samples, -32767, 32767).astype("<i2").tobytes()


def trim_trailing_artifacts(pcm: bytes, sample_rate: int = SERVER_SAMPLE_RATE) -> bytes:
    """Drop end-of-utterance breath and a ghost word after a pause."""
    if np is None or not pcm:
        return pcm
    samples = _pcm16(pcm).astype(np.float32)
    if samples.size < int(sample_rate * 0.08):
        return pcm

    frame = max(1, int(sample_rate * BREATH_FRAME_MS / 1000))
    n_frames = len(samples) // frame
    if n_frames < 4:
        return pcm

    rms = np.array(
        [float(np.sqrt(np.mean(samples[i * frame:(i + 1) * frame] ** 2))) for i in range(n_frames)],
        dtype=np.float32,
    )
    peak = float(rms.max()) if rms.size else 0.0
    if peak <= 0:
        return b""
    voiced = rms >= (BREATH_RATIO * peak)

    last_voiced = int(np.max(np.nonzero(voiced)[0])) if voiced.any() else -1
    if last_voiced < 0:
        return b""
    cut_at = last_voiced

    pause_frames = max(1, int(POST_PAUSE_MS / BREATH_FRAME_MS))
    window_start = max(0, n_frames - int(HOLD_TAIL_MS / BREATH_FRAME_MS) - 2)
    silent_run = 0
    for i in range(last_voiced, window_start - 1, -1):
        if voiced[i]:
            if silent_run >= pause_frames:
                cut_at = i
                break
            silent_run = 0
        else:
            silent_run += 1

    decay_frames = max(2, int(DECAY_TAIL_MS / BREATH_FRAME_MS))
    if cut_at + 1 >= decay_frames * 2:
        tail_mean = float(rms[cut_at + 1 - decay_frames:cut_at + 1].mean())
        prev_mean = float(rms[cut_at + 1 - 2 * decay_frames:cut_at + 1 - decay_frames].mean())
        if (
            prev_mean > 0
            and tail_mean < DECAY_RATIO * prev_mean
            and tail_mean < DECAY_PEAK_RATIO * peak
        ):
            cut_at = max(0, cut_at - decay_frames)

    end = min(len(samples), (cut_at + 1) * frame)
    while end > frame:
        tail = samples[max(0, end - frame):end]
        if float(np.sqrt(np.mean(tail ** 2))) >= BREATH_RATIO * peak:
            break
        end -= frame // 2

    out = samples[:end]
    fade = min(len(out), int(sample_rate * FADE_MS / 1000))
    if fade > 1:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return _pack_pcm(out)


class _TailHoldback:
    """Keep the last HOLD_TAIL_MS unemitted so flush can trim it."""

    def __init__(self, sample_rate: int, hold_ms: int = HOLD_TAIL_MS) -> None:
        self._hold = int(sample_rate * hold_ms / 1000) * 2
        self._buf = bytearray()

    def push(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        self._buf.extend(pcm)
        extra = len(self._buf) - self._hold
        if extra < 2:
            return b""
        extra -= extra % 2
        emit = bytes(self._buf[:extra])
        del self._buf[:extra]
        return emit

    def flush(self, sample_rate: int) -> bytes:
        trimmed = trim_trailing_artifacts(bytes(self._buf), sample_rate)
        self._buf.clear()
        return trimmed


# ── Plugin ───────────────────────────────────────────────────────────────────

class VoxCPMTTSPlugin(tts.TTS):
    def __init__(
        self,
        *,
        server_url: str,
        model: str = "VoxCPM2",
        voice: str = "hoda",
        seed: int | None = None,
        language: str = "Arabic",
        timeout_s: float = 120.0,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SERVER_SAMPLE_RATE,
            num_channels=SERVER_CHANNELS,
        )
        self._server_url = server_url.rstrip("/")
        if self._server_url.endswith("/v1"):
            self._server_url = self._server_url[:-len("/v1")]
        self._model = model
        self._voice = voice
        self._seed = seed
        self._language = language
        self._timeout_s = timeout_s
        self._session = http_session
        logger.info(
            "VoxCPM TTS initialized server_url={} model={} voice={} seed={}",
            self._server_url, self._model, self._voice, self._seed,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "voxcpm"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "VoxCPMChunkedStream":
        return VoxCPMChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class VoxCPMChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: VoxCPMTTSPlugin,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = f"voxcpm-{uuid.uuid4().hex[:12]}"
        raw_input = self.input_text
        sanitized = strip_control_tokens(normalize_arabic(raw_input))

        if not sanitized.strip():
            logger.warning("VoxCPM TTS skipped empty input request_id={}", request_id)
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=SERVER_SAMPLE_RATE,
                num_channels=SERVER_CHANNELS,
                mime_type="audio/pcm",
            )
            output_emitter.push(b"\x00\x00" * int(SERVER_SAMPLE_RATE * 0.04))
            output_emitter.flush()
            return

        payload = {
            "model": self._tts._model,
            "input": sanitized,
            "voice": self._tts._voice,
            "language": self._tts._language,
            "response_format": "pcm",
            "stream": True,
            "stream_format": "audio",
        }
        if self._tts._seed is not None:
            payload["seed"] = self._tts._seed

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=SERVER_SAMPLE_RATE,
            num_channels=SERVER_CHANNELS,
            mime_type="audio/pcm",
        )

        endpoint = f"{self._tts._server_url}/v1/audio/speech"
        started_at = time.perf_counter()
        first_audio_at: float | None = None
        remainder = b""
        bytes_received = 0
        holdback = _TailHoldback(SERVER_SAMPLE_RATE)

        logger.info(
            "VoxCPM TTS request start request_id={} chars={} preview={!r}",
            request_id, len(sanitized), sanitized[:80],
        )

        try:
            async with self._tts._ensure_session().post(
                endpoint,
                json=payload,
                headers={"X-Request-ID": request_id},
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=self._conn_options.timeout,
                    sock_read=self._conn_options.timeout,
                ),
            ) as resp:
                if resp.status != 200:
                    error_body = await resp.text()
                    logger.error(
                        "VoxCPM TTS request failed request_id={} status={} body={!r}",
                        request_id, resp.status, error_body[:300],
                    )
                    raise APIStatusError(
                        f"VoxCPM TTS request failed: {error_body}",
                        status_code=resp.status,
                    )

                async for chunk in resp.content.iter_chunked(8192):
                    if not chunk:
                        continue
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
                        logger.info(
                            "VoxCPM TTS first audio request_id={} ttfb={:.3f}s",
                            request_id, first_audio_at - started_at,
                        )
                    if remainder:
                        chunk = remainder + chunk
                    frame_bytes = (len(chunk) // 2) * 2
                    remainder = chunk[frame_bytes:]
                    if frame_bytes == 0:
                        continue
                    aligned = chunk[:frame_bytes]
                    bytes_received += frame_bytes
                    emitted = holdback.push(aligned)
                    if emitted:
                        output_emitter.push(emitted)

            tail = holdback.flush(SERVER_SAMPLE_RATE)
            if tail:
                output_emitter.push(tail)

            trailing = b"\x00\x00" * int(SERVER_SAMPLE_RATE * TRAILING_SILENCE_MS / 1000)
            output_emitter.push(trailing)
            output_emitter.flush()

            elapsed = time.perf_counter() - started_at
            audio_s = bytes_received / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2)
            logger.info(
                "VoxCPM TTS complete request_id={} audio_s={:.3f} elapsed={:.3f}s ttfb={}",
                request_id, audio_s, elapsed,
                f"{first_audio_at - started_at:.3f}s" if first_audio_at else "none",
            )

        except aiohttp.ClientError as e:
            logger.exception(
                "VoxCPM TTS network failure request_id={} elapsed={:.3f}s",
                request_id, time.perf_counter() - started_at,
            )
            raise APIConnectionError("failed to reach VoxCPM TTS server") from e
