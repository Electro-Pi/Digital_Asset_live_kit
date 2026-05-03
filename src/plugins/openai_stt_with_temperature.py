"""livekit.plugins.openai.STT does not expose ``temperature``; this subclass forwards it."""

from __future__ import annotations

from typing import Any

import httpx
import openai
from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    LanguageCode,
    stt,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given
from livekit.plugins.openai import STT as OpenAISTT
from openai.types.audio import TranscriptionVerbose


class STTWithTemperature(OpenAISTT):
    """
    Same as ``livekit.plugins.openai.STT`` (non-realtime path) but passes ``temperature``
    to ``client.audio.transcriptions.create`` when set.
    """

    def __init__(self, *, temperature: float | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._temperature = temperature

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        try:
            if is_given(language):
                self._opts.language = LanguageCode(language)
            data = rtc.combine_audio_frames(buffer).to_wav_bytes()
            prompt = self._opts.prompt if is_given(self._opts.prompt) else openai.omit

            response_format = "json"
            if self._opts.model == "whisper-1":
                response_format = "verbose_json"

            create_kwargs: dict[str, Any] = dict(
                file=(
                    "file.wav",
                    data,
                    "audio/wav",
                ),
                model=self._opts.model,  # type: ignore[arg-type]
                language=self._opts.language.language if self._opts.language else "",
                prompt=prompt,
                response_format=response_format,
                timeout=httpx.Timeout(30, connect=conn_options.timeout),
            )
            if self._temperature is not None:
                create_kwargs["temperature"] = self._temperature

            resp = await self._client.audio.transcriptions.create(**create_kwargs)

            sd = stt.SpeechData(text=resp.text, language=self._opts.language)
            if isinstance(resp, TranscriptionVerbose) and resp.language:
                sd.language = LanguageCode(resp.language)

            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[sd],
            )

        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code, request_id=e.request_id, body=e.body
            ) from None
        except Exception as e:
            raise APIConnectionError() from e
