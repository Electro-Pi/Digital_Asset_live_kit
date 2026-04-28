from __future__ import annotations

import asyncio
import base64
import collections
import io
import json
import logging
import os
import uuid
import wave
from urllib.parse import urlparse

import aiohttp
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError, AgentSession, llm, plugin, vad
from livekit.agents.llm.utils import build_legacy_openai_schema
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    NOT_GIVEN,
    NotGivenOr,
)

logger = logging.getLogger(__name__)


class Gemma4AudioLLM(llm.LLM):
    @classmethod
    def from_env(cls, *, system_prompt: str | None = None) -> Gemma4AudioLLM:
        return cls(
            base_url=os.getenv("GEMMA4_BASE_URL"),
            api_key=os.getenv("GEMMA4_API_KEY", "needed"),
            model=os.getenv("GEMMA4_MODEL", "gemma4"),
            max_tokens=int(os.getenv("GEMMA4_MAX_TOKENS", "512")),
            request_timeout=float(os.getenv("GEMMA4_TIMEOUT", "60")),
            system_prompt=system_prompt,
        )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str = "gemma4",
        system_prompt: str | None = None,
        max_tokens: int = 512,
        request_timeout: float = 60.0,
        user_audio_placeholder: str = "User provided audio input.",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__()
        self._base_url = (
            base_url
            or os.getenv("GEMMA4_BASE_URL")
            or "https://ranch-shirt-ceremony-rail.trycloudflare.com/v1"
        ).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("GEMMA4_API_KEY", "needed")
        self._model = model
        self._max_tokens = max_tokens
        self._request_timeout = request_timeout
        self._user_audio_placeholder = user_audio_placeholder
        self._session = session
        self._owns_session = session is None
        self._history = llm.ChatContext.empty()
        self._history_lock = asyncio.Lock()

        if system_prompt:
            self._history.add_message(role="system", content=system_prompt)

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        parsed = urlparse(self.chat_completions_url)
        return parsed.netloc or "gemma4"

    @property
    def chat_completions_url(self) -> str:
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return f"{self._base_url}/chat/completions"

    def new_chat_context(self) -> llm.ChatContext:
        return self._history.copy()

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, object]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        extra: dict[str, object] = {}
        if extra_kwargs is not NOT_GIVEN:
            extra.update(extra_kwargs)
        if tool_choice is not NOT_GIVEN:
            extra["tool_choice"] = tool_choice
        if parallel_tool_calls is not NOT_GIVEN:
            extra["parallel_tool_calls"] = parallel_tool_calls

        return _Gemma4AudioLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            extra=extra,
        )

    async def generate_reply_from_audio(
        self,
        frames: list[rtc.AudioFrame],
        *,
        chat_ctx: llm.ChatContext | None = None,
    ) -> str:
        if not frames:
            return ""

        target_ctx = chat_ctx or self._history

        async with self._history_lock:
            user_message = target_ctx.add_message(
                role="user",
                content=[llm.AudioContent(frame=list(frames))],
            )

            try:
                response = await self.chat(chat_ctx=target_ctx).collect()
            except Exception:
                if target_ctx.items and target_ctx.items[-1].id == user_message.id:
                    target_ctx.items.pop()
                raise

            text = response.text.strip()
            if text:
                target_ctx.add_message(role="assistant", content=text)

            return text

    async def publish_reply_from_audio(
        self,
        frames: list[rtc.AudioFrame],
        *,
        participant: rtc.LocalParticipant,
        reliable: bool = True,
        chat_ctx: llm.ChatContext | None = None,
    ) -> str:
        text = await self.generate_reply_from_audio(frames, chat_ctx=chat_ctx)
        if text.strip():
            await participant.publish_data(text.encode("utf-8"), reliable=reliable)
        return text

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def aclose(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def attach_room_audio(
        self,
        *,
        room: rtc.Room,
        session: AgentSession,
        vad_model: vad.VAD,
    ) -> None:
        _Gemma4RoomAudioBridge(self, room=room, session=session, vad_model=vad_model).attach()

    async def _build_messages(self, chat_ctx: llm.ChatContext) -> list[dict[str, object]]:
        items = list(chat_ctx.items)
        latest_audio_message_id: str | None = None
        for item in reversed(items):
            if isinstance(item, llm.ChatMessage) and any(
                isinstance(c, llm.AudioContent) for c in item.content
            ):
                latest_audio_message_id = item.id
                break

        payload_messages: list[dict[str, object]] = []
        for item in items:
            if isinstance(item, llm.ChatMessage):
                payload_messages.append(
                    await self._message_to_provider_payload(
                        item,
                        include_audio=item.id == latest_audio_message_id,
                    )
                )
            elif isinstance(item, llm.FunctionCall):
                payload_messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": item.call_id,
                                "type": "function",
                                "function": {
                                    "name": item.name,
                                    "arguments": item.arguments or "",
                                },
                            }
                        ],
                    }
                )
            elif isinstance(item, llm.FunctionCallOutput):
                payload_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.call_id,
                        "content": item.output or "",
                    }
                )
        return payload_messages

    async def _message_to_provider_payload(
        self,
        message: llm.ChatMessage,
        *,
        include_audio: bool,
    ) -> dict[str, object]:
        text_parts: list[str] = []
        multimodal_parts: list[dict[str, object]] = []

        for content in message.content:
            if isinstance(content, str):
                cleaned = content.strip()
                if cleaned:
                    text_parts.append(cleaned)
            elif isinstance(content, llm.AudioContent):
                transcript = (content.transcript or "").strip()
                if include_audio and content.frame:
                    if transcript:
                        text_parts.append(transcript)
                    b64 = await asyncio.to_thread(self._encode_audio_base64, content.frame)
                    multimodal_parts.append(
                        {
                            "type": "audio_url",
                            "audio_url": {
                                "url": f"data:audio/wav;base64,{b64}",
                            },
                        }
                    )
                else:
                    text_parts.append(transcript or self._user_audio_placeholder)

        if multimodal_parts:
            content: list[dict[str, object]] = []
            content.extend(multimodal_parts)
            if text_parts:
                content.append({"type": "text", "text": "\n".join(text_parts)})
            return {"role": message.role, "content": content}

        return {"role": message.role, "content": "\n".join(text_parts)}

    @staticmethod
    def _encode_audio_base64(frames: list[rtc.AudioFrame]) -> str:
        if not frames:
            return ""

        sample_rate = frames[0].sample_rate
        num_channels = frames[0].num_channels

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(num_channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for frame in frames:
                wav_file.writeframes(frame.data)

        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    @staticmethod
    def _extract_text(response: dict[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""

        message = first_choice.get("message")
        if not isinstance(message, dict):
            return ""

        content = message.get("content")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in {"text", "output_text"} and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts).strip()

        return ""

    @staticmethod
    def _extract_usage(response: dict[str, object]) -> llm.CompletionUsage | None:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None

        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        prompt_cached_tokens = int(usage.get("prompt_cached_tokens", 0) or 0)
        total_tokens = int(
            usage.get("total_tokens", completion_tokens + prompt_tokens) or 0
        )

        return llm.CompletionUsage(
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            prompt_cached_tokens=prompt_cached_tokens,
            total_tokens=total_tokens,
        )


class _Gemma4AudioLLMStream(llm.LLMStream):
    def __init__(
        self,
        llm_v: Gemma4AudioLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
        extra: dict[str, object] | None = None,
    ) -> None:
        super().__init__(llm_v, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._gemma_llm = llm_v
        self._extra = extra or {}

    async def _run(self) -> None:
        messages = await self._gemma_llm._build_messages(self.chat_ctx)
        
        if messages and messages[-1].get("role") == "user":
            last_content = messages[-1].get("content")
            if not last_content or (isinstance(last_content, str) and not last_content.strip()):
                logger.debug("Ignoring empty text trigger from VoicePipelineAgent.")
                return

        payload: dict[str, object] = {
            "model": self._gemma_llm.model,
            "messages": messages,
            "max_tokens": self._gemma_llm._max_tokens,
            "stream": True,
        }

        tool_specs: list[dict[str, object]] = []
        for tool in self._tools or []:
            if llm.is_function_tool(tool) or llm.is_raw_function_tool(tool):
                try:
                    tool_specs.append(build_legacy_openai_schema(tool, internally_tagged=False))
                except Exception as exc:
                    logger.warning("failed to serialize tool %r: %s", tool, exc)
        if tool_specs:
            payload["tools"] = tool_specs
        for key, value in self._extra.items():
            payload[key] = value

        headers = {"Content-Type": "application/json"}
        if self._gemma_llm._api_key:
            headers["Authorization"] = f"Bearer {self._gemma_llm._api_key}"

        session = await self._gemma_llm._ensure_session()
        debug_payload = {**payload, "messages": [
            {**m, "content": (
                [{**p, "audio_url": {"url": "<b64-omitted>"}} if p.get("type") == "audio_url" else p for p in m["content"]]
                if isinstance(m.get("content"), list) else m.get("content")
            )} for m in payload["messages"]
        ]}
        logger.debug("Gemma payload: %s", json.dumps(debug_payload)[:2000])
        request_id: str | None = None
        chunk_id: str | None = None
        usage_obj: llm.CompletionUsage | None = None
        tool_call_acc: dict[int, dict[str, str]] = {}

        try:
            timeout = aiohttp.ClientTimeout(total=self._conn_options.timeout)
            async with session.post(
                self._gemma_llm.chat_completions_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            ) as response:
                request_id = response.headers.get("x-request-id")

                if response.status != 200:
                    body = await response.text()
                    raise APIStatusError(
                        f"Gemma request failed with status {response.status}",
                        status_code=response.status,
                        request_id=request_id,
                        body=_safe_json_loads(body),
                        retryable=response.status == 429 or response.status >= 500,
                    )

                async for raw_line in response.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue

                    if chunk_id is None:
                        chunk_id = str(chunk.get("id") or request_id or uuid.uuid4().hex)

                    delta_text = _extract_delta_text(chunk)
                    if delta_text:
                        self._event_ch.send_nowait(
                            llm.ChatChunk(
                                id=chunk_id,
                                delta=llm.ChoiceDelta(role="assistant", content=delta_text),
                            )
                        )

                    _accumulate_tool_calls(chunk, tool_call_acc)

                    maybe_usage = self._gemma_llm._extract_usage(chunk)
                    if maybe_usage is not None:
                        usage_obj = maybe_usage
        except asyncio.TimeoutError as exc:
            raise APIConnectionError("Gemma request timed out.", retryable=True) from exc
        except aiohttp.ClientError as exc:
            raise APIConnectionError(
                f"Gemma connection error: {exc}",
                retryable=True,
            ) from exc

        if tool_call_acc:
            tool_calls: list[llm.FunctionToolCall] = []
            for idx in sorted(tool_call_acc.keys()):
                entry = tool_call_acc[idx]
                name = entry.get("name") or ""
                if not name:
                    continue
                tool_calls.append(
                    llm.FunctionToolCall(
                        name=name,
                        arguments=entry.get("arguments", "") or "",
                        call_id=entry.get("id") or uuid.uuid4().hex,
                    )
                )
            if tool_calls:
                self._event_ch.send_nowait(
                    llm.ChatChunk(
                        id=chunk_id or uuid.uuid4().hex,
                        delta=llm.ChoiceDelta(role="assistant", tool_calls=tool_calls),
                    )
                )

        if usage_obj is not None:
            self._event_ch.send_nowait(
                llm.ChatChunk(id=chunk_id or uuid.uuid4().hex, usage=usage_obj)
            )


def _extract_delta_text(chunk: dict[str, object]) -> str:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta") or first.get("message")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                if item.get("type") in {"text", "output_text", None}:
                    parts.append(item["text"])
        return "".join(parts)
    return ""


def _accumulate_tool_calls(
    chunk: dict[str, object],
    acc: dict[int, dict[str, str]],
) -> None:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return
    first = choices[0]
    if not isinstance(first, dict):
        return
    delta = first.get("delta") or first.get("message")
    if not isinstance(delta, dict):
        return
    tool_calls = delta.get("tool_calls")
    if not isinstance(tool_calls, list):
        return
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            continue
        idx = tc.get("index")
        if not isinstance(idx, int):
            idx = i
        entry = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if isinstance(tc.get("id"), str) and tc["id"]:
            entry["id"] = tc["id"]
        fn = tc.get("function")
        if isinstance(fn, dict):
            if isinstance(fn.get("name"), str) and fn["name"]:
                entry["name"] = fn["name"]
            args = fn.get("arguments")
            if isinstance(args, str):
                entry["arguments"] += args


def _safe_json_loads(payload: str) -> object:
    try:
        return json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        return {"raw": payload}


class _Gemma4AudioPlugin(plugin.Plugin):
    def __init__(self) -> None:
        super().__init__(
            title="Gemma4 Audio LLM",
            version="0.1.0",
            package="src.plugins.custom_gemma4_audio_plugin",
            logger=logger,
        )


plugin.Plugin.register_plugin(_Gemma4AudioPlugin())


class _Gemma4RoomAudioBridge:
    def __init__(
        self,
        gemma_llm: Gemma4AudioLLM,
        *,
        room: rtc.Room,
        session: AgentSession,
        vad_model: vad.VAD,
    ) -> None:
        self._gemma_llm = gemma_llm
        self._room = room
        self._session = session
        self._vad = vad_model
        self._attached_track_sids: set[str] = set()

    def attach(self) -> None:
        @self._room.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO and isinstance(track, rtc.RemoteAudioTrack):
                self._start_track_processor(track)

        for participant in self._room.remote_participants.values():
            for publication in participant.track_publications.values():
                track = publication.track
                if track and track.kind == rtc.TrackKind.KIND_AUDIO:
                    assert isinstance(track, rtc.RemoteAudioTrack)
                    self._start_track_processor(track)

    def _start_track_processor(self, track: rtc.RemoteAudioTrack) -> None:
        track_sid = getattr(track, "sid", None)
        if track_sid and track_sid in self._attached_track_sids:
            return
        if track_sid:
            self._attached_track_sids.add(track_sid)

        asyncio.create_task(self._process_track(track))

    async def _process_track(self, track: rtc.RemoteAudioTrack) -> None:
        audio_stream = rtc.AudioStream(track)
        vad_stream = self._vad.stream()
        audio_buffer: list[rtc.AudioFrame] = []
        is_speaking = False
        pre_buffer: collections.deque[rtc.AudioFrame] = collections.deque(maxlen=10)

        async def _push_frames() -> None:
            async for event in audio_stream:
                vad_stream.push_frame(event.frame)
                if is_speaking:
                    audio_buffer.append(event.frame)
                else:
                    pre_buffer.append(event.frame)

        async def _read_vad() -> None:
            nonlocal is_speaking, audio_buffer
            async for vad_evt in vad_stream:
                if vad_evt.type == vad.VADEventType.START_OF_SPEECH:
                    is_speaking = True
                    audio_buffer = list(pre_buffer)
                    pre_buffer.clear()
                elif vad_evt.type == vad.VADEventType.END_OF_SPEECH:
                    is_speaking = False
                    if audio_buffer:
                        self._session.generate_reply(
                            user_input=llm.ChatMessage(
                                role="user",
                                content=[llm.AudioContent(frame=list(audio_buffer))],
                            ),
                            input_modality="audio",
                        )
                    audio_buffer = []

        try:
            await asyncio.gather(_push_frames(), _read_vad())
        finally:
            track_sid = getattr(track, "sid", None)
            if track_sid:
                self._attached_track_sids.discard(track_sid)