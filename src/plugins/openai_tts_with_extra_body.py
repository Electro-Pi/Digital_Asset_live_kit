"""OpenAI-compatible TTS with extra JSON merged into /audio/speech."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import replace
from typing import Any

import httpx
import openai
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins.openai.tts import (
    AUDIO_STREAM_MODELS,
    NUM_CHANNELS,
    SAMPLE_RATE,
    TTS as OpenAITTSBase,
    _TTSOptions,
)

# 20 ms PCM frame — must match what LiveKit's AudioByteStream expects.
# At 24 kHz, 16-bit, mono: 24000 * 0.020 * 2 * 1 = 960 bytes per frame.
_BYTES_PER_SAMPLE = 2
_FRAME_MS = 20
_FRAME_BYTES = int(SAMPLE_RATE * _FRAME_MS / 1000) * _BYTES_PER_SAMPLE * NUM_CHANNELS


def _speech_extra_body_kw(extra: dict[str, Any]) -> dict[str, Any]:
    return {"extra_body": extra} if extra else {}


def _request_id(raw: str | None) -> str:
    """OmniVoice doesn't return x-request-id — generate one so LiveKit stays quiet."""
    return raw or f"omnivoice-{uuid.uuid4().hex[:12]}"


def _push_pcm_aligned(
    buf: bytearray, chunk: bytes, emitter: tts.AudioEmitter
) -> bytearray:
    """Accumulate chunk into buf, push only complete frames, return leftover."""
    buf.extend(chunk)
    while len(buf) >= _FRAME_BYTES:
        emitter.push(bytes(buf[:_FRAME_BYTES]))
        del buf[:_FRAME_BYTES]
    return buf


class _AudioChunkedStreamExtra(tts.ChunkedStream):
    """Raw-byte (audio) streaming path — used for models in AUDIO_STREAM_MODELS."""

    def __init__(
        self,
        *,
        tts_impl: OpenAITTSBase,
        input_text: str,
        conn_options: APIConnectOptions,
        extra_body: dict[str, Any],
        is_pcm: bool,
    ) -> None:
        super().__init__(tts=tts_impl, input_text=input_text, conn_options=conn_options)
        self._tts: OpenAITTSBase = tts_impl
        self._opts: _TTSOptions = replace(tts_impl._opts)
        self._extra_body = extra_body
        self._is_pcm = is_pcm

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        oai_stream = self._tts._client.audio.speech.with_streaming_response.create(
            input=self.input_text,
            model=self._opts.model,
            voice=self._opts.voice,
            response_format=self._opts.response_format,
            speed=self._opts.speed,
            instructions=self._opts.instructions or openai.omit,
            stream_format="audio",
            # Generous timeout: OmniVoice is a remote diffusion model.
            # connect: fail fast if the server is down.
            # read/write/pool: allow up to 2 min for long utterances.
            timeout=httpx.Timeout(120.0, connect=10.0),
            **_speech_extra_body_kw(self._extra_body),
        )
        try:
            async with oai_stream as stream:
                output_emitter.initialize(
                    request_id=_request_id(stream.request_id),
                    sample_rate=SAMPLE_RATE,
                    num_channels=NUM_CHANNELS,
                    mime_type=f"audio/{self._opts.response_format}",
                )
                if self._is_pcm:
                    buf = bytearray()
                    async for data in stream.iter_bytes():
                        buf = _push_pcm_aligned(buf, data, output_emitter)
                    # flush any sub-frame tail (a few ms at most)
                    if buf:
                        output_emitter.push(bytes(buf))
                else:
                    async for data in stream.iter_bytes():
                        output_emitter.push(data)
            output_emitter.flush()
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code,
                request_id=e.request_id, body=e.body,
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class _SSEChunkedStreamExtra(tts.ChunkedStream):
    """SSE streaming path — OmniVoice returns speech.audio.delta events with base64 PCM."""

    def __init__(
        self,
        *,
        tts_impl: OpenAITTSBase,
        input_text: str,
        conn_options: APIConnectOptions,
        extra_body: dict[str, Any],
        is_pcm: bool,
    ) -> None:
        super().__init__(tts=tts_impl, input_text=input_text, conn_options=conn_options)
        self._tts: OpenAITTSBase = tts_impl
        self._opts: _TTSOptions = replace(tts_impl._opts)
        self._extra_body = extra_body
        self._is_pcm = is_pcm

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        oai_stream = self._tts._client.audio.speech.with_streaming_response.create(
            input=self.input_text,
            model=self._opts.model,
            voice=self._opts.voice,
            response_format=self._opts.response_format,
            speed=self._opts.speed,
            instructions=self._opts.instructions or openai.omit,
            stream_format="sse",
            timeout=httpx.Timeout(120.0, connect=10.0),
            **_speech_extra_body_kw(self._extra_body),
        )
        try:
            async with oai_stream as stream:
                # Initialize immediately with a generated id — don't wait for the
                # first SSE event, which could be delayed by diffusion warm-up.
                output_emitter.initialize(
                    request_id=_request_id(stream.request_id),
                    sample_rate=SAMPLE_RATE,
                    num_channels=NUM_CHANNELS,
                    mime_type=f"audio/{self._opts.response_format}",
                )

                buf = bytearray()

                async for line in stream.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    if event_type == "speech.audio.delta":
                        audio_b64 = event.get("delta", "") or event.get("audio", "")
                        if audio_b64:
                            chunk = base64.b64decode(audio_b64)
                            if self._is_pcm:
                                # Push only aligned frames to avoid tail-drop warnings.
                                buf = _push_pcm_aligned(buf, chunk, output_emitter)
                            else:
                                output_emitter.push(chunk)

                    elif event_type == "speech.audio.done":
                        usage = event.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)
                        if input_tokens or output_tokens:
                            self._set_token_usage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )

                # Push any sub-frame remainder accumulated during the loop.
                if self._is_pcm and buf:
                    output_emitter.push(bytes(buf))

            output_emitter.flush()

        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code,
                request_id=e.request_id, body=e.body,
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class OpenAITTSWithSpeechExtraBody(OpenAITTSBase):
    """Same as livekit.plugins.openai.TTS but merges ``speech_extra_body`` into each request."""

    def __init__(
        self,
        *,
        speech_extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._speech_extra_body: dict[str, Any] = dict(speech_extra_body) if speech_extra_body else {}
        super().__init__(**kwargs)

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        extra = self._speech_extra_body
        is_pcm = getattr(self._opts, "response_format", "") == "pcm"

        if self._opts.model in AUDIO_STREAM_MODELS:
            return _AudioChunkedStreamExtra(
                tts_impl=self,
                input_text=text,
                conn_options=conn_options,
                extra_body=extra,
                is_pcm=is_pcm,
            )
        return _SSEChunkedStreamExtra(
            tts_impl=self,
            input_text=text,
            conn_options=conn_options,
            extra_body=extra,
            is_pcm=is_pcm,
        )