"""Streaming TTS plugin for the hosted VoxCPM2 server (openbmb/VoxCPM2 on vLLM).

The server exposes an OpenAI-style endpoint:
  POST /v1/audio/speech
    {"model", "input", "voice", "response_format": "pcm", "stream": true,
     "stream_format": "audio"}

With ``stream: true`` the response is ``Transfer-Encoding: chunked`` and the
first audio arrives in ~75ms on an idle server, then streams faster than
real-time (a 3.7s utterance completes in ~0.46s). Without it the server buffers
the whole utterance. We always stream so the agent starts speaking fast.

Audio is 48 kHz, 16-bit, **mono** — the same format the previous MOSS server
emitted, so the rest of the pipeline is unchanged. Unlike MOSS, VoxCPM2 is mono
on *both* paths: its non-streaming ``wav`` is mono too, not stereo.

Voices live server-side; ``GET /v1/audio/voices`` currently lists ``default``
and ``hoda``. ``hoda`` is the precomputed production female voice (measured
median F0 186 Hz). Per-request cloning still works by sending ``voice=default``
plus ``ref_audio``/``ref_text``, which is how the male speaker is produced from
``ref-audio/male.wav`` (measured median F0 115 Hz).

Unknown fields are accepted and ignored rather than rejected, so a stale voice
name is the only loud failure: an unregistered ``voice`` returns 400 and an
unknown ``model`` returns 404. That is worth relying on — it means a
misconfigured voice fails visibly instead of silently falling back to the
male ``default`` speaker.

Note this server does NOT take the MOSS/sglang-omni sampling knobs
(``text_temperature``, ``audio_temperature``, ``audio_top_p``, …). They are not
in its request schema; sending them returns 200 with no effect. They are
deliberately absent here rather than passed and ignored.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata

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
SERVER_CHANNELS = 1  # VoxCPM2 is mono on both the stream and wav paths
# Expected pitch band for the production `hoda` voice — logged, not gated.
#
# The point of this check is catching a drift to the male speaker: this server's
# `default` voice measures ~115Hz, versus ~186Hz for `hoda`.
#
# Both bounds are loose because F0 is estimated from only the first
# F0_ANALYSIS_SECONDS of the stream, and that onset window is a poor proxy for
# the utterance — a full-utterance measurement of `hoda` spans 154-289Hz at the
# 10th/90th percentile around its 186Hz median. Tighter bounds fire on ordinary
# turns and train you to ignore the log.
VOICE_MIN_F0 = 135.0
VOICE_MAX_F0 = 340.0
F0_ANALYSIS_SECONDS = 1.5

# Speaking rate.
#
# The server's own `speed` knob is a resampler: it rescales the sample clock, so
# 0.9x stretches the waveform *and* drags pitch and every formant down 10%. The
# cloned speaker becomes a different, deeper one and drifts out of the F0 band
# checked above — exactly the failure this plugin already spends effort avoiding.
#
# So the rate change happens here instead, with WSOLA (see _WSOLAStretcher):
# duration changes by repeating or dropping whole pitch periods, and F0 and the
# formants stay bit-for-bit where the model put them. Cost is one frame
# (STRETCH_FRAME_MS) of extra latency and a few percent of a core.
MIN_SPEED = 0.5
MAX_SPEED = 2.0
STRETCH_FRAME_MS = 30.0
# Half a pitch period is the minimum useful search range; at the ~186Hz of
# hoda that is 2.7ms, and 6ms covers voices down to ~85Hz.
STRETCH_SEARCH_MS = 6.0

# Text normalization before synthesis.
#
# Tashkeel is deliberately PRESERVED, and reaches the TTS server intact. The
# history is worth keeping straight, because it has been reversed three times:
#
# Diacritics were once deleted on the theory that they destabilized the cloned
# speaker (~40% male-voice outliers). That was a misdiagnosis — the real cause
# was the old MOSS/sglang-omni server's default audio_temperature of 1.7, and
# with tuned sampling the instability was gone. Re-measured over 30 requests
# spanning diacritized and undiacritized input: F0 stayed in the female band in
# every one. On that evidence stripping was removed, because diacritics do carry
# vowel information the model would otherwise have to guess.
#
# Stripping then came back with a prompt that emitted bare Arabic, so the
# regex had nothing to do in the normal case. Both are now reversed together:
# the prompt asks for word-internal tashkeel with bare word endings (see
# prompts/agent_prompt.py) precisely so the model stops guessing vowels, which
# only works if the marks survive to the request body. The marks are for the
# voice, not the reader — the playground transcript strips them for display
# (frontend src/lib/arabic.ts).
#
# What remains removed carries no pronunciation at all:
#   * U+0640 tatweel — a typographic stretch, never phonetic
#   * U+0610-U+061A  — honorific annotations (e.g. "sallallahu alayhi wasallam")
#   * U+06D6-U+06ED  — Quranic recitation/annotation marks
#
# This matches normalizeArabic() in the frontend's src/lib/upstream.ts, so the
# playground TTS tab and the live agent pronounce the same text identically.
_NON_PHONETIC_MARKS = re.compile(
    "[\\u0640\\u0610-\\u061a\\u06d6-\\u06dc\\u06df-\\u06e8\\u06ea-\\u06ed]"
)

# A mark cluster on a word's last letter — one not followed by another Arabic
# letter. Marks are excluded from the lookahead as well as letters, which is
# what stops the greedy ``+`` from backtracking into the middle of a cluster and
# calling an interior shadda "final".
#
# The prompt asks the LLM for bare word endings, but asking is not enforcing: a
# model that vocalizes السَّلَامُ عَلَيْكُمْ reads it as recitation rather than
# speech. This makes the rule hold whatever the LLM actually sent.
_WORD_FINAL_MARKS = re.compile(
    "[\\u064b-\\u0652\\u0670]+"
    "(?![\\u0621-\\u064a\\u0671-\\u06d3\\u064b-\\u0652\\u0670])"
)


def _keep_shadda_only(match: "re.Match[str]") -> str:
    """A final shadda is gemination (مُسْتَمِرّ), not an ending, so it survives."""
    return "ّ" if "ّ" in match.group(0) else ""


def normalize_arabic(text: str) -> str:
    """Keep word-internal tashkeel, drop non-phonetic marks and the i'rab.

    The NFC pass is load-bearing. Hamza and maddah also exist as COMBINING
    marks (U+0653-U+0655), so decomposed text spells أ as ا+U+0654. Deleting
    that range silently rewrites letters — رئيس becomes رييس, مؤسسة becomes
    موسسة, القرآن becomes القران — and those really are pronounced wrong.
    Composing first turns those sequences back into single letters, which is
    why U+0653-U+0655 stays out of the character class above: those marks are
    letter identity, not vowels, and must survive the strip.
    """
    composed = _NON_PHONETIC_MARKS.sub("", unicodedata.normalize("NFC", text))
    return _WORD_FINAL_MARKS.sub(_keep_shadda_only, composed)


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
        "x-sample-rate",
        "x-channels",
        "x-bit-depth",
    }
    return {
        str(key): str(value)
        for key, value in dict(headers).items()
        if str(key).lower() in keep
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


class _WSOLAStretcher:
    """Streaming, pitch-preserving time-stretch for mono s16 PCM.

    ``speed`` is a playback-rate multiplier: 0.9 makes the utterance 1/0.9 longer
    (slower) while leaving pitch and timbre untouched.

    Each output frame is cut from the input near a nominal analysis position,
    then nudged within a small search window to whichever offset best continues
    the previous frame, and overlap-added at a fixed synthesis hop. That search
    is the whole point: a plain overlap-add at the same hops splices waveforms
    mid-pitch-period and produces the classic warble on sustained Arabic vowels.

    The nominal positions are a fixed grid (frame m at ``m * hop_out * speed``)
    and the search only perturbs around it. Advancing the grid from the chosen
    offset instead looks equivalent but silently disables the stretch: the
    previous frame's exact continuation is a perfect correlation match and sits
    only ``hop_out * |1 - speed|`` samples inside the search window, so every
    frame snaps to it and the output comes back at 1.0x. Against a fixed grid
    that offset can only win for a few frames before it falls outside the
    window, which is what makes the algorithm insert or drop a whole pitch
    period at a time — the behavior that keeps it artifact-free.

    Fed chunk by chunk; ``push`` returns whatever output is complete so far and
    keeps the rest buffered, so it does not delay the stream beyond one frame.
    Input must be a whole number of samples (even byte count).
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        speed: float,
        frame_ms: float = STRETCH_FRAME_MS,
        search_ms: float = STRETCH_SEARCH_MS,
    ) -> None:
        self._frame = max(4, int(sample_rate * frame_ms / 1000.0) // 2 * 2)
        self._hop_out = self._frame // 2
        self._hop_in = self._hop_out * speed  # kept fractional: it is a grid step
        self._search = max(1, int(sample_rate * search_ms / 1000.0))
        # A periodic Hann sums to exactly 1.0 at 50% overlap, so overlap-add is
        # gain-flat and needs no normalization pass.
        self._window = np.hanning(self._frame + 1)[: self._frame].astype(np.float32)
        self._reset()

    def _reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._base = 0  # absolute sample index of self._buf[0]
        self._tail = np.zeros(self._hop_out, dtype=np.float32)
        self._prev: int | None = None  # absolute start of the last frame taken
        self._frames = 0  # frames emitted, i.e. the index into the nominal grid

    def push(self, pcm: bytes) -> bytes:
        if pcm:
            self._buf = np.concatenate(
                (self._buf, np.frombuffer(pcm, dtype="<i2").astype(np.float32))
            )
        return self._pack(self._advance())

    def flush(self) -> bytes:
        parts = self._advance()
        if self._prev is None:
            # Never accumulated a full frame; pass the scrap through unstretched.
            parts.append(self._buf.copy())
        else:
            # self._tail is the fading-out second half of the last frame. Its
            # natural continuation in the source starts at prev + hop_out, and
            # the periodic Hann guarantees window[:hop_out] is its exact
            # complement — so this fades cleanly into the untouched remainder.
            rest = self._slice(self._prev + self._hop_out, self._end())
            merged = self._tail.copy()
            overlap = min(len(merged), len(rest))
            merged[:overlap] += rest[:overlap] * self._window[:overlap]
            parts.append(merged)
            if len(rest) > len(merged):
                parts.append(rest[len(merged) :])
        self._reset()
        return self._pack(parts)

    def _end(self) -> int:
        return self._base + len(self._buf)

    def _slice(self, start: int, stop: int) -> "np.ndarray":
        return self._buf[start - self._base : stop - self._base]

    def _advance(self) -> list["np.ndarray"]:
        parts: list[np.ndarray] = []
        while True:
            if self._prev is None:
                if self._end() < self._frame:
                    break
                offset = 0
            else:
                nominal = int(round(self._frames * self._hop_in))
                lo = max(self._prev + 1, nominal - self._search)
                hi = max(lo, nominal + self._search)
                if self._end() < hi + self._frame:
                    break
                offset = self._best_offset(lo, hi)
            frame = self._slice(offset, offset + self._frame) * self._window
            parts.append(self._tail + frame[: self._hop_out])
            self._tail = frame[self._hop_out :].copy()
            self._prev = offset
            self._frames += 1
            # The next frame reads no earlier than the current one, and its
            # template starts at offset + hop_out.
            self._trim(offset)
        return parts

    def _best_offset(self, lo: int, hi: int) -> int:
        # Similarity against the previous frame's natural continuation, i.e.
        # what the source would have played had we not resynchronized.
        template = self._slice(self._prev + self._hop_out, self._prev + self._frame)
        window = self._slice(lo, hi + self._hop_out)
        corr = np.correlate(window, template, mode="valid")
        # Normalizing by candidate energy stops the search from always snapping
        # to the loudest offset rather than the best-aligned one.
        energy = np.convolve(
            window * window, np.ones(len(template), dtype=np.float32), mode="valid"
        )
        return lo + int(np.argmax(corr / np.sqrt(energy + 1e-9)))

    def _trim(self, keep_from: int) -> None:
        drop = keep_from - self._base
        if drop > 0:
            self._buf = self._buf[drop:]
            self._base = keep_from

    @staticmethod
    def _pack(parts: list["np.ndarray"]) -> bytes:
        parts = [p for p in parts if len(p)]
        if not parts:
            return b""
        data = parts[0] if len(parts) == 1 else np.concatenate(parts)
        return np.clip(np.rint(data), -32768, 32767).astype("<i2").tobytes()


class VoxCPMTTSPlugin(tts.TTS):
    def __init__(
        self,
        *,
        server_url: str = "http://localhost:16326",
        model: str = "VoxCPM2",
        voice: str = "hoda",
        ref_audio: str | None = None,
        ref_text: str | None = None,
        seed: int | None = None,
        speed: float = 1.0,
        expected_voice: str = "hoda",
        voice_min_f0: float = VOICE_MIN_F0,
        voice_max_f0: float = VOICE_MAX_F0,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Args:
            server_url: VoxCPM2 server base URL, WITHOUT the /v1 suffix — the
                endpoint path is appended here.
            model: model id served (see GET /v1/models); an unknown id is a 404.
            voice: server-side voice name (see GET /v1/audio/voices). Currently
                "hoda" (precomputed female) or "default" (male). An unregistered
                name is a 400, so this fails loudly rather than falling back.
            expected_voice: the voice this deployment is supposed to use. Any
                other value is logged as a warning, since a silently swapped
                voice is the most common cause of "the agent sounds wrong".
            ref_audio: optional reference audio (server path, URL, or data URI).
                When set, the request uses voice=default plus this ref_audio so
                the server clones per request instead of using a named voice.
            ref_text: transcript of ``ref_audio`` when cloning from a reference.
            seed: optional deterministic sampling seed. Without one the server
                is free-running, so utterance length varies between identical
                requests.
            speed: playback-rate multiplier applied locally, below 1.0 for a
                slower delivery. Pitch-preserving (see _WSOLAStretcher); this is
                deliberately not the server's `speed`, which resamples and so
                changes the speaker. 1.0 disables the stretch entirely.
            voice_min_f0 / voice_max_f0: diagnostic pitch band for this speaker
                (female hoda ≈ 135-340 Hz, male clone ≈ 70-180 Hz).
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SERVER_SAMPLE_RATE,
            num_channels=1,
        )
        self._server_url = server_url.rstrip("/")
        # The base URL is commonly copy-pasted from the model card with the /v1
        # already on it; appending the endpoint then yields /v1/v1/audio/speech.
        if self._server_url.endswith("/v1"):
            self._server_url = self._server_url[: -len("/v1")]
            logger.warning(
                "VoxCPM TTS server_url ended in /v1; trimmed to {} because the "
                "endpoint path is appended by this plugin",
                self._server_url,
            )
        self._model = model
        self._voice = voice
        self._ref_audio = ref_audio
        self._ref_text = ref_text
        self._seed = seed
        self._speed = min(MAX_SPEED, max(MIN_SPEED, float(speed)))
        if self._speed != float(speed):
            logger.warning(
                "VoxCPM TTS speed {} is outside the supported {}-{} range and was clamped to {}",
                speed,
                MIN_SPEED,
                MAX_SPEED,
                self._speed,
            )
        if self._speed != 1.0 and np is None:
            logger.error(
                "VoxCPM TTS speed={} requires numpy for the pitch-preserving stretch, "
                "but numpy is unavailable; audio will play at the server's rate",
                self._speed,
            )
            self._speed = 1.0
        self._expected_voice = expected_voice
        self._voice_min_f0 = float(voice_min_f0)
        self._voice_max_f0 = float(voice_max_f0)
        self._session = http_session
        logger.info(
            "VoxCPM TTS plugin initialized server_url={} model={} voice={} ref_audio={} "
            "ref_text_chars={} seed={} speed={} sample_rate={} channels={} "
            "voice_f0_band={:.0f}-{:.0f}",
            self._server_url,
            self._model,
            self._voice,
            "set" if self._ref_audio else None,
            len(self._ref_text or ""),
            self._seed,
            self._speed,
            SERVER_SAMPLE_RATE,
            SERVER_CHANNELS,
            self._voice_min_f0,
            self._voice_max_f0,
        )

    @property
    def name(self) -> str:
        return "VoxCPM-TTS"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
            logger.info("VoxCPM TTS aiohttp session created/reused from LiveKit context")
        return self._session

    def update_options(self, *, voice: str | None = None, model: str | None = None) -> None:
        old_voice = self._voice
        old_model = self._model
        if voice is not None:
            self._voice = voice
        if model is not None:
            self._model = model
        logger.warning(
            "VoxCPM TTS options updated old_voice={} new_voice={} old_model={} new_model={}",
            old_voice,
            self._voice,
            old_model,
            self._model,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "VoxCPMChunkedStream":
        logger.info(
            "VoxCPM TTS synthesize called voice={} model={} text_chars={} text_sha256={} preview={!r}",
            self._voice,
            self._model,
            len(text),
            _text_hash(text),
            _preview(text),
        )
        return VoxCPMChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class VoxCPMChunkedStream(tts.ChunkedStream):
    def __init__(
        self, *, tts: VoxCPMTTSPlugin, input_text: str, conn_options: APIConnectOptions
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = f"voxcpm-{utils.shortuuid()}"
        raw_input = self.input_text
        sanitized_input = normalize_arabic(raw_input)
        payload = {
            "model": self._tts._model,
            "input": sanitized_input,
            "voice": "default" if self._tts._ref_audio else self._tts._voice,
            "language": "Arabic",
            "response_format": "pcm",
            "stream": True,
            "stream_format": "audio",
        }
        if self._tts._ref_audio:
            payload["ref_audio"] = self._tts._ref_audio
        if self._tts._ref_text:
            payload["ref_text"] = self._tts._ref_text
        if self._tts._seed is not None:
            payload["seed"] = self._tts._seed

        # Buffer to keep s16 samples 2-byte aligned across chunk boundaries.
        remainder = b""
        analysis_audio = bytearray()
        analysis_limit = int(F0_ANALYSIS_SECONDS * SERVER_SAMPLE_RATE) * 2
        started_at = time.perf_counter()
        first_audio_at: float | None = None
        chunks = 0
        bytes_received = 0
        bytes_pushed = 0
        max_chunk_bytes = 0
        odd_boundary_events = 0
        endpoint = f"{self._tts._server_url}/v1/audio/speech"

        speed = self._tts._speed
        stretcher = (
            _WSOLAStretcher(sample_rate=SERVER_SAMPLE_RATE, speed=speed)
            if speed != 1.0
            else None
        )

        expected_voice = self._tts._expected_voice
        if (
            expected_voice
            and self._tts._voice.lower() != expected_voice.lower()
            and not self._tts._ref_audio
        ):
            logger.warning(
                "VoxCPM TTS request {} is using voice {} but this deployment expects {}",
                request_id,
                self._tts._voice,
                expected_voice,
            )

        logger.info(
            "VoxCPM TTS request start request_id={} endpoint={} model={} voice={} "
            "raw_chars={} sanitized_chars={} removed_marks={} text_sha256={} "
            "preview={!r} stream={} response_format={} stream_format={} "
            "language={} ref_audio={} ref_text_chars={} seed={} speed={} "
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
            "set" if payload.get("ref_audio") else None,
            len(payload.get("ref_text") or ""),
            payload.get("seed"),
            speed,
            self._tts._voice_min_f0,
            self._tts._voice_max_f0,
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
                headers={"X-Request-ID": request_id},
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=self._conn_options.timeout,
                    sock_read=self._conn_options.timeout,
                ),
            ) as resp:
                logger.info(
                    "VoxCPM TTS response headers request_id={} status={} elapsed={:.3f}s headers={}",
                    request_id,
                    resp.status,
                    time.perf_counter() - started_at,
                    _interesting_headers(resp.headers),
                )
                if resp.status != 200:
                    error_body = await resp.text()
                    # 400 = voice not registered on this server, 404 = unknown
                    # model id. Both mean config drifted from GET /v1/audio/voices
                    # or GET /v1/models, not a transient fault.
                    logger.error(
                        "VoxCPM TTS request failed request_id={} status={} voice={} model={} body={!r}",
                        request_id,
                        resp.status,
                        payload["voice"],
                        payload["model"],
                        _preview(error_body, limit=600),
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
                            "VoxCPM TTS first audio request_id={} first_chunk_bytes={} ttfb={:.3f}s",
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
                    bytes_received += frame_bytes
                    max_chunk_bytes = max(max_chunk_bytes, frame_bytes)
                    # F0 is measured on the server's audio, before the stretch.
                    # WSOLA leaves pitch untouched, so the two agree, and keeping
                    # the pre-stretch signal makes the numbers comparable with
                    # every log line recorded before this knob existed.
                    if len(analysis_audio) < analysis_limit:
                        needed = analysis_limit - len(analysis_audio)
                        analysis_audio.extend(aligned_chunk[:needed])
                    if chunks == 1 or chunks % 25 == 0:
                        logger.info(
                            "VoxCPM TTS stream progress request_id={} chunks={} bytes={} "
                            "audio_seconds={:.3f} elapsed={:.3f}s remainder_bytes={}",
                            request_id,
                            chunks,
                            bytes_received,
                            bytes_received / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2),
                            time.perf_counter() - started_at,
                            len(remainder),
                        )
                    emitted = (
                        stretcher.push(aligned_chunk)
                        if stretcher is not None
                        else aligned_chunk
                    )
                    if emitted:
                        bytes_pushed += len(emitted)
                        output_emitter.push(emitted)

            if stretcher is not None:
                emitted = stretcher.flush()
                if emitted:
                    bytes_pushed += len(emitted)
                    output_emitter.push(emitted)

            output_emitter.flush()
            elapsed = time.perf_counter() - started_at
            f0, rms, peak = _median_f0(bytes(analysis_audio))
            audio_seconds = bytes_received / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2)
            logger.info(
                "VoxCPM TTS request complete request_id={} seed={} chunks={} bytes={} "
                "audio_seconds={:.3f} speed={} emitted_bytes={} emitted_seconds={:.3f} "
                "elapsed={:.3f}s first_audio_latency={} "
                "max_chunk_bytes={} odd_boundary_events={} final_remainder_bytes={} "
                "analysis_seconds={:.3f} median_f0_hz={} rms={} peak={} "
                "retry_count=0 gate_action=none",
                request_id,
                payload.get("seed"),
                chunks,
                bytes_received,
                audio_seconds,
                speed,
                bytes_pushed,
                bytes_pushed / (SERVER_SAMPLE_RATE * SERVER_CHANNELS * 2),
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
                    "VoxCPM TTS diagnostic could not estimate first-audio F0 request_id={}; "
                    "audio was already emitted because this path has no gate",
                    request_id,
                )
            elif not (self._tts._voice_min_f0 <= f0 <= self._tts._voice_max_f0):
                logger.error(
                    "VoxCPM TTS diagnostic out-of-range request_id={} seed={} median_f0_hz={:.1f} "
                    "expected_range={:.0f}-{:.0f}; audio was emitted because this path has "
                    "no gate/retry",
                    request_id,
                    payload.get("seed"),
                    f0,
                    self._tts._voice_min_f0,
                    self._tts._voice_max_f0,
                )
        except aiohttp.ClientError as e:
            logger.exception(
                "VoxCPM TTS network/client failure request_id={} endpoint={} elapsed={:.3f}s",
                request_id,
                endpoint,
                time.perf_counter() - started_at,
            )
            raise APIConnectionError("failed to reach VoxCPM TTS server") from e
