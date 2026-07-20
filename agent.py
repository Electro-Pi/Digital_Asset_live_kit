from contextlib import AsyncExitStack
from fastmcp import Client
from dotenv import load_dotenv
from typing import Any, AsyncIterable
import uuid
import time
from loguru import logger
import json
import re
import asyncio
load_dotenv(".env.local")

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ModelSettings,
    RunContext,
    StopResponse,
    function_tool,
    room_io,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    MetricsCollectedEvent,
    ToolError,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.turn import TurnHandlingOptions
from src.plugins.semantic_cache import sera_cache
from src.plugins.crm_detector import is_crm_action, is_knowledge_query
from livekit.plugins import noise_cancellation, silero, openai, groq, cartesia

from livekit.agents.tts import StreamAdapter
from src.config.omnivoice_najdi_tts import najdi_omnivoice_speech_extra_body
from src.plugins.openai_stt_with_temperature import STTWithTemperature
from src.plugins.openai_tts_with_extra_body import OpenAITTSWithSpeechExtraBody
from src.prompts.agent_prompt import system_prompt
from src.plugins.higgs_tts import HiggsTTS
from src.utils.user_text import extract_user_message
from src.utils.speech_format import (
    format_stream_for_speech,
    is_complete_voice_response,
    is_detail_followup,
    prepare_voice_reply,
)

class Assistant(Agent):

    def __init__(
        self,
        mcp_client,
        session_id: str,
        background_audio: BackgroundAudioPlayer,
    ) -> None:

        super().__init__(
            instructions=system_prompt,
        )

        self.mcp_client = mcp_client
        self.session_id = session_id
        self.background_audio = background_audio
        self._last_user_message = ""

    def _remember_user_message(self, message: str) -> str:
        cleaned = extract_user_message(message)
        if cleaned:
            self._last_user_message = cleaned
        return cleaned

    def _latest_user_message(self, session: AgentSession) -> str:
        if self._last_user_message:
            return self._last_user_message

        history = getattr(session, "history", None)
        if history is None:
            return ""

        for item in reversed(history.items):
            if isinstance(item, ChatMessage) and item.role == "user":
                cleaned = extract_user_message(item.text_content or "")
                if cleaned:
                    return cleaned
        return ""

    def _unwrap_tts(self):
        activity = self._get_activity_or_raise()
        wrapped = activity.tts
        while isinstance(wrapped, StreamAdapter):
            wrapped = wrapped._wrapped_tts
        return wrapped

    async def _resolve_knowledge_message(self, message: str) -> str:
        """Fetch answer from cache or SERA backend; return full cleaned text."""
        message = extract_user_message(message)
        if not message:
            return ""

        if not is_detail_followup(message):
            cached_answer = sera_cache.get_cached_response(message)
            if cached_answer:
                cleaned = prepare_voice_reply(cached_answer)
                if is_complete_voice_response(cleaned):
                    logger.info(f"Cache hit: returning {len(cleaned)} chars")
                    return cleaned
                logger.warning(
                    f"Incomplete cache payload ({len(cleaned)} chars) — re-fetching backend."
                )

        typing_handle = self.background_audio.play(
            [
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.15, probability=0.7),
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.12, probability=0.3),
            ],
            loop=True,
        )
        start_time = time.time()
        try:
            result = await self.mcp_client.call_tool(
                name="sera_chat",
                arguments={"message": message, "session_id": self.session_id},
            )
        finally:
            typing_handle.stop()

        logger.info(f"FastMCP Call took {time.time() - start_time:.2f} seconds")

        tool_response = ""
        if hasattr(result, "content"):
            tool_response = "\n".join(
                block.text for block in result.content if getattr(block, "type", "") == "text"
            )
            try:
                data = json.loads(tool_response)
                tool_response = data.get("answer", tool_response)
            except Exception:
                pass

        clean_response = prepare_voice_reply(tool_response)
        logger.info(
            f"SERA Backend Response (Cleaned, {len(clean_response)} chars): "
            f"{clean_response[:120]}...{clean_response[-80:] if len(clean_response) > 200 else ''}"
        )

        if clean_response and "Error" not in clean_response and is_complete_voice_response(clean_response):
            sera_cache.add_to_cache(query=message, response=clean_response)
        elif clean_response:
            logger.warning("Backend response incomplete — not caching.")

        return clean_response
        
    def trim_history(self, session: AgentSession, max_turns: int = 5):
        logger.info(f"Current Chat Context Length: {len(session.chat_ctx.messages)} messages")
        messages = session.chat_ctx.messages
        if len(messages) > (max_turns * 2):
            # Keep the system prompt (index 0) and the last max_turns*2 messages
            session.chat_ctx.messages = [messages[0]] + messages[-(max_turns * 2):]

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Route knowledge queries to backend/cache — skip LLM hallucinations."""
        message = self._remember_user_message(new_message.text_content)
        if not message or is_crm_action(message):
            return

        if not is_knowledge_query(message):
            return

        spoken = await self._resolve_knowledge_message(message)
        if not spoken:
            return

        logger.info(
            f"Knowledge fast-path for '{message[:60]}' — speaking {len(spoken)} chars, skipping LLM."
        )
        await self.session.interrupt()
        self.session.say(spoken, allow_interruptions=True)
        raise StopResponse()
    
    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        """One TTS call for the full cleaned text — no sentence splitting."""
        activity = self._get_activity_or_raise()
        base_tts = self._unwrap_tts()
        conn_options = activity.session.conn_options.tts_conn_options

        buffer = ""
        async for chunk in format_stream_for_speech(text):
            buffer += chunk

        if not buffer:
            return

        async with base_tts.synthesize(buffer, conn_options=conn_options) as tts_stream:
            async for audio in tts_stream:
                yield audio.frame

    @function_tool()
    async def sera_chat(self, context: RunContext, message: str) -> str:
        try:
            latest_user = self._latest_user_message(context.session)
            if latest_user:
                message = latest_user
            else:
                message = extract_user_message(message)

            crm_intent = is_crm_action(message)
            logger.info(f"sera_chat query (user turn): '{message}'")

            if not crm_intent:
                logger.info(f"Classified as Knowledge Query: '{message}'.")
                clean_response = await self._resolve_knowledge_message(message)
                if clean_response:
                    logger.info(f"Speaking backend answer directly ({len(clean_response)} chars).")
                    context.session.say(clean_response, allow_interruptions=True)
                raise StopResponse()

            logger.info(f"Classified as CRM Action: '{message}'. Bypassing Cache.")
            typing_handle = self.background_audio.play(
                [
                    AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.15, probability=0.7),
                    AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.12, probability=0.3),
                ],
                loop=True,
            )
            start_time = time.time()
            try:
                result = await self.mcp_client.call_tool(
                    name="sera_chat",
                    arguments={"message": message, "session_id": self.session_id},
                )
            finally:
                typing_handle.stop()

            logger.info(f"FastMCP Call took {time.time() - start_time:.2f} seconds")

            tool_response = ""
            if hasattr(result, "content"):
                tool_response = "\n".join(
                    block.text for block in result.content if getattr(block, "type", "") == "text"
                )
                try:
                    data = json.loads(tool_response)
                    tool_response = data.get("answer", tool_response)
                except Exception:
                    pass

            clean_response = prepare_voice_reply(tool_response)
            logger.info(f"SERA CRM Response (Cleaned): {clean_response[:200]}...")
            return clean_response or "عذراً، ما لقيت هالمعلومة في النظام حالياً."

        except StopResponse:
            raise
        except Exception as e:
            error_msg = f"Error connecting to SERA backend: {str(e)}"
            logger.error(error_msg)
            return error_msg

server = AgentServer(
    num_idle_processes=1,
    initialize_process_timeout=120.0,
    job_memory_warn_mb=3000,
)


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    exit_stack = AsyncExitStack()
    background_audio = None

    try:
        # 1. INITIALIZE REMOTE FASTMCP CONNECTION
        url = "http://8.213.50.155:8081/mcp"
        logger.info(f"Connecting to remote FastMCP server at {url}...")
        
        # The FastMCP Client abstracts all the messy SSE and initialization logic!
        mcp_client = await exit_stack.enter_async_context(Client(url))
        logger.info("Remote FastMCP connection established successfully.")

        session = AgentSession(
            stt=STTWithTemperature(
                base_url="http://8.213.50.155:8002/v1",
                model="qwen3-asr",
                api_key="no-key-needed",
                language="ar",
                temperature=0.0,
            ),
            llm=openai.LLM(
                base_url="http://176.124.69.198:8000/v1",
                model="ibm-granite/granite-4.1-8b-fp8",
                api_key="needed",
                temperature=0.1,
                max_completion_tokens=512,
            ),

            # tts = openai.TTS(
            #     base_url="http://211.21.50.85:57027/v1",
            #     model="omnivoice-najdi-1.0",
            #     api_key="no-key-needed",
            # ),
            # tts = HiggsTTS(
            #     base_url="http://38.108.83.78:60010/v1",
            #     model="bosonai/higgs-audio-v3-tts-4b",
            #     reference_audio_path="data/sera_reference.wav",
            #     reference_text="هلا والله، أنا سيرا، مساعدتك الذكية من الهيئة السعودية لتنظيم الكهرباء.",
            # ),

            tts=OpenAITTSWithSpeechExtraBody(
                base_url="http://176.124.69.198:8100",
                model="omnivoice",
                voice="najdi",
                speech_extra_body=najdi_omnivoice_speech_extra_body(),
                response_format="wav",
            ),





            vad=silero.VAD.load(),
            turn_handling=TurnHandlingOptions(
                turn_detection="vad",
                endpointing={"min_delay": 0.35, "max_delay": 2.0},
                preemptive_generation={"enabled": False},
            ),
            tts_text_transforms=["filter_markdown", "filter_emoji"],
        )

        background_audio = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.25),
            stream_timeout_ms=1000,
        )

        last_eou_metrics = None
        collected_metrics = []

        @session.on("metrics_collected")
        def _on_metrics_collected(event: MetricsCollectedEvent):
            nonlocal last_eou_metrics
            if getattr(event.metrics, "type", None) == "eou_metrics":
                last_eou_metrics = event.metrics
            collected_metrics.append(event.metrics)

        # 2. UPDATE SHUTDOWN CALLBACK 
        async def log_usage_and_cleanup():
            logger.info("Session ended.")
            logger.info(f"Total metrics events collected: {len(collected_metrics)}")
            logger.info("Closing FastMCP connection...")
            await exit_stack.aclose()

        ctx.add_shutdown_callback(log_usage_and_cleanup)

        @session.on("agent_state_changed")
        def _on_agent_state_changed(event: AgentStateChangedEvent):
            logger.info(f"Agent state changed: {event.new_state}")
            if event.new_state == "speaking" and last_eou_metrics:
                try:
                    elapsed_time = time.time() - last_eou_metrics.timestamp
                    logger.info(f"Time since last EOU: {elapsed_time:.2f} seconds")
                except Exception as e:
                    logger.warning(f"Could not calculate EOU latency: {e}")

        # Generate a completely unique, pure Python string for this phone call
        call_session_id = str(uuid.uuid4())
        
        

        # 3. PASS THE CLIENT AND SESSION ID TO THE ASSISTANT
        assistant = Assistant(
            mcp_client=mcp_client,
            session_id=call_session_id,
            background_audio=background_audio,
        )

        @session.on("conversation_item_added")
        def _on_conversation_item(event: ConversationItemAddedEvent):
            if isinstance(event.item, ChatMessage) and event.item.role == "user":
                assistant._remember_user_message(event.item.text_content or "")

        await session.start(
            room=ctx.room,
            agent=assistant,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda params:
                        noise_cancellation.BVCTelephony()
                        if params.participant.kind
                        == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                        else noise_cancellation.BVC()
                ),
            ),
        )
        
        
        await background_audio.start(
            room=ctx.room,
            agent_session=session,
        )

        await session.generate_reply(
            instructions=(
                "رحّب بجملة واحدة قصيرة بلهجة نجدية، واسأل كيف تقدر تساعده. "
                "لا تستخدم أي رموز أو قوائم أو أرقام لاتينية."
            ),
            tool_choice="none",
        )
        
        
    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        if background_audio:
            await background_audio.aclose()
        await exit_stack.aclose()


if __name__ == "__main__":
    agents.cli.run_app(server)