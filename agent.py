from contextlib import AsyncExitStack
from fastmcp import Client
from dotenv import load_dotenv
from typing import Any
import uuid
import time
from loguru import logger
import json
import re
import asyncio
load_dotenv(".env.local")

from pathlib import Path
import sys

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    function_tool,
    room_io,
    AgentStateChangedEvent,
    MetricsCollectedEvent,
    ToolError,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
)
from src.plugins.semantic_cache import sera_cache
from src.plugins.crm_detector import is_crm_action
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.config.omnivoice_najdi_tts import najdi_omnivoice_speech_extra_body
from src.plugins.openai_stt_with_temperature import STTWithTemperature
from src.plugins.openai_tts_with_extra_body import OpenAITTSWithSpeechExtraBody
from src.prompts.agent_prompt import system_prompt


def clean_backend_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[*#_`~>|]', '', text)
    text = re.sub(r'[-•]', '،', text)
    text = re.sub(r'[\[\]\(\){}<>]', '', text)
    text = re.sub(r'\n+', ' ، ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(،\s*)+', '، ', text)
    return text.strip(' ،')

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
        

    @function_tool()
    async def sera_chat(self, context: RunContext, message: str) -> str:
        try:
            # 1. USE THE DETECTOR FUNCTION
            crm_intent = is_crm_action(message)

            # 2. CHECK CACHE
            if not crm_intent:
                logger.info(f"Classified as Knowledge Query: '{message}'. Checking Cache...")
                cached_answer = sera_cache.get_cached_response(message)
                
                if cached_answer:
                    logger.info("Cache Hit! Sending to LLM to enforce speaking rules.")
                    # بدلاً من التحدث مباشرة، نُرجع النص للموديل مع تذكير صارم بالقواعد
                    enforcement_prompt = (
                        f"{cached_answer}\n\n"
                        "[SYSTEM COMMAND: صُغ هذه الإجابة للمستخدم بلهجة نجدية طبيعية. "
                        "حوّل جميع الأرقام إلى كلمات (مثال: 'ثلاثة'). لا تستخدم أي رموز أو Markdown.]"
                    )
                    return enforcement_prompt

                logger.info("Cache Miss. Proceeding to backend.")
                
                
            
            logger.info(f"Classified as CRM Action: '{message}'. Bypassing Cache.")

            # text_to_say = f"أبشر، خلني أشوف لك {message} اللي تحتاجها. لحظات بس"
            # await context.session.say(
            #     text_to_say,
            #     allow_interruptions=False,
            # )
            
            typing_handle = self.background_audio.play(
                                [
                                    AudioConfig(
                                        BuiltinAudioClip.KEYBOARD_TYPING,
                                        volume=0.15,
                                        probability=0.7,
                                    ),
                                    AudioConfig(
                                        BuiltinAudioClip.KEYBOARD_TYPING2,
                                        volume=0.12,
                                        probability=0.3,
                                    ),
                                ],
                                loop=True,
                            )
            
            start_time = time.time()
            
            result = await self.mcp_client.call_tool(
                name="sera_chat",
                arguments={
                    "message": message,
                    "session_id": self.session_id 
                }
            )
            
            typing_handle.stop()
            
            mcp_duration = time.time() - start_time
            logger.info(f"FastMCP Call took {mcp_duration:.2f} seconds")
            
            # FastMCP response handling
            tool_response = ""
            if hasattr(result, 'content'):
                tool_response = "\n".join(block.text for block in result.content if getattr(block, 'type', '') == "text")
                
                try:
                    data = json.loads(tool_response)
                    tool_response = data.get("answer", tool_response)
                    # زيادة الحد قليلاً لعدم قطع الكلمات
                    tool_response = tool_response[:400] + "..." if len(tool_response) > 400 else tool_response
                except:
                    pass
            
            clean_response = clean_backend_text(tool_response)
            logger.info(f"SERA Backend Response (Cleaned): {clean_response}")

            # 4. SAVE TO CACHE (احفظ النسخة النظيفة في الكاش)
            if not crm_intent and clean_response and "Error" not in clean_response:
                sera_cache.add_to_cache(query=message, response=clean_response)

            # 5. إجبار الموديل على تطبيق القواعد
            # إلحاق هذا الأمر السري بنهاية النص يُجبر الموديل على عدم الهلوسة بالتنسيقات
            final_prompt = (
                f"{clean_response}\n\n"
                "[SYSTEM COMMAND: Summarize the above naturally. Speak exclusively in Saudi Najdi. "
                "Write ALL numbers as full Arabic words (e.g., 'مائة'). "
                "DO NOT use markdown, bullet points, asterisks, or any symbols. Use only commas and periods.]"
            )
            return final_prompt
                
        except Exception as e:
            error_msg = f"Error connecting to SERA backend: {str(e)}"
            logger.error(error_msg)
            return error_msg

server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    exit_stack = AsyncExitStack()

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
                model="/home/ai/vllm-sera/models/cohere-transcribe",
                api_key="no-key-needed",
                language="ar",
                temperature=0.0,
            ),
            llm=openai.LLM(
                base_url="http://8.213.50.155:8001/v1",
                model="gemma4",
                api_key="needed",
            ),
            # tts = openai.TTS(
            #     base_url="http://211.21.50.85:57027/v1",
            #     model="omnivoice-najdi-1.0",
            #     api_key="no-key-needed",
            # ),
                
            tts=OpenAITTSWithSpeechExtraBody(
                speech_extra_body=najdi_omnivoice_speech_extra_body(),
                base_url="http://211.21.50.85:57028/v1",
                api_key="no-key-needed",
                response_format="pcm",
                speed=1.1,
            ),
            # tts = cartesia.TTS(),

            vad=silero.VAD.load(),
            turn_detection=MultilingualModel(),
        )
        
        office_noise_path = Path("data") / "office_low.wav"
        
        background_audio = BackgroundAudioPlayer(
            
            ambient_sound= str(office_noise_path)
            # ambient_sound=AudioConfig(
            #     BuiltinAudioClip.OFFICE_AMBIENCE,
            #     volume=0.8,
            # ),
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
        await session.start(
            room=ctx.room,
            # Pass the freshly generated string here:
            agent=Assistant(
                    mcp_client=mcp_client,
                    session_id=call_session_id,
                    background_audio=background_audio,
                ), 
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
                "ابدأ بالترحيب بالعميل كمساعد ذكي من سيرا، "
                "واسأله بشكل طبيعي: كيف يمكنني مساعدتك اليوم؟"
            )
        )
        
        
    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        await background_audio.aclose()
        await exit_stack.aclose()


if __name__ == "__main__":
    agents.cli.run_app(server)