"""Streaming TTS plugin for the local MOSS / sglang-omni server (Rabe3/saudi-tts-4).

The server exposes an OpenAI-style endpoint:
  POST /v1/audio/speech
    {"model", "input", "voice", "response_format": "pcm", "stream": true}

With ``stream: true`` the response is ``Transfer-Encoding: chunked`` and the
first audio arrives in ~90ms, then streams at roughly real-time. Without it the
server buffers the whole utterance and only responds after full generation
(~2.6s for a 5s clip). We always stream so the agent starts speaking fast.

Streamed ``pcm`` audio is 48 kHz, 16-bit, **mono** (MOSS-TTS-Local v1.5 /
MOSS-Audio-Tokenizer-v2 native rate). Note the asymmetry: the non-streaming
``wav`` path returns stereo, but ``stream: true`` + ``stream_format: audio``
returns mono — treating it as stereo halves the sample count and plays back
2x too fast ("chipmunk" audio).

Voice stability: sglang-omni's default audio sampling temperature is 1.7,
which makes the cloned speaker drift badly (random pitch/gender per request).
We pass temperature/top_p/top_k matching the values the finetune was validated
with. The registered voice must also use a long (~20s) reference clip with its
transcript — short references produce unstable clones.
"""

from __future__ import annotations

import hashlib
import re
import time

import aiohttp
try:
    import numpy as np
except Exception:  # pragma: no cover - diagnostics only
    np = None
from loguru import logger

from livekit.agents import tts, utils
from livekit.agents._exceptions import APIConnectionError, APIStatusError
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

SERVER_SAMPLE_RATE = 48000  # raw pcm output rate of the server
SERVER_CHANNELS = 1  # streamed pcm is mono s16 (the wav endpoint is stereo)
HODA_MIN_F0 = 150.0
HODA_MAX_F0 = 280.0
F0_ANALYSIS_SECONDS = 1.5

# Arabic diacritics (tashkeel) + tatweel. Diacritized input destabilizes the
# cloned speaker (measured male-voice outliers in ~40% of requests), so we
# strip them before synthesis.
_ARABIC_DIACRITICS = re.compile(
    "[\\u0610-\\u061a\\u064b-\\u065f\\u0670\\u0640\\u06d6-\\u06dc\\u06df-\\u06e8\\u06ea-\\u06ed]"
)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _preview(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _interesting_headers(headers: aiohttp.typedefs.LooseHeaders | None) -> dict[str, str]:
    if not headers:
        return {}
    keep = {
        "content-type",
        "transfer-encoding",
        "x-request-id",
        "x-moss-debug-request-id",
        "x-moss-gateway-request-id",
        "x-sample-rate",
        "x-channels",
        "x-bit-depth",
    }
    return {
        str(key): str(value)
        for key, value in dict(headers).items()
        if str(key).lower() in keep or str(key).lower().startswith("x-moss")
    }


def _median_f0(audio_bytes: bytes) -> tuple[float | None, float | None, int | None]:
    """Return median F0, RMS, and peak for diagnostic logging."""
    if np is None or not audio_bytes:
        return None, None, None

    aligned = len(audio_bytes) // 2 * 2
    if aligned < int(0.08 * SERVER_SAMPLE_RATE) * 2:
        return None, None, None

    samples = np.frombuffer(audio_bytes[:aligned], dtype=np.int16)
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    peak = int(np.max(np.abs(samples))) if samples.size else 0

    win = int(0.04 * SERVER_SAMPLE_RATE)
    if len(samples) < 2 * win:
        return None, rms, peak

    level = float(np.abs(samples).mean())
    if level == 0.0:
        return None, rms, peak

    f0s: list[float] = []
    for i in range(0, len(samples) - 2 * win, win):
        seg = samples[i : i + 2 * win].astype(np.float64)
        if float(np.abs(seg).mean()) < 0.5 * level:
            continue
        seg -= seg.mean()
        ac = np.correlate(seg, seg, "full")[len(seg) - 1 :]
        lo, hi = int(SERVER_SAMPLE_RATE / 500), int(SERVER_SAMPLE_RATE / 60)
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.4 * ac[0]:
            f0s.append(SERVER_SAMPLE_RATE / lag)

    if len(f0s) < 4:
        return None, rms, peak
    return float(np.median(f0s)), rms, peak


class MOSSTTSPlugin(tts.TTS):
    def __init__(
        self,
        *,
        server_url: str = "http://localhost:8088",
        model: str = "Rabe3/saudi-tts-4",
        voice: str = "hoda",
        ref_audio: str | None = None,
        seed: int | None = None,
        text_temperature: float = 1.0,
        text_top_p: float = 1.0,
        text_top_k: int = 50,
        audio_temperature: float = 0.8,
        audio_top_p: float = 0.95,
        audio_top_k: int = 50,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Args:
            server_url: MOSS/sglang-omni server base URL.
            model: model id served (see GET /v1/models).
            voice: uploaded reference voice name (see GET /v1/audio/voices).
            ref_audio: optional server-side reference audio path. When set, the
                request uses voice=default plus this ref_audio so SGLang does
                not go through its uploaded-voice alias path.
            seed: optional deterministic seed for SGLang sampling.
            text_temperature: sampling temperature for the binary continue/stop head.
            text_top_p: nucleus sampling for the binary continue/stop head.
            text_top_k: top-k sampling for the binary continue/stop head.
            audio_temperature: sampling temperature for the RVQ audio channels.
            audio_top_p: nucleus sampling for the RVQ audio channels.
            audio_top_k: top-k sampling for the RVQ audio channels.
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SERVER_SAMPLE_RATE,
            num_channels=1,
        )
        self._server_url = server_url.rstrip("/")
        self._model = model
        self._voice = voice
        self._ref_audio = ref_audio
        self._seed = seed
        self._text_temperature = text_temperature
        self._text_top_p = text_top_p
        self._text_top_k = text_top_k
        self._audio_temperature = audio_temperature
        self._audio_top_p = audio_top_p
        self._audio_top_k = audio_top_k
        self._session = http_session
        logger.info(
            "MOSS TTS plugin initialized server_url={} model={} voice={} ref_audio={} "
            "seed={} text_temperature={} text_top_p={} text_top_k={} "
            "audio_temperature={} audio_top_p={} audio_top_k={} sample_rate={} channels={}",
            self._server_url,
            self._model,
            self._voice,
            self._ref_audio,
            self._seed,
            self._text_temperature,
            self._text_top_p,
            self._text_top_k,
            self._audio_temperature,
            self._audio_top_p,
            self._audio_top_k,
            SERVER_SAMPLE_RATE,
            SERVER_CHANNELS,
        )

    @property
    def name(self) -> str:
        return "MOSS-TTS"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
            logger.info("MOSS TTS aiohttp session created/reused from LiveKit context")
        return self._session

    def update_options(self, *, voice: str | None = None, model: str | None = None) -> None:
        old_voice = self._voice
        old_model = self._model
        if voice is not None:
            self._voice = voice
        if model is not None:
            self._model = model
        logger.warning(
            "MOSS TTS options updated old_voice={} new_voice={} old_model={} new_model={}",
            old_voice,
            self._voice,
            old_model,
            self._model,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "MOSSChunkedStream":
        logger.info(
            "MOSS TTS synthesize called voice={} model={} text_chars={} text_sha256={} preview={!r}",
            self._voice,
            self._model,
            len(text),
            _text_hash(text),
            _preview(text),
        )
        return MOSSChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class MOSSChunkedStream(tts.ChunkedStream):
    def __init__(
        self, *, tts: MOSSTTSPlugin, input_text: str, conn_options: APIConnectOptions
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = f"moss-{utils.shortuuid()}"
        raw_input = self.input_text
        sanitized_input = _ARABIC_DIACRITICS.sub("", raw_input)
        payload = {
            "model": self._tts._model,
            "input": sanitized_input,
            "voice": "default" if self._tts._ref_audio else self._tts._voice,
            "language": "Arabic",
            "response_format": "pcm",
            "stream": True,
            "stream_format": "audio",
            "text_temperature": self._tts._text_temperature,
            "text_top_p": self._tts._text_top_p,
            "text_top_k": self._tts._text_top_k,
            "audio_temperature": self._tts._audio_temperature,
            "audio_top_p": self._tts._audio_top_p,
            "audio_top_k": self._tts._audio_top_k,
        }
        if self._tts._ref_audio:
            payload["ref_audio"] = self._tts._ref_audio
        if self._tts._seed is not None:
            payload["seed"] = self._tts._seed

        # Buffer to keep s16 samples 2-byte aligned across chunk boundaries.
        remainder = b""
        analysis_audio = bytearray()
        analysis_limit = int(F0_ANALYSIS_SECONDS * SERVER_SAMPLE_RATE) * 2
        started_at = time.perf_counter()
        first_audio_at: float | None = None
        chunks = 0
        bytes_pushed = 0
        max_chunk_bytes = 0
        odd_boundary_events = 0
        endpoint = f"{self._tts._server_url}/v1/audio/speech"

        if self._tts._voice.lower() != "hoda" and not self._tts._ref_audio:
            logger.warning(
                "MOSS TTS request {} is using non-Hoda voice: {}",
                request_id,
                self._tts._voice,
            )
        if self._tts._server_url.endswith(":8088"):
            logger.info(
                "MOSS TTS request {} targets raw SGLang directly on 8088 with one "
                "fixed-seed request; no gateway, no retry, no playback gate",
                request_id,
            )

        logger.info(
            "MOSS TTS request start request_id={} endpoint={} model={} voice={} "
            "raw_chars={} sanitized_chars={} removed_marks={} text_sha256={} "
            "preview={!r} stream={} response_format={} stream_format={} "
            "language={} ref_audio={} seed={} text_temperature={} text_top_p={} "
            "text_top_k={} audio_temperature={} audio_top_p={} audio_top_k={} "
            "diagnostic_f0_min={} diagnostic_f0_max={} timeout={}",
            request_id,
            endpoint,
            payload["model"],
            payload["voice"],
            len(raw_input),
            len(sanitized_input),
            len(raw_input) - len(sanitized_input),
            _text_hash(sanitized_input),
            _preview(sanitized_input),
            payload["stream"],
            payload["response_format"],
            payload["stream_format"],
            payload["language"],
            payload.get("ref_audio"),
            payload.get("seed"),
            payload["text_temperature"],
            payload["text_top_p"],
            payload["text_top_k"],
            payload["audio_temperature"],
            payload["audio_top_p"],
            payload["audio_top_k"],
            HODA_MIN_F0,
            HODA_MAX_F0,
            self._conn_options.timeout,
        )

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=SERVER_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )

        try:
            async with self._tts._ensure_session().post(
                endpoint,
                json=payload,
                headers={
                    "X-MOSS-Debug-Request-ID": request_id,
                    "X-Request-ID": request_id,
                },
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=self._conn_options.timeout,
                    sock_read=self._conn_options.timeout,
                ),
            ) as resp:
                logger.info(
                    "MOSS TTS response headers request_id={} status={} elapsed={:.3f}s headers={}",
                    request_id,
                    resp.status,
                    time.perf_counter() - started_at,
                    _interesting_headers(resp.headers),
                )
                if resp.status != 200:
                    error_body = await resp.text()
                    logger.error(
                        "MOSS TTS request failed request_id={} status={} body={!r}",
                        request_id,
                        resp.status,
                        _preview(error_body, limit=600),
                    )
                    raise APIStatusError(
                        f"MOSS TTS request failed: {error_body}",
                        status_code=resp.status,
                    )

                async for chunk in resp.content.iter_chunked(8192):
                    if not chunk:
                        continue
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
                        logger.info(
                            "MOSS TTS first audio request_id={} first_chunk_bytes={} ttfb={:.3f}s",
                            request_id,
                            len(chunk),
                            first_audio_at - started_at,
                        )
                    if remainder:
                        chunk = remainder + chunk
                        odd_boundary_events += 1
                    frame_bytes = (len(chunk) // 2) * 2
                    remainder = chunk[frame_bytes:]
                    if remainder:
                        odd_boundary_events += 1
                    if frame_bytes == 0:
                        continue
                    aligned_chunk = chunk[:frame_bytes]
                    chunks += 1
                    bytes_pushed += frame_bytes
                    max_chunk_bytes = max(max_chunk_bytes, frame_bytes)
                    if len(analysis_audio) < analysis_limit:
                        needed = analysis_limit - len(analysis_audio)
                        analysis_audio.extend(aligned_chunk[:needed])
                    if chunks == 1 or chunks % 25 == 0:
                        logger.info(
                            "MOSS TTS stream progress request_id={} chunks={} bytes={} "
                            "audio_seconds={:.3f} elapsed={:.3f}s remainder_bytes={}",
                            request_id,
                            chunks,
                            bytes_pushed,
                            bytes_pushed / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2),
                            time.perf_counter() - started_at,
                            len(remainder),
                        )
                    output_emitter.push(aligned_chunk)

            output_emitter.flush()
            elapsed = time.perf_counter() - started_at
            f0, rms, peak = _median_f0(bytes(analysis_audio))
            audio_seconds = bytes_pushed / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2)
            logger.info(
                "MOSS TTS request complete request_id={} seed={} chunks={} bytes={} "
                "audio_seconds={:.3f} elapsed={:.3f}s first_audio_latency={} "
                "max_chunk_bytes={} odd_boundary_events={} final_remainder_bytes={} "
                "analysis_seconds={:.3f} median_f0_hz={} rms={} peak={} "
                "gateway_bypass=true retry_count=0 gate_action=none",
                request_id,
                payload.get("seed"),
                chunks,
                bytes_pushed,
                audio_seconds,
                elapsed,
                f"{first_audio_at - started_at:.3f}s" if first_audio_at else "none",
                max_chunk_bytes,
                odd_boundary_events,
                len(remainder),
                len(analysis_audio) / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2),
                f"{f0:.1f}" if f0 is not None else "unknown",
                f"{rms:.1f}" if rms is not None else "unknown",
                peak if peak is not None else "unknown",
            )
            if f0 is None:
                logger.warning(
                    "MOSS TTS diagnostic could not estimate first-audio F0 request_id={}; "
                    "audio was already emitted because fixed-seed direct mode has no gate",
                    request_id,
                )
            elif not (HODA_MIN_F0 <= f0 <= HODA_MAX_F0):
                logger.error(
                    "MOSS TTS diagnostic out-of-range request_id={} seed={} median_f0_hz={:.1f} "
                    "expected_range={:.0f}-{:.0f}; audio was emitted because fixed-seed direct "
                    "mode has no gate/retry",
                    request_id,
                    payload.get("seed"),
                    f0,
                    HODA_MIN_F0,
                    HODA_MAX_F0,
                )
        except aiohttp.ClientError as e:
            logger.exception(
                "MOSS TTS network/client failure request_id={} endpoint={} elapsed={:.3f}s",
                request_id,
                endpoint,
                time.perf_counter() - started_at,
            )
            raise APIConnectionError("failed to reach MOSS TTS server") from e
