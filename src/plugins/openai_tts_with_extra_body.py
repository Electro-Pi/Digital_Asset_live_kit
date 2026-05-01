"""OpenAI-compatible TTS with extra JSON merged into /audio/speech (OpenAI SDK extra_body)."""

from __future__ import annotations

import base64
import json
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


def _speech_extra_body_kw(extra: dict[str, Any]) -> dict[str, Any]:
    return {"extra_body": extra} if extra else {}


class _AudioChunkedStreamExtra(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts_impl: OpenAITTSBase,
        input_text: str,
        conn_options: APIConnectOptions,
        extra_body: dict[str, Any],
    ) -> None:
        super().__init__(tts=tts_impl, input_text=input_text, conn_options=conn_options)
        self._tts: OpenAITTSBase = tts_impl
        self._opts: _TTSOptions = replace(tts_impl._opts)
        self._extra_body = extra_body

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        oai_stream = self._tts._client.audio.speech.with_streaming_response.create(
            input=self.input_text,
            model=self._opts.model,
            voice=self._opts.voice,
            response_format=self._opts.response_format,  # type: ignore[arg-type]
            speed=self._opts.speed,
            instructions=self._opts.instructions or openai.omit,
            stream_format="audio",
            timeout=httpx.Timeout(30, connect=self._conn_options.timeout),
            **_speech_extra_body_kw(self._extra_body),
        )
        try:
            async with oai_stream as stream:
                output_emitter.initialize(
                    request_id=stream.request_id or "",
                    sample_rate=SAMPLE_RATE,
                    num_channels=NUM_CHANNELS,
                    mime_type=f"audio/{self._opts.response_format}",
                )
                async for data in stream.iter_bytes():
                    output_emitter.push(data)
            output_emitter.flush()
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code, request_id=e.request_id, body=e.body
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class _SSEChunkedStreamExtra(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts_impl: OpenAITTSBase,
        input_text: str,
        conn_options: APIConnectOptions,
        extra_body: dict[str, Any],
    ) -> None:
        super().__init__(tts=tts_impl, input_text=input_text, conn_options=conn_options)
        self._tts: OpenAITTSBase = tts_impl
        self._opts: _TTSOptions = replace(tts_impl._opts)
        self._extra_body = extra_body

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        oai_stream = self._tts._client.audio.speech.with_streaming_response.create(
            input=self.input_text,
            model=self._opts.model,
            voice=self._opts.voice,
            response_format=self._opts.response_format,  # type: ignore[arg-type]
            speed=self._opts.speed,
            instructions=self._opts.instructions or openai.omit,
            stream_format="sse",
            timeout=httpx.Timeout(30, connect=self._conn_options.timeout),
            **_speech_extra_body_kw(self._extra_body),
        )
        try:
            async with oai_stream as stream:
                output_emitter.initialize(
                    request_id=stream.request_id or "",
                    sample_rate=SAMPLE_RATE,
                    num_channels=NUM_CHANNELS,
                    mime_type=f"audio/{self._opts.response_format}",
                )
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
                            output_emitter.push(base64.b64decode(audio_b64))
                    elif event_type == "speech.audio.done":
                        usage = event.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)
                        if input_tokens or output_tokens:
                            self._set_token_usage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
            output_emitter.flush()
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code, request_id=e.request_id, body=e.body
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class OpenAITTSWithSpeechExtraBody(OpenAITTSBase):
    """Same as livekit.plugins.openai.TTS but merges ``speech_extra_body`` into each speech request."""

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
        if self._opts.model in AUDIO_STREAM_MODELS:
            return _AudioChunkedStreamExtra(
                tts_impl=self,
                input_text=text,
                conn_options=conn_options,
                extra_body=extra,
            )
        return _SSEChunkedStreamExtra(
            tts_impl=self,
            input_text=text,
            conn_options=conn_options,
            extra_body=extra,
        )
