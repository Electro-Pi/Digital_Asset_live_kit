"""Speaker-verification gateway for the MOSS / sglang-omni TTS server.

The finetuned model (Rabe3/saudi-tts-4) occasionally rolls a wrong speaker
(male or squeaky) in the first generated frames and keeps it for the whole
utterance — roughly 10-15% of requests, regardless of sampling parameters.
The speaker is locked in once the first frames are out, so the roll can be
detected early and cheaply from pitch.

This gateway sits in front of sglang (default: listens on :8089, upstream
:8088) and, for every /v1/audio/speech request:

1. Injects stable default sampling params if the client sent none
   (sglang's own default audio temperature of 1.7 destabilizes the speaker).
2. Buffers the first ~0.6s of generated audio and measures its median F0.
3. If the pitch lands outside the expected range for the registered voice,
   aborts the generation and silently retries (up to MAX_ATTEMPTS in total).
4. Streams the verified audio to the client (both streaming-pcm and wav
   responses are supported; every other endpoint is proxied through).

Run:  uv run python -m src.tts_gateway
"""

from __future__ import annotations

import asyncio
import io
import json
import wave

import aiohttp
import numpy as np
from aiohttp import web
from loguru import logger

UPSTREAM_URL = "http://localhost:8088"
LISTEN_PORT = 8089
SAMPLE_RATE = 48000

# nouf speaks at ~195-245 Hz (median ~210); wrong rolls land well outside
# (male ~90-140 Hz, squeaky ~300-440 Hz).
GATE_MIN_F0 = 150.0
GATE_MAX_F0 = 280.0
GATE_ANALYSIS_SECONDS = 0.6  # decide as soon as we have this much audio
GATE_MAX_WAIT_SECONDS = 1.5  # if still not enough voiced audio, let it pass
GATE_MIN_VOICED_WINDOWS = 4
MAX_ATTEMPTS = 3

# Applied when the client didn't send them; sglang's hard-coded defaults
# (audio temperature 1.7) make the cloned speaker drift badly.
DEFAULT_SAMPLING = {"temperature": 0.8, "top_p": 0.95, "top_k": 50}

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
    "host",
}


def median_f0(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float | None:
    """Median F0 over voiced 80ms windows, or None if too little voiced audio."""
    win = int(0.04 * sample_rate)
    if len(samples) < 2 * win:
        return None
    level = np.abs(samples).mean()
    if level == 0:
        return None
    f0s = []
    for i in range(0, len(samples) - 2 * win, win):
        seg = samples[i : i + 2 * win].astype(np.float64)
        if np.abs(seg).mean() < 0.5 * level:  # skip silence/breath
            continue
        seg -= seg.mean()
        ac = np.correlate(seg, seg, "full")[len(seg) - 1 :]
        lo, hi = int(sample_rate / 500), int(sample_rate / 60)
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.4 * ac[0]:  # voiced: strong periodicity
            f0s.append(sample_rate / lag)
    if len(f0s) < GATE_MIN_VOICED_WINDOWS:
        return None
    return float(np.median(f0s))


def _gate_verdict(samples: np.ndarray) -> tuple[bool, float | None]:
    f0 = median_f0(samples)
    if f0 is None:
        return True, None  # can't judge -> let it through
    return GATE_MIN_F0 <= f0 <= GATE_MAX_F0, f0


class Gateway:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ #
    # /v1/audio/speech with speaker gate
    # ------------------------------------------------------------------ #

    async def handle_speech(self, request: web.Request) -> web.StreamResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        for key, value in DEFAULT_SAMPLING.items():
            payload.setdefault(key, value)

        is_pcm_stream = bool(payload.get("stream")) and (
            payload.get("response_format") == "pcm"
        )
        if is_pcm_stream:
            return await self._speech_pcm_stream(request, payload)
        return await self._speech_buffered(request, payload)

    async def _speech_pcm_stream(
        self, request: web.Request, payload: dict
    ) -> web.StreamResponse:
        """Gate the head of a streaming pcm response, then pipe it through."""
        session = await self.session()
        analysis_bytes = int(GATE_ANALYSIS_SECONDS * SAMPLE_RATE) * 2
        max_wait_bytes = int(GATE_MAX_WAIT_SECONDS * SAMPLE_RATE) * 2
        last_error: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            upstream = await session.post(
                f"{UPSTREAM_URL}/v1/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120),
            )
            if upstream.status != 200:
                body = await upstream.read()
                return web.Response(
                    body=body, status=upstream.status, content_type="application/json"
                )

            head = bytearray()
            verdict: bool | None = None
            f0: float | None = None
            try:
                async for chunk in upstream.content.iter_chunked(8192):
                    head.extend(chunk)
                    if verdict is None and len(head) >= analysis_bytes:
                        ok, f0 = _gate_verdict(
                            np.frombuffer(bytes(head[: len(head) // 2 * 2]), dtype=np.int16)
                        )
                        if ok or len(head) >= max_wait_bytes:
                            verdict = ok
                    if verdict is True or (
                        verdict is False and attempt == MAX_ATTEMPTS
                    ):
                        break
                    if verdict is False:
                        break
            except aiohttp.ClientError as exc:
                upstream.close()
                last_error = str(exc)
                continue

            if verdict is False and attempt < MAX_ATTEMPTS:
                logger.warning(
                    "speaker gate: attempt {} rejected (F0={:.0f} Hz), retrying",
                    attempt,
                    f0 or 0.0,
                )
                upstream.close()  # aborts the upstream generation
                continue

            if verdict is False:
                logger.error(
                    "speaker gate: all {} attempts rejected, streaming last (F0={:.0f} Hz)",
                    MAX_ATTEMPTS,
                    f0 or 0.0,
                )

            response = web.StreamResponse(
                status=200, headers={"Content-Type": "audio/pcm"}
            )
            await response.prepare(request)
            await response.write(bytes(head))
            try:
                async for chunk in upstream.content.iter_chunked(8192):
                    await response.write(chunk)
            except aiohttp.ClientError:
                pass
            finally:
                upstream.release()
            await response.write_eof()
            return response

        return web.json_response(
            {"error": f"upstream failed: {last_error}"}, status=502
        )

    async def _speech_buffered(
        self, request: web.Request, payload: dict
    ) -> web.StreamResponse:
        """Gate a non-streaming (e.g. wav) response; retry on a bad roll."""
        session = await self.session()
        body: bytes = b""
        status = 500
        content_type = "application/octet-stream"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            async with session.post(
                f"{UPSTREAM_URL}/v1/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300),
            ) as upstream:
                status = upstream.status
                content_type = upstream.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                body = await upstream.read()
            if status != 200:
                break

            samples = self._decode_head(body, payload.get("response_format", "mp3"))
            if samples is None:
                break  # unknown format -> pass through unverified
            ok, f0 = _gate_verdict(samples)
            if ok:
                break
            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    "speaker gate: attempt {} rejected (F0={:.0f} Hz), retrying",
                    attempt,
                    f0 or 0.0,
                )
            else:
                logger.error(
                    "speaker gate: all {} attempts rejected, returning last (F0={:.0f} Hz)",
                    MAX_ATTEMPTS,
                    f0 or 0.0,
                )

        return web.Response(body=body, status=status, content_type=content_type)

    @staticmethod
    def _decode_head(body: bytes, response_format: str) -> np.ndarray | None:
        """Extract mono int16 samples from a buffered response for gating."""
        if response_format == "pcm":
            return np.frombuffer(body[: len(body) // 2 * 2], dtype=np.int16)
        if response_format == "wav":
            try:
                w = wave.open(io.BytesIO(body))
                frames = w.readframes(w.getnframes())
                data = np.frombuffer(frames, dtype=np.int16)
                if w.getnchannels() == 2:
                    data = data.reshape(-1, 2).mean(axis=1).astype(np.int16)
                return data
            except (wave.Error, EOFError):
                return None
        return None

    # ------------------------------------------------------------------ #
    # transparent proxy for every other endpoint
    # ------------------------------------------------------------------ #

    async def handle_proxy(self, request: web.Request) -> web.StreamResponse:
        session = await self.session()
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        body = await request.read()
        async with session.request(
            request.method,
            f"{UPSTREAM_URL}{request.rel_url}",
            headers=headers,
            data=body if body else None,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300),
        ) as upstream:
            resp_headers = {
                k: v
                for k, v in upstream.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            response = web.StreamResponse(status=upstream.status, headers=resp_headers)
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(8192):
                await response.write(chunk)
            await response.write_eof()
            return response


def main() -> None:
    gateway = Gateway()
    app = web.Application()
    app.router.add_post("/v1/audio/speech", gateway.handle_speech)
    app.router.add_route("*", "/{tail:.*}", gateway.handle_proxy)

    async def on_cleanup(_: web.Application) -> None:
        await gateway.close()

    app.on_cleanup.append(on_cleanup)
    logger.info("TTS gateway listening on :{} -> {}", LISTEN_PORT, UPSTREAM_URL)
    web.run_app(app, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
